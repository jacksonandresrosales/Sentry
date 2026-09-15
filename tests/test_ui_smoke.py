from __future__ import annotations

import os
import tempfile
import unittest
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QLineEdit, QPushButton

from app.services.audio_analysis import assign_roles
from app.secret_store import unprotect
from app.ui.views.main_window import (
    CallRecord, SentryWindow, TranscriptLine, audio_filename_metadata, audio_phone_from_filename, call_record_from_audio,
    call_record_from_row, dated_local_directories, install_ui_font, issabel_directories, scan_audio_files,
)


class SentryWindowSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        instance = QApplication.instance()
        cls.app = instance if isinstance(instance, QApplication) else QApplication([])
        install_ui_font(cls.app)

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.window = SentryWindow(Path(self.temp.name) / "audit.db")

    def tearDown(self) -> None:
        self.window.close()
        self.temp.cleanup()

    def _load_sample_calls(self) -> None:
        transcript = (
            TranscriptLine(3, "Asesor", "Buenas tardes, le atiende Ana."),
            TranscriptLine(10, "Cliente", "Llamo porque necesito ayuda con mi factura."),
            TranscriptLine(38, "Cliente", "Presentaré una demanda con mi abogado.", True, (38.0, 38.3, 38.7, 39.0, 39.4, 39.8)),
            TranscriptLine(47, "Asesor", "Voy a validar su caso."),
        )
        calls = [
            CallRecord(1, "primera.wav", "", "099***1234", "10:30", 54, "Alerta", True, "demanda", 38,
                       "Crítica", "Resumen", "Demanda", transcript, category_code="ALERTA", tags=("demanda", "abogado")),
            CallRecord(2, "segunda.wav", "", "098***5678", "11:15", 48, "Alerta", True, "abogado", 38,
                       "Alta", "Resumen", "Abogado", transcript, category_code="ALERTA", tags=("abogado",)),
            CallRecord(3, "tercera.wav", "", "097***9012", "12:00", 15, "Buzón", False, "Buzón", None,
                       "Baja", "Sin conversación", "Buzón", transcript[:1], category_code="BUZON"),
            CallRecord(4, "cuarta.wav", "", "096***3456", "13:00", 42, "Normal", False, "Normal", None,
                       "Baja", "Sin novedad", "Consulta", transcript[:2], category_code="NORMAL"),
        ]
        self.window.call_records = calls
        self.window.calls = {call.call_id: call for call in calls}
        self.window._populate_call_list()
        self.window._update_category_metrics()
        self.window.call_list.setCurrentRow(0)

    def test_filters_selection_and_evidence_jump(self) -> None:
        self.assertEqual(self.window.call_list.count(), 0)
        self.assertEqual(self.window.detail_pages.currentIndex(), 0)
        self.assertEqual(self.window.report_total_value.text(), "0")
        self.assertEqual(len(self.window.sort_button.menu().actions()), 7)
        self.assertFalse(self.window.play_button.isEnabled())
        self.assertFalse(self.window.original_button.isEnabled())
        self.assertEqual(self.window.status_filter.parentWidget().objectName(), "sectionHeader")

        self._load_sample_calls()
        self.assertEqual(self.window.report_total_value.text(), "4")
        self.assertEqual(self.window.report_sensitive_value.text(), "2")
        self.window.status_filter.setCurrentIndex(1)
        visible = sum(
            not self.window.call_list.item(row).isHidden()
            for row in range(self.window.call_list.count())
        )
        self.assertEqual(visible, 2)

        self.window.call_list.setCurrentRow(0)
        self.window._jump_to_evidence()
        self.assertEqual(self.window.current_second, 38)
        self.assertIn("00:38", self.window.time_display.text())
        self.assertIs(self.window.transcript_rows[2][1], self.window.critical_line)
        self.assertEqual(self.window.critical_line.property("playbackState"), "active")

        self.window._on_player_position_changed(10_000)
        self.assertEqual(self.window.current_second, 10)
        self.assertEqual(self.window.transcript_rows[1][1].property("playbackState"), "active")
        self.assertEqual(self.window.critical_line.property("playbackState"), "upcoming")

        self.assertEqual(self.window._heard_word_count(2, 38.65), 2)

        self.window.transcript_rows[0][1].seek_requested.emit(3)
        self.assertEqual(self.window.current_second, 3)

        self.window._sort_calls("duration_asc", "menor duración")
        self.assertEqual(self.window.call_records[0].filename, "tercera.wav")

    def test_all_sensitive_phrases_are_highlighted(self) -> None:
        highlighted = self.window._highlight_keywords(
            "Presentará una demanda con su abogado.", ("demanda", "abogado")
        )
        self.assertEqual(highlighted.count("font-weight:700"), 2)
        self.assertIn(">demanda</span>", highlighted)
        self.assertIn(">abogado</span>", highlighted)

    def test_call_classifications_have_distinct_vector_icons(self) -> None:
        self._load_sample_calls()
        expected = {"ALERTA": "Demanda / alerta", "BUZON": "Buzón", "NORMAL": "Llamada normal"}
        for card in self.window.call_cards.values():
            badge = card.findChild(QFrame, "classificationBadge")
            self.assertIsNotNone(badge)
            category = badge.property("category")
            self.assertIn(category, expected)
            self.assertTrue(badge.findChild(QLabel, "classificationText").text())
            self.assertFalse(badge.findChild(QLabel, "classificationIcon").pixmap().isNull())

    def test_api_keys_are_masked_and_escalation_action_is_removed(self) -> None:
        self.assertFalse(self.window.windowIcon().isNull())
        self.assertNotIn("ASESOR", [label.text() for label in self.window.findChildren(QLabel)])
        logo = self.window.findChild(QLabel, "brandLogo")
        self.assertIsNotNone(logo)
        self.assertFalse(logo.pixmap().isNull())
        self.assertEqual(self.window.nav_buttons["config"].text(), "")
        self.assertEqual(self.window.nav_buttons["config"].accessibleName(), "Configuración")
        self.assertEqual(self.window.scan_button.text(), "")
        self.assertEqual(self.window.scan_button.accessibleName(), "Escanear carpeta local")
        self.assertEqual(self.window.directory_label.text(), "Sin directorio")
        self.assertEqual(self.window.directory_label.toolTip(), "No se ha configurado una carpeta")
        self.assertIn("QComboBox::down-arrow", self.window.styleSheet())

        self.assertEqual(self.window.transcription_api_key.echoMode(), QLineEdit.EchoMode.Password)
        self.window.transcription_key_toggle.click()
        self.assertEqual(self.window.transcription_api_key.echoMode(), QLineEdit.EchoMode.Normal)

        self.window.transcription_api_key.clear()
        self.window.transcription_validate.click()
        self.assertIn("Introduce una clave", self.window.transcription_api_status.text())

        self.window.transcription_api_key.setText("session-secret")
        self.window.transcription_provider.setCurrentIndex(1)
        self.assertEqual(self.window.transcription_api_key.text(), "")
        self.assertEqual(self.window.transcription_model.currentText(), "gpt-4o-transcribe-diarize")

        button_labels = {button.text() for button in self.window.findChildren(QPushButton)}
        self.assertNotIn("Escalar a supervisión", button_labels)

    def test_winscp_connection_is_masked_encrypted_and_restored(self) -> None:
        self.assertEqual(self.window.remote_password.echoMode(), QLineEdit.EchoMode.Password)
        self.window.remote_host.setText("192.0.2.10")
        self.window.remote_port.setText("22")
        self.window.remote_username.setText("jeremy")
        self.window.remote_password.setText("secreto-local")
        self.window.remote_path.setText("/grabaciones")
        self.window.remote_fingerprint.setText("ssh-ed25519 255 SHA256:huella-prueba")

        self.assertEqual(self.window._persist_remote_connection(), 1)
        stored = self.window.database.remote_connection()
        self.assertIsNotNone(stored)
        self.assertNotEqual(stored["encrypted_password"], "secreto-local")
        self.assertEqual(unprotect(stored["encrypted_password"]), "secreto-local")

        self.window.remote_password.clear()
        self.window._restore_remote_connection()
        self.assertEqual(self.window.remote_password.text(), "secreto-local")
        self.assertIn("Configuración cifrada recuperada", self.window.remote_status.text())
        self.assertTrue(self.window.remote_search_button.isEnabled())

        self.window._remote_search_succeeded([{
            "path": "/var/spool/asterisk/monitor/2026/09/q-000-0990000001.wav",
            "name": "q-000-0990000001.wav",
            "size": 16000,
            "modified": "2026-09-15 10:30:00",
        }])
        self.assertEqual(self.window.remote_results.count(), 1)
        self.assertIn("q-000-0990000001.wav", self.window.remote_results.item(0).text())
        self.assertIn("1 archivo encontrado", self.window.remote_search_status.text())

    def test_model_catalogs_are_normalized(self) -> None:
        deepgram = {"stt": [{"name": "general", "canonical_name": "nova-3-general"}]}
        gemini = {
            "models": [
                {"name": "models/gemini-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]},
            ]
        }
        openai = {"data": [{"id": "gpt-chat"}, {"id": "gpt-audio-transcribe"}]}

        self.assertEqual(self.window._extract_models("deepgram", "transcription", deepgram), ["nova-3-general"])
        self.assertEqual(self.window._extract_models("gemini", "analysis", gemini), ["gemini-flash"])
        self.assertEqual(self.window._extract_models("openai", "transcription", openai), ["gpt-audio-transcribe"])

    def test_speaker_roles_use_dialogue_instead_of_speaker_order(self) -> None:
        transcript = {"speaker_count": 2, "segments": [
            {"speaker": "Hablante 1", "text": "Hola, llamo porque me cobraron dos veces."},
            {"speaker": "Hablante 2", "text": "Buenos días, le atiende Ana. ¿En qué puedo ayudarle?"},
            {"speaker": "Hablante 1", "text": "Quiero reclamar el valor de mi factura."},
            {"speaker": "Hablante 2", "text": "Permítame validar su número de caso."},
        ]}
        speakers = [item["speaker"] for item in assign_roles(transcript)["segments"]]
        self.assertEqual(speakers, ["Cliente", "Asesor", "Cliente", "Asesor"])

    def test_filename_date_and_time_are_restored_from_sqlite(self) -> None:
        path = Path(self.temp.name) / "q-000-0964220551-20260528-162746-1780003653.920447.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\0\0" * 8000)

        self.assertEqual(audio_filename_metadata(path), ("096***0551", "16:27:46"))
        detected = call_record_from_audio(path, 1)
        self.assertEqual(detected.clock, "16:27:46")
        self.window.database.register_calls([detected])
        restored = call_record_from_row(self.window.database.call_rows([path])[0])
        self.assertEqual(restored.clock, "16:27:46")
        self.assertEqual(restored.customer, "096***0551")

    def test_base_phone_filter_accepts_only_matching_q_audio(self) -> None:
        folder = Path(self.temp.name) / "audios"
        folder.mkdir()
        matching = folder / "q-000-0990000001-20260528-162746-1.wav"
        other_phone = folder / "q-000-0990000002-20260528-162746-2.wav"
        wrong_prefix = folder / "x-000-0990000001-20260528-162746-3.wav"
        for path in (matching, other_phone, wrong_prefix):
            path.touch()

        self.assertEqual(audio_phone_from_filename(matching), "0990000001")
        self.assertIsNone(audio_phone_from_filename(wrong_prefix))
        self.assertEqual(scan_audio_files(folder, {"0990000001"}), (matching,))
        self.assertEqual(
            scan_audio_files(folder, phone_dates={"0990000001": frozenset({"20260528"})}),
            (matching,),
        )
        self.assertEqual(scan_audio_files(folder, phone_dates={"0990000001": frozenset({"20260529"})}), ())

    def test_local_nas_and_issabel_sources_use_date_folders(self) -> None:
        root = Path(self.temp.name) / "recordings"
        expected = root / "2026" / "07" / "06"
        expected.mkdir(parents=True)
        self.assertEqual(dated_local_directories(root, {"20260706"}), (expected,))
        self.assertEqual(
            issabel_directories("/var/spool/asterisk/monitor/2026/", {"20260706"}),
            ["/var/spool/asterisk/monitor/2026/07/06/"],
        )
        self.assertEqual(self.window.audio_source.count(), 3)
        self.assertEqual(
            [self.window.audio_source.itemData(index) for index in range(self.window.audio_source.count())],
            ["local", "nas", "issabel"],
        )

    def test_directory_scan_finds_supported_audio_recursively(self) -> None:
        root = Path.cwd() / ".tmp" / "scan-audio-test"
        nested = root / "septiembre"
        files = (root / "llamada.wav", root / "ignorar.txt", nested / "LLAMADA.MP3")
        nested.mkdir(parents=True, exist_ok=True)
        try:
            with wave.open(str(files[0]), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8000)
                audio.writeframes(b"\0\0" * 8000)
            for path in files[1:]:
                path.touch()

            detected = scan_audio_files(root)
            self.window._finish_scan(root)
            displayed_names = [self.window.call_records[index].filename for index in range(len(self.window.call_records))]
            media_source = self.window.media_player.source().toLocalFile()
            play_enabled = self.window.play_button.isEnabled()
            original_enabled = self.window.original_button.isEnabled()
            customer_heading = self.window.call_cards[1].findChild(QLabel, "callCustomer")
            footer_texts = [label.text() for label in self.window.call_cards[1].findChildren(QLabel, "monoMuted")]
        finally:
            self.window.media_player.stop()
            self.window.media_player.setSource(QUrl())
            self.app.processEvents()
            for path in files:
                path.unlink(missing_ok=True)
            nested.rmdir()
            root.rmdir()

        self.assertEqual([path.suffix.casefold() for path in detected], [".wav", ".mp3"])
        self.assertEqual(displayed_names, ["llamada.wav", "LLAMADA.MP3"])
        self.assertEqual(self.window.call_list.count(), 2)
        self.assertEqual(self.window.files_metric.text(), "2")
        self.assertEqual(self.window.call_records[0].agent, "")
        self.assertEqual(Path(media_source), files[0])
        self.assertTrue(play_enabled)
        self.assertTrue(original_enabled)
        self.assertEqual(customer_heading.text(), "Sin identificar")
        self.assertNotIn("Sin identificar", footer_texts)


if __name__ == "__main__":
    unittest.main()
