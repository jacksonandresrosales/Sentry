from __future__ import annotations

import os
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QLineEdit, QPushButton

from app.ui.views.main_window import AudioAnalysisJob, CallRecord, SentryWindow, TranscriptLine, _infer_speaker_roles, _transcription_request, install_ui_font, scan_audio_files


class SentryWindowSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        instance = QApplication.instance()
        cls.app = instance if isinstance(instance, QApplication) else QApplication([])
        install_ui_font(cls.app)

    def setUp(self) -> None:
        settings_path = Path.cwd() / ".tmp" / "sentry-test.ini"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.unlink(missing_ok=True)
        self.settings_path = settings_path
        self.settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
        self.environment = patch.dict(os.environ, {
            "DEEPGRAM_API_KEY": "",
            "GEMINI_API_KEY": "",
            "GOOGLE_API_KEY": "",
            "OPENAI_API_KEY": "",
        })
        self.environment.start()
        self.window = SentryWindow(self.settings)

    def tearDown(self) -> None:
        self.window.close()
        self.settings.clear()
        self.settings.sync()
        self.environment.stop()
        self.settings_path.unlink(missing_ok=True)

    def _load_sample_calls(self) -> None:
        transcript = (
            TranscriptLine(3, "Asesor", "Buenas tardes, le atiende Ana."),
            TranscriptLine(10, "Cliente", "Llamo porque necesito ayuda con mi factura."),
            TranscriptLine(38, "Cliente", "Quiero presentar una demanda.", True),
            TranscriptLine(47, "Asesor", "Voy a validar su caso."),
        )
        calls = [
            CallRecord(1, "primera.wav", "", "099***1234", "10:30", 54, "Alerta", True, "demanda", 38, "Crítica", "Resumen", "Demanda", transcript),
            CallRecord(2, "segunda.wav", "", "098***5678", "11:15", 48, "Alerta", True, "abogado", 20, "Alta", "Resumen", "Abogado", transcript),
        ]
        self.window.call_records = calls
        self.window.calls = {call.call_id: call for call in calls}
        self.window._populate_call_list()
        self.window._update_metrics()
        self.window.call_list.setCurrentRow(0)

    def test_filters_selection_and_evidence_jump(self) -> None:
        self.assertEqual(self.window.call_list.count(), 0)
        self.assertEqual(self.window.files_metric.text(), "0")
        self.assertEqual(self.window.sensitive_metric.text(), "0")
        self.assertEqual(self.window.normal_metric.text(), "0")
        self.assertEqual(self.window.nav_buttons["audit"].text(), "Auditoría")
        self.assertEqual(self.window.detail_pages.currentIndex(), 0)
        self.assertNotIn("Llamadas detectadas", [label.text() for label in self.window.findChildren(QLabel)])
        self.assertEqual(self.window.sort_button.text(), "Organizar llamadas")
        self.assertEqual(len(self.window.sort_button.menu().actions()), 7)
        self.assertFalse(self.window.play_button.isEnabled())
        self.assertFalse(self.window.original_button.isEnabled())

        self._load_sample_calls()
        self.window.status_filter.setCurrentIndex(1)
        visible = sum(
            not self.window.call_list.item(row).isHidden()
            for row in range(self.window.call_list.count())
        )
        self.assertEqual(visible, 2)

        self.window.call_list.setCurrentRow(0)
        second_row = self.window.transcript_rows[1][1]
        second_row.seek_requested.emit(10)
        self.assertEqual(self.window.current_second, 10)
        self.assertEqual(self.window.transcript_rows[0][1].property("playbackState"), "played")
        self.assertEqual(second_row.property("playbackState"), "active")
        self.assertIn("#1f7830", self.window.transcript_rows[1][2].text())

        self.window._jump_to_evidence()
        self.assertEqual(self.window.current_second, 38)
        self.assertIn("00:38", self.window.time_display.text())
        self.assertEqual(self.window.transcript_rows[2][1].property("playbackState"), "active")

        self.window.keywords_input.setText("abogado, demanda")
        self.window._sort_calls("priority", "Prioridad")
        self.assertEqual(self.window.call_list.item(0).data(Qt.ItemDataRole.UserRole), 2)

    def test_api_keys_are_masked_and_escalation_action_is_removed(self) -> None:
        self.assertFalse(self.window.windowIcon().isNull())
        identity = self.window.findChild(QFrame, "contentPanel")
        self.assertNotIn("AGENTE", [label.text() for label in identity.findChildren(QLabel)])
        logo = self.window.findChild(QLabel, "brandLogo")
        self.assertIsNotNone(logo)
        self.assertFalse(logo.pixmap().isNull())
        self.assertEqual(self.window.nav_buttons["config"].text(), "")
        self.assertEqual(self.window.nav_buttons["config"].accessibleName(), "Configuración")
        self.assertEqual(self.window.scan_button.text(), "")
        self.assertEqual(self.window.scan_button.accessibleName(), "Escanear carpeta")
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
        self.assertEqual(self.window.transcription_model.currentText(), "gpt-4o-mini-transcribe")

        self.window.config_directory.setText(r"C:\Audios")
        self.window.keywords_input.setText("demanda, abogado")
        self.window.transcription_provider.setCurrentIndex(0)
        self.window.transcription_model.setCurrentText("nova-3")
        self.window.transcription_api_key.setText("deepgram-secret")
        self.window.analysis_api_key.setText("gemini-secret")
        self.window._save_settings()
        encrypted = str(self.settings.value("api/transcription/deepgram/key"))
        self.assertNotIn("deepgram-secret", encrypted)

        self.window.close()
        self.window = SentryWindow(self.settings)
        self.assertEqual(self.window.config_directory.text(), r"C:\Audios")
        self.assertEqual(self.window.keywords_input.text(), "demanda, abogado")
        self.assertEqual(self.window.transcription_api_key.text(), "deepgram-secret")
        self.assertEqual(self.window.analysis_api_key.text(), "gemini-secret")

        button_labels = {button.text() for button in self.window.findChildren(QPushButton)}
        self.assertNotIn("Escalar a supervisión", button_labels)

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

    def test_gemini_is_skipped_for_calls_without_sensitive_terms(self) -> None:
        summaries = []
        with patch("app.ui.views.main_window._transcription_request", return_value=(
            [{"second": 0, "speaker": "Cliente", "text": "Consulta de saldo"}],
            "Consulta de saldo",
        )), patch("app.ui.views.main_window._summary_request", side_effect=lambda *args: summaries.append(args) or "Resumen"):
            normal = AudioAnalysisJob(1, Path("normal.wav"), ("deepgram", "nova-3", "stt"), ("gemini", "gemini-2.5-flash-lite", "gemini"), ("demanda", "abogado"))
            normal.run()
        self.assertEqual(summaries, [])

    def test_speaker_roles_use_dialogue_instead_of_speaker_order(self) -> None:
        utterances = [
            {"speaker": 0, "transcript": "Hola, llamo porque me cobraron dos veces."},
            {"speaker": 1, "transcript": "Buenos días, le atiende Ana. ¿En qué puedo ayudarle?"},
            {"speaker": 0, "transcript": "Quiero reclamar el valor de mi factura."},
            {"speaker": 1, "transcript": "Permítame validar su número de caso."},
        ]
        self.assertEqual(_infer_speaker_roles(utterances), {"0": "Cliente", "1": "Asesor"})

        uncertain = [
            {"speaker": 0, "transcript": "Buenos días."},
            {"speaker": 1, "transcript": "Buenos días."},
        ]
        self.assertEqual(_infer_speaker_roles(uncertain), {"0": "Participante 1", "1": "Participante 2"})

    def test_deepgram_word_timestamps_are_preserved(self) -> None:
        audio_path = Path.cwd() / ".tmp" / "timed-transcript.wav"
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"audio")
        payload = {
            "results": {
                "utterances": [{
                    "start": 1.2,
                    "speaker": 0,
                    "transcript": "Llamo porque necesito ayuda",
                    "words": [{"start": 1.2}, {"start": 1.7}, {"start": 2.1}, {"start": 2.6}],
                }],
                "channels": [{"alternatives": [{"transcript": "Llamo porque necesito ayuda"}]}],
            }
        }
        try:
            with patch("app.ui.views.main_window._request_bytes", return_value=payload):
                lines, _transcript = _transcription_request(audio_path, "deepgram", "nova-3", "key")
        finally:
            audio_path.unlink(missing_ok=True)

        self.assertEqual(lines[0]["speaker"], "Cliente")
        self.assertEqual(lines[0]["word_seconds"], (1.2, 1.7, 2.1, 2.6))

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
            self.window._sort_calls("size_asc", "Menor peso")
            smallest_name = self.window.call_records[0].filename
            self.window._sort_calls("size_desc", "Mayor peso")
            largest_name = self.window.call_records[0].filename
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
        self.assertEqual(smallest_name, "LLAMADA.MP3")
        self.assertEqual(largest_name, "llamada.wav")
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
