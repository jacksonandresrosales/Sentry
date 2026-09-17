"""Revisión manual y reevaluación automática con SQLite y audios sintéticos."""
from __future__ import annotations

from datetime import date, timedelta
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

import openpyxl

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from app.database import Database
from app.services.analytics import load_report
from app.services.audio_analysis import keyword_signature
from app.services.report_export import export_audit_excel
from app.ui.views.main_window import SentryWindow, call_record_from_row, install_ui_font


TRANSCRIPT = {
    "text": "El cliente solicita ayuda y menciona un fraude y cancelar el servicio.",
    "segments": [
        {"second": 0, "speaker": "Asesor", "text": "Buenos días, le atiende el asesor."},
        {"second": 1, "speaker": "Cliente", "text": "Es un fraude; quiero cancelar el servicio."},
    ],
    "speaker_count": 2,
}
NORMAL = {
    "category": "NORMAL", "summary": "Conversación sintética sin denuncia automática.",
    "sentiment": "NEUTRAL", "risk": "BAJO", "validated_keywords": [], "hits": [],
}
ALERT = {
    **NORMAL, "category": "ALERTA", "summary": "Denuncia sintética por fraude.",
    "risk": "ALTO", "validated_keywords": ["fraude"],
    "hits": [{"keyword": "fraude", "speaker": "Cliente", "second": 1,
              "snippet": "Es un fraude", "validated": True}],
}


class ReviewReanalysisUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        install_ui_font(cls.app)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name).resolve()
        self.audio_folder = self.folder / "synthetic-audio"
        self.audio_folder.mkdir()
        self.database_path = self.folder / "audit.db"
        database = Database(self.database_path)
        database.save_settings({"keywords": "denuncia", "audio_directory": str(self.audio_folder)})
        self.window = SentryWindow(self.database_path)
        self.addCleanup(self.temporary.cleanup)
        self.request_guard = patch(
            "app.services.audio_analysis._request", side_effect=AssertionError("La prueba no debe usar red")
        )
        self.request_guard.start()
        self.addCleanup(self.request_guard.stop)
        self.next_index = 1
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        if self.window.analysis_worker is not None:
            self.window.analysis_worker.request_stop()
            self.wait_for(lambda: self.window.analysis_worker is None)
        self.window.media_player.stop()
        self.window.media_player.setSource(QUrl())
        self.window.close()
        self.window.deleteLater()
        self.flush_events()

    def flush_events(self):
        for _ in range(4):
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def wait_for(self, condition, timeout=10):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.flush_events()
            time.sleep(.01)
        self.flush_events()
        self.assertTrue(condition(), "La operación de prueba no terminó a tiempo.")

    def wait_idle(self):
        self.flush_events()
        self.wait_for(lambda: self.window.analysis_worker is None)
        # Procesa un eventual segundo lote encolado por completed/singleShot.
        self.flush_events()
        if self.window.analysis_worker is not None:
            self.wait_for(lambda: self.window.analysis_worker is None)
        self.flush_events()

    def enable_fake_credentials(self):
        self.window.transcription_api_key.setText("synthetic-transcription-key")
        self.window.analysis_api_key.setText("synthetic-analysis-key")

    def add_call(self, category="PENDIENTE", *, terms=("denuncia",), error=False):
        index = self.next_index
        self.next_index += 1
        path = self.audio_folder / f"q-000-099000{index:04d}-20260917-120000-{index}.wav"
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(bytes([index, 0]) * 8000)
        self.window.database.register_calls([
            SimpleNamespace(filename=path.name, source_path=path, duration=1)
        ])
        if category != "PENDIENTE":
            self.window.database.save_call_result(
                path, f"synthetic-result-{index}", keyword_signature(terms), TRANSCRIPT,
                {**NORMAL, "category": category},
            )
        if error:
            self.window.database.set_call_status(path, "ERROR", "Error sintético")
        self.reload_calls()
        return path

    def reload_calls(self):
        records = [call_record_from_row(row) for row in self.window.database.call_rows()]
        self.window.call_records = records
        self.window.calls = {call.call_id: call for call in records}
        self.window._populate_call_list()
        self.window._update_category_metrics()
        if records:
            self.window.call_list.setCurrentRow(0)
        self.flush_events()

    def visible_ids(self):
        return {
            call.call_id for row, call in enumerate(self.window.call_records)
            if not self.window.call_list.item(row).isHidden()
        }

    def test_manual_review_promotes_normal_and_mailbox_in_metrics_export_and_reopen(self):
        paths = {category: self.add_call(category) for category in ("NORMAL", "BUZON")}
        base = self.folder / "synthetic-base.xlsx"
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = "Hoja1"
        sheet.append(["Teléfono", "Nombre", "ID", "Estado"])
        sheet.append(["0990000001", "PERSONA UNO", "0123456789", "Pendiente"])
        sheet.append(["0990000002", "PERSONA DOS", "0123456790", "Pendiente"])
        book.save(base)
        book.close()

        for category, path in paths.items():
            with self.subTest(category=category):
                call = next(call for call in self.window.call_records if call.source_path == path)
                self.window._select_call_by_id(call.call_id)
                self.assertTrue(self.window.reviewed_button.isEnabled())
                self.window.reviewed_button.click()
                reviewed = self.window.calls[call.call_id]
                self.assertTrue(reviewed.reviewed)
                self.assertTrue(reviewed.sensitive)
                self.assertEqual(reviewed.category_code, "ALERTA")
                self.assertEqual(self.window.database.call_rows([path])[0]["reviewed"], 1)

        self.assertEqual(self.window.sensitive_metric.text(), "2")
        self.assertEqual(self.window.normal_metric.text(), "0")
        self.assertEqual(self.window.mailbox_metric.text(), "0")
        self.window.status_filter.setCurrentIndex(self.window.status_filter.findData("alert"))
        self.assertEqual(len(self.visible_ids()), 2)
        automatic, verified = self.window._export_phone_groups()
        self.assertEqual(verified, {"0990000001", "0990000002"})
        output = self.folder / "verified.xlsx"
        result = export_audit_excel(output, base, automatic, verified, "verified")
        self.assertEqual(result.row_count, 2)
        exported = openpyxl.load_workbook(output, read_only=True, data_only=True)
        try:
            self.assertEqual([row[3] for row in list(exported.active.values)[1:]],
                             ["Denuncia verificada", "Denuncia verificada"])
        finally:
            exported.close()
        today = date.today()
        report = load_report(self.window.database, today, today + timedelta(days=1))
        self.assertEqual((report["incidents"], report["verified"], report["normal"], report["mailbox"]),
                         (2, 2, 0, 0))

        self.window.close()
        self.window.deleteLater()
        self.flush_events()
        self.window = SentryWindow(self.database_path)
        self.assertTrue(all(call.reviewed and call.category_code == "ALERTA" for call in self.window.call_records))
        for category, path in paths.items():
            call = next(call for call in self.window.call_records if call.source_path == path)
            self.window._select_call_by_id(call.call_id)
            self.window.reviewed_button.click()
            restored = self.window.calls[call.call_id]
            self.assertFalse(restored.reviewed)
            self.assertFalse(restored.sensitive)
            self.assertEqual(restored.category_code, category)
        self.assertEqual(self.window.sensitive_metric.text(), "0")
        self.assertEqual(self.window.normal_metric.text(), "1")
        self.assertEqual(self.window.mailbox_metric.text(), "1")
        self.assertEqual(self.window._export_phone_groups(), (set(), set()))

    def test_editing_finished_reanalyzes_completed_audio_and_reuses_transcription(self):
        path = self.add_call()
        self.enable_fake_credentials()
        with (
            patch("app.services.audio_analysis.transcribe", return_value=TRANSCRIPT) as transcribe,
            patch("app.services.audio_analysis.contextual_analysis", side_effect=[NORMAL, ALERT]) as analysis,
        ):
            self.window._start_analysis()
            self.wait_idle()
            first_terms = self.window.call_records[0].analysis_terms
            self.window.keywords_input.setText("denuncia, fraude")
            self.window.keywords_input.editingFinished.emit()
            self.wait_for(lambda: analysis.call_count == 2)
            self.wait_idle()
            self.assertEqual(transcribe.call_count, 1)
            self.assertEqual(analysis.call_count, 2)
        row = self.window.database.call_rows([path])[0]
        self.assertEqual(row["category"], "ALERTA")
        self.assertNotEqual(first_terms, row["analysis_terms"])
        self.assertEqual(row["analysis_terms"], keyword_signature(["denuncia", "fraude"]))
        self.assertEqual(self.window.database.settings()["keywords"], "denuncia, fraude")

    def test_typing_debounces_for_1500ms_before_committing_sensitive_terms(self):
        self.add_call("NORMAL")
        with patch.object(self.window, "_start_analysis") as start:
            self.window.keywords_input.setText("denuncia, fraude")
            started = time.monotonic()
            self.window.keywords_input.textEdited.emit(self.window.keywords_input.text())
            self.flush_events()
            start.assert_not_called()
            self.wait_for(lambda: start.called, timeout=4)
            self.assertGreaterEqual(time.monotonic() - started, 1.2)
            start.assert_called_once_with(automatic=True)

    def test_equivalent_keyword_order_accents_case_and_duplicates_do_not_start_analysis(self):
        self.window.keywords_input.setText("denuncia, acción legal")
        self.window._commit_sensitive_terms()
        self.add_call("NORMAL", terms=("denuncia", "acción legal"))
        with patch.object(self.window, "_start_analysis") as start:
            self.window.keywords_input.setText("  ACCION LEGAL, Denúncia, denuncia  ")
            self.window.keywords_input.editingFinished.emit()
            self.flush_events()
            start.assert_not_called()

    def test_automatic_reanalysis_excludes_unanalyzed_pending_and_error_calls(self):
        expected = {self.add_call(category) for category in ("NORMAL", "BUZON", "ALERTA")}
        expected.add(self.add_call("NORMAL", error=True))
        excluded = {self.add_call(), self.add_call(error=True)}
        self.enable_fake_credentials()
        with patch("app.ui.views.main_window.AnalysisWorker") as worker:
            try:
                self.window.keywords_input.setText("denuncia, fraude")
                self.window._commit_sensitive_terms()
                self.flush_events()
                worker.assert_called_once()
                requested = set(worker.call_args.args[1])
                self.assertEqual(requested, expected)
                self.assertTrue(requested.isdisjoint(excluded))
            finally:
                self.window.analysis_worker = None
                self.window._set_analysis_controls_locked(False)

    def test_missing_api_keys_reports_once_without_dialog_navigation_or_retry_loop(self):
        self.add_call("NORMAL")
        with (
            patch.object(self.window, "_show_toast") as toast,
            patch.object(self.window, "_switch_page") as navigation,
            patch.object(self.window, "_start_analysis", wraps=self.window._start_analysis) as start,
            patch.object(QMessageBox, "information") as information,
            patch.object(QMessageBox, "warning") as warning,
            patch.object(QMessageBox, "critical") as critical,
            patch.object(QMessageBox, "question") as question,
        ):
            self.window.keywords_input.setText("denuncia, fraude")
            self.window._commit_sensitive_terms()
            for _ in range(4):
                self.flush_events()
            self.assertIsNone(self.window.analysis_worker)
            start.assert_called_once_with(automatic=True)
            navigation.assert_not_called()
            for modal in (information, warning, critical, question):
                modal.assert_not_called()
            self.assertTrue(any("clave" in str(call).casefold() for call in toast.call_args_list))

    def test_save_settings_commits_keyword_change_without_manual_analyze_click(self):
        self.add_call("NORMAL")
        with (
            patch.object(self.window, "_persist_credentials", return_value=0),
            patch.object(self.window, "_persist_remote_connection", return_value=0),
            patch.object(self.window, "_start_analysis") as start,
        ):
            self.window.keywords_input.setText("denuncia, fraude")
            self.window._save_settings()
            self.flush_events()
            start.assert_called_once_with(automatic=True)
            self.assertEqual(self.window.database.settings()["keywords"], "denuncia, fraude")

    def run_busy_reanalysis(self, *, manual_stop):
        self.add_call()
        self.enable_fake_credentials()
        started = threading.Event()
        release = threading.Event()
        self.gates.append(release)
        observed = []

        def analyze(_transcript, keywords, *_args):
            observed.append(tuple(keywords))
            if len(observed) == 2:
                started.set()
                if not release.wait(8):
                    raise TimeoutError("La prueba no liberó el análisis simulado.")
            return NORMAL

        with (
            patch("app.services.audio_analysis.transcribe", return_value=TRANSCRIPT) as transcribe,
            patch("app.services.audio_analysis.contextual_analysis", side_effect=analyze),
        ):
            self.window._start_analysis()
            self.wait_idle()
            self.window.keywords_input.setText("denuncia, fraude")
            self.window._commit_sensitive_terms()
            self.wait_for(started.is_set)
            active = self.window.analysis_worker
            try:
                self.window.keywords_input.setText("denuncia, fraude, amenaza")
                self.window._commit_sensitive_terms()
                self.window.keywords_input.setText("denuncia, fraude, amenaza, cancelar servicio")
                self.window._commit_sensitive_terms()
                self.assertIs(self.window.analysis_worker, active)
                self.assertFalse(active._stop, "Editar términos no debe detener el análisis activo.")
                if manual_stop:
                    self.window._start_analysis()
                    self.assertTrue(active._stop)
            finally:
                release.set()
            self.wait_idle()
            self.assertEqual(transcribe.call_count, 1)
            self.assertEqual(len(observed), 2 if manual_stop else 3)
            if not manual_stop:
                self.assertEqual(observed[-1], ("denuncia", "fraude", "amenaza", "cancelar servicio"))
                self.assertEqual(self.window.call_records[0].analysis_terms, keyword_signature(observed[-1]))

    def test_keyword_edits_during_analysis_coalesce_and_run_once_after_completion(self):
        self.run_busy_reanalysis(manual_stop=False)

    def test_manual_stop_cancels_queued_keyword_reanalysis(self):
        self.run_busy_reanalysis(manual_stop=True)

    def test_keyword_reanalysis_waits_for_active_scan(self):
        self.add_call("NORMAL")
        self.window.local_scan_worker = object()
        with patch.object(self.window, "_start_analysis") as start:
            try:
                self.window.keywords_input.setText("denuncia, fraude")
                self.window._commit_sensitive_terms()
                self.flush_events()
                start.assert_not_called()
            finally:
                self.window.local_scan_worker = None
            self.window._run_pending_keyword_reanalysis()
            self.flush_events()
            start.assert_called_once_with(automatic=True)

    def test_rejected_close_during_scan_preserves_queued_reanalysis(self):
        self.add_call("NORMAL")
        self.window.local_scan_worker = object()
        with patch.object(self.window, "_start_analysis") as start:
            try:
                self.window.keywords_input.setText("denuncia, fraude")
                self.window._commit_sensitive_terms()
                self.flush_events()
                event = QCloseEvent()
                self.window.closeEvent(event)
                self.assertFalse(event.isAccepted())
                self.assertTrue(self.window._keyword_reanalysis_pending)
                start.assert_not_called()
            finally:
                self.window.local_scan_worker = None
            self.window._run_pending_keyword_reanalysis()
            self.flush_events()
            start.assert_called_once_with(automatic=True)

    def test_base_worker_completion_drains_queued_reanalysis(self):
        class BaseWorker(QObject):
            finished = Signal()

        self.add_call("NORMAL")
        worker = BaseWorker()
        self.window.bases_page.worker = worker
        self.window.bases_page.busy = True
        with patch.object(self.window, "_start_analysis") as start:
            try:
                self.window.keywords_input.setText("denuncia, fraude")
                self.window._commit_sensitive_terms()
                self.flush_events()
                start.assert_not_called()
                self.window.bases_page.busy = False
                self.window.bases_page.worker = None
                worker.finished.emit()
                self.flush_events()
                start.assert_called_once_with(automatic=True)
            finally:
                self.window.bases_page.busy = False
                self.window.bases_page.worker = None

    def test_failed_update_backup_completion_drains_queued_reanalysis(self):
        self.add_call("NORMAL")
        panel = self.window.updates_panel
        panel.worker = SimpleNamespace(mode="backup", deleteLater=lambda: None)
        with patch.object(self.window, "_start_analysis") as start:
            try:
                self.window.keywords_input.setText("denuncia, fraude")
                self.window._commit_sensitive_terms()
                self.flush_events()
                start.assert_not_called()
                panel._failed("Respaldo sintético cancelado")
                panel._finished()
                self.flush_events()
                start.assert_called_once_with(automatic=True)
            finally:
                panel.worker = None

    def test_editing_finished_then_analyze_click_starts_once_without_stopping_new_worker(self):
        self.add_call("NORMAL")
        self.enable_fake_credentials()
        with patch("app.ui.views.main_window.AnalysisWorker") as worker:
            try:
                self.window.keywords_input.setText("denuncia, fraude")
                self.window.keywords_input.editingFinished.emit()
                worker.assert_not_called()
                self.window.analyze_button.click()
                self.flush_events()
                worker.assert_called_once()
                worker.return_value.start.assert_called_once()
                worker.return_value.request_stop.assert_not_called()
            finally:
                self.window.analysis_worker = None
                self.window._set_analysis_controls_locked(False)

    def test_manual_retry_includes_verified_error_even_with_same_keyword_signature(self):
        path = self.add_call("NORMAL")
        self.window.database.set_reviewed(path, True)
        self.window.database.set_call_status(path, "ERROR", "Error transitorio sintético")
        self.reload_calls()
        self.enable_fake_credentials()
        current = self.window.call_records[0]
        self.assertEqual(current.category_code, "ALERTA")
        self.assertTrue(current.reviewed)
        self.assertEqual(current.analysis_terms, keyword_signature(["denuncia"]))
        with patch("app.ui.views.main_window.AnalysisWorker") as worker:
            try:
                self.window._start_analysis()
                self.flush_events()
                worker.assert_called_once()
                self.assertEqual(worker.call_args.args[1], [path])
            finally:
                self.window.analysis_worker = None
                self.window._set_analysis_controls_locked(False)


if __name__ == "__main__":
    unittest.main()
