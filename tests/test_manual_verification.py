"""Denuncias manuales durables; solo SQLite y reportes sintéticos temporales."""
from datetime import date
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.database import Database, effective_call_category
from app.services.analytics import load_report


TRANSCRIPT = {"text": "Texto de prueba sin datos reales.", "segments": [], "speaker_count": 2}
START, END = date(2026, 9, 17), date(2026, 9, 18)


class ManualVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.database = Database(self.root / "synthetic.db")

    def tearDown(self):
        self.temporary.cleanup()

    def add_call(self, category, name=None, *, hits=None):
        path = self.root / (name or f"synthetic-{category}.wav")
        self.database.register_calls([SimpleNamespace(filename=path.name, source_path=path, duration=30)])
        self.save_result(path, category, hits=hits)
        return path

    def save_result(self, path, category, *, hits=None):
        self.database.save_call_result(path, f"cache-{category}", f"terms-{category}", TRANSCRIPT,
            {"category": category, "summary": f"Resultado automático {category}", "risk": "BAJO",
             "sentiment": "NEUTRAL", "hits": hits or []})
        with self.database.connect() as connection:
            connection.execute("UPDATE calls SET processed_at=datetime(?,'utc') WHERE file_path=?",
                               ("2026-09-17 12:00:00", str(path)))

    def raw_row(self, path):
        with self.database.connect() as connection:
            return dict(connection.execute("SELECT * FROM calls WHERE file_path=?", (str(path),)).fetchone())

    def test_normal_and_mailbox_become_effective_alert_without_fabricating_evidence(self):
        for category in ("NORMAL", "BUZON"):
            with self.subTest(category=category):
                path = self.add_call(category)
                original = self.raw_row(path)
                self.database.set_reviewed(path, True)
                row = self.database.call_rows([path])[0]
                self.assertEqual((row["category"], row["automatic_category"], row["reviewed"]),
                                 ("ALERTA", category, 1))
                self.assertEqual(row["hits"], [])
                self.assertEqual(row["has_sensitive_keyword"], 0)
                raw = self.raw_row(path)
                self.assertEqual(raw["category"], category)
                for field in ("status", "risk_level", "summary", "transcript_json", "analysis_terms", "cache_key"):
                    self.assertEqual(raw[field], original[field])

    def test_removing_verification_restores_automatic_normal_mailbox_or_alert(self):
        for category in ("NORMAL", "BUZON", "ALERTA"):
            with self.subTest(category=category):
                path = self.add_call(category)
                self.database.set_reviewed(path)
                self.database.set_reviewed(path, False)
                row = self.database.call_rows([path])[0]
                self.assertEqual((row["category"], row["automatic_category"], row["reviewed"]),
                                 (category, category, 0))

    def test_verification_survives_reanalysis_and_unverify_restores_latest_result(self):
        path = self.add_call("NORMAL")
        self.database.set_reviewed(path)
        for category in ("BUZON", "ALERTA", "NORMAL"):
            with self.subTest(reanalysis=category):
                self.save_result(path, category)
                row = self.database.call_rows([path])[0]
                self.assertEqual(row["category"], "ALERTA")
                self.assertEqual(row["automatic_category"], category)
                self.assertEqual(row["reviewed"], 1)
                self.assertEqual(row["analysis_terms"], f"terms-{category}")
                self.database.set_reviewed(path, False)
                self.assertEqual(self.database.call_rows([path])[0]["category"], category)
                self.database.set_reviewed(path)

    def test_reopening_database_preserves_manual_decision_without_schema_migration(self):
        path = self.add_call("BUZON")
        self.database.set_reviewed(path)
        with self.database.connect() as connection:
            original_schema = connection.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
            original_schema = [tuple(row) for row in original_schema]
        reopened = Database(self.database.path)
        self.assertEqual(reopened.call_rows([path])[0]["category"], "ALERTA")
        self.assertEqual(reopened.call_rows([path])[0]["automatic_category"], "BUZON")
        with reopened.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 6)
            self.assertEqual([tuple(row) for row in connection.execute("SELECT sql FROM sqlite_master ORDER BY name")],
                             original_schema)
        reopened.set_reviewed(path, False)
        self.assertEqual(reopened.call_rows([path])[0]["category"], "BUZON")

    def test_failed_reanalysis_keeps_manual_alert_and_retains_actual_error_status(self):
        path = self.add_call("NORMAL")
        self.database.set_reviewed(path)
        self.database.set_call_status(path, "ERROR", "Fallo de API simulado")
        row = self.database.call_rows([path])[0]
        self.assertEqual((row["category"], row["automatic_category"], row["status"]),
                         ("ALERTA", "NORMAL", "ERROR"))
        self.assertEqual(row["analysis_error"], "Fallo de API simulado")
        self.database.set_reviewed(path, False)
        row = self.database.call_rows([path])[0]
        self.assertEqual((row["category"], row["status"]), ("NORMAL", "ERROR"))

    def test_filtered_and_global_reads_share_effective_category_and_input_order(self):
        first = self.add_call("NORMAL")
        second = self.add_call("BUZON")
        self.database.set_reviewed(first)
        self.database.set_reviewed(second)
        by_path = self.database.call_rows([second, first])
        global_rows = self.database.call_rows()
        self.assertEqual([row["file_path"] for row in by_path], [str(second), str(first)])
        self.assertEqual([row["category"] for row in global_rows], ["ALERTA", "ALERTA"])
        self.assertEqual([row["automatic_category"] for row in global_rows], ["NORMAL", "BUZON"])
        self.assertEqual(self.database.call_rows([]), [])

    def test_reports_include_manual_alerts_in_details_daily_metrics_and_base_totals(self):
        normal = self.add_call("NORMAL")
        mailbox = self.add_call("BUZON")
        self.add_call("NORMAL", "synthetic-unreviewed.wav")
        for path in (normal, mailbox):
            self.database.set_reviewed(path)
            row = self.database.call_rows([path])[0]
            self.database.record_analysis_base(row["id"], self.root / "synthetic-base.xlsx")
        with self.database.connect() as connection:
            connection.execute("UPDATE call_bases SET analyzed_at=datetime('2026-09-17 12:00:00','utc')")
        report = load_report(self.database, START, END)
        self.assertEqual((report["completed"], report["incidents"], report["verified"], report["normal"], report["mailbox"]),
                         (3, 2, 2, 1, 0))
        self.assertEqual(report["bases"][0]["incidents"], 2)
        self.assertEqual(report["daily"], [("17/09", 3, 2)])
        self.assertEqual({row["automatic_category"] for row in report["calls"] if row["reviewed"]}, {"NORMAL", "BUZON"})
        self.database.set_reviewed(mailbox, False)
        report = load_report(self.database, START, END)
        self.assertEqual((report["incidents"], report["verified"], report["mailbox"]), (1, 1, 1))
        self.assertEqual(report["bases"][0]["incidents"], 1)

    def test_failed_reanalysis_remains_in_reports_without_counting_as_completed(self):
        path = self.add_call("NORMAL", hits=[{
            "keyword": "término sintético", "second": 2, "snippet": "Contexto de prueba", "validated": False,
        }])
        self.database.set_reviewed(path)
        self.database.set_call_status(path, "ERROR", "Fallo de API simulado")
        report = load_report(self.database, START, END)
        self.assertEqual((report["completed"], report["evaluated"], report["incidents"], report["verified"], report["errors"]),
                         (0, 1, 1, 1, 1))
        self.assertEqual(report["daily"], [("17/09", 0, 1)])
        self.assertEqual(report["calls"][0]["category"], "ALERTA")
        self.assertEqual(report["calls"][0]["keywords"], "término sintético")
        self.database.set_reviewed(path, False)
        report = load_report(self.database, START, END)
        self.assertEqual((report["incidents"], report["verified"], report["evaluated"]), (0, 0, 0))

    def test_effective_category_has_safe_pending_fallback(self):
        self.assertEqual(effective_call_category(None, False), "PENDIENTE")
        self.assertEqual(effective_call_category("", True), "ALERTA")

    def test_report_ui_and_excel_retain_verified_error_and_true_technical_status(self):
        import openpyxl
        from PySide6.QtWidgets import QApplication
        from app.ui.views.reports_page import ReportsPage

        app = QApplication.instance() or QApplication([])
        path = self.add_call("BUZON")
        self.database.set_reviewed(path)
        self.database.set_call_status(path, "ERROR", "Fallo de API simulado")
        page = ReportsPage(self.database)
        try:
            page.start_date, page.end_date = START, END
            page.render(load_report(self.database, START, END))
            self.assertEqual(page.metrics["completed"].text(), "0")
            self.assertEqual(page.metrics["verified"].text(), "1")
            self.assertEqual(page.incidents.rowCount(), 1)
            self.assertIn("100.0%", page.summary.text())
            destination = self.root / "synthetic-report.xlsx"
            with patch("app.ui.views.reports_page.QFileDialog.getSaveFileName", return_value=(str(destination), "Excel")):
                page.export_excel()
            book = openpyxl.load_workbook(destination, read_only=True)
            try:
                incident_rows = list(book["Incidentes"].values)
                call_rows = list(book["Llamadas"].values)
                self.assertEqual(len(incident_rows), 2)
                self.assertEqual(incident_rows[1][3], "Verificada")
                self.assertEqual(call_rows[1][2:5], ("ERROR", "ALERTA", "Sí"))
            finally:
                book.close()
        finally:
            page.close()
            page.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
