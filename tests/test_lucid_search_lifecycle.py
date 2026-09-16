"""Remote audio becomes usable before a Lucid server scan finishes."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import wave
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from app.services.base_conversion import BaseAudioIndex
from app.ui.views.main_window import SentryWindow, call_record_from_audio


class LucidSearchLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.window = SentryWindow(self.root / "audit.db")
        self.window.active_base_path = (self.root / "lucid.xlsx").resolve()
        self.window.active_base_index = BaseAudioIndex(
            {"0990000001": frozenset()}, {"0990000001": "CLIENTE PRUEBA"}, "lucid"
        )
        self.window.active_base_phones = self.window.active_base_index.phones
        self.window.audio_source.setCurrentIndex(self.window.audio_source.findData("issabel"))
        self.config_patch = patch.object(
            self.window, "_remote_config",
            return_value={"fingerprint": "test", "password": "test", "remote_path": "/monitor"},
        )
        self.config_patch.start()

    def tearDown(self):
        if self.window.issabel_match_worker is not None:
            self.window.issabel_match_worker.request_stop()
            self.wait_until(lambda: self.window.issabel_match_worker is None)
        self.config_patch.stop()
        self.window.media_player.stop()
        self.window.media_player.setSource(QUrl())
        self.window.close()
        self.app.processEvents()
        self.temporary.cleanup()

    def wait_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(predicate(), self.window.scan_status_label.text())

    def make_audio(self, time_part="120000"):
        path = self.root / f"out-990000001-1001-20260916-{time_part}-1234567890.1.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x00\x00" * 8000)
        return path

    def test_first_real_audio_is_visible_before_full_scan_finishes_and_cancel_keeps_it(self):
        audio = self.make_audio()
        callback_sent = threading.Event()

        def downloader(*_args, progress_callback, should_cancel, **_kwargs):
            progress_callback({"phase": "download", "paths": [str(audio)],
                               "candidate_count": 27, "match_count": 1})
            callback_sent.set()
            deadline = time.monotonic() + 5
            while not should_cancel() and time.monotonic() < deadline:
                time.sleep(0.01)
            return {"paths": [str(audio)], "candidate_count": 27, "match_count": 1,
                    "canceled": should_cancel()}

        self.window._prepare_base_refresh()
        with patch("app.ui.views.main_window.match_and_download_remote_audio", side_effect=downloader):
            self.window._start_issabel_match()
            self.wait_until(lambda: len(self.window.call_records) == 1)
            self.assertTrue(callback_sent.is_set())
            self.assertIsNotNone(self.window.issabel_match_worker)
            self.assertTrue(self.window.issabel_match_worker.isRunning())
            self.assertEqual(self.window.detail_pages.currentIndex(), 1)
            self.assertEqual(self.window.filename_label.text(), "CLIENTE PRUEBA")
            self.assertEqual(self.window.call_records[0].customer, "099***0001")
            self.assertIn("27 audios revisados", self.window.scan_status_label.text())
            self.assertIn("1 disponibles", self.window.scan_status_label.text())
            self.window.scan_cancel_button.click()
            self.wait_until(lambda: self.window.issabel_match_worker is None)
        self.assertEqual(len(self.window.call_records), 1)
        self.assertEqual(self.window.detected_audio_files, (audio,))
        self.assertTrue(self.window.analyze_button.isEnabled())
        self.assertTrue(self.window.scan_cancel_button.isHidden())
        self.assertIn("Búsqueda detenida", self.window.scan_status_label.text())

    def test_failed_search_replaces_updating_screen_with_persistent_error(self):
        self.window._prepare_base_refresh()
        with patch("app.ui.views.main_window.match_and_download_remote_audio",
                   side_effect=RuntimeError("Se agotó el tiempo de conexión")):
            self.window._start_issabel_match()
            self.wait_until(lambda: self.window.issabel_match_worker is None)
        self.assertIn("No se pudo completar", self.window.empty_detail_title.text())
        self.assertIn("tiempo de conexión", self.window.empty_detail_text.text())
        self.assertIn("tiempo de conexión", self.window.scan_status_label.text())
        self.assertTrue(self.window.analyze_button.isEnabled())

    def test_error_after_download_preserves_usable_partial_result(self):
        audio = self.make_audio()

        def downloader(*_args, progress_callback, **_kwargs):
            progress_callback({"paths": [str(audio)], "candidate_count": 2, "match_count": 1})
            raise RuntimeError("El servidor cerró la conexión")

        self.window._prepare_base_refresh()
        with patch("app.ui.views.main_window.match_and_download_remote_audio", side_effect=downloader):
            self.window._start_issabel_match()
            self.wait_until(lambda: self.window.issabel_match_worker is None)
        self.assertEqual(len(self.window.call_records), 1)
        self.assertEqual(self.window.detail_pages.currentIndex(), 1)
        self.assertIn("servidor cerró", self.window.scan_status_label.text())

    def test_old_base_progress_and_completion_are_ignored(self):
        audio = self.make_audio()
        self.window._prepare_base_refresh()
        payload = {"base_path": self.root / "old-base.xlsx", "paths": [audio],
                   "records": [call_record_from_audio(audio, 1)],
                   "candidate_count": 10, "match_count": 1}
        self.window._issabel_match_progress(payload)
        self.window._issabel_match_succeeded(payload)
        self.assertEqual(self.window.call_records, [])
        self.assertEqual(self.window.empty_detail_title.text(), "Actualizando resultados…")

    def test_further_download_does_not_restart_selected_audio(self):
        first, second = self.make_audio(), self.make_audio("120001")
        self.window._append_remote_records([call_record_from_audio(first, 1)])
        current = self.window.current_call
        with patch.object(self.window, "_show_call") as show:
            self.window._append_remote_records([call_record_from_audio(second, 2)])
        show.assert_not_called()
        self.assertIs(self.window.current_call, current)
        self.assertEqual(len(self.window.call_records), 2)


if __name__ == "__main__":
    unittest.main()
