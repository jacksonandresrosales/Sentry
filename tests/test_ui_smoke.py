from __future__ import annotations

import os
import tempfile
import unittest
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton

from app.ui.views.main_window import SentryWindow, install_ui_font, scan_audio_files


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

    def test_filters_selection_and_evidence_jump(self) -> None:
        self.assertEqual(self.window.call_list.count(), 4)
        self.assertFalse(self.window.play_button.isEnabled())
        self.assertFalse(self.window.original_button.isEnabled())

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

    def test_api_keys_are_masked_and_escalation_action_is_removed(self) -> None:
        logo = self.window.findChild(QLabel, "brandLogo")
        self.assertIsNotNone(logo)
        self.assertFalse(logo.pixmap().isNull())
        self.assertEqual(self.window.nav_buttons["config"].text(), "")
        self.assertEqual(self.window.nav_buttons["config"].accessibleName(), "Configuración")
        self.assertEqual(self.window.scan_button.text(), "")
        self.assertEqual(self.window.scan_button.accessibleName(), "Escanear carpeta")
        self.assertEqual(self.window.directory_label.text(), "Llamadas_Entrantes")
        self.assertEqual(self.window.directory_label.toolTip(), r"C:\Grabaciones\Llamadas_Entrantes")
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
        self.assertEqual(Path(media_source), files[0])
        self.assertTrue(play_enabled)
        self.assertTrue(original_enabled)


if __name__ == "__main__":
    unittest.main()
