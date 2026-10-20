"""La limpieza libera caché regenerable y conserva los datos operativos."""
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget

from app.database import Database
from app.ui.cache_dialog import CacheDialog
from app.ui.views.main_window import SentryWindow


class CacheDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.database = Database(Path(self.folder.name) / "audit.db")

    def seed_cache(self):
        self.database.cache_set("transcription_cache", "audio", {"text": "Transcripción ñ" * 30_000})
        self.database.cache_set("analysis_cache", "analysis", {"summary": "Resultado"})
        self.database.save_file_digest(Path(self.folder.name) / "audio.wav", 8, 123, "abc")

    def test_size_cleanup_and_compaction_preserve_all_operational_tables(self):
        audio = Path(self.folder.name) / "audio.wav"
        audio.write_bytes(b"original")
        self.seed_cache()
        with self.database.connect() as connection:
            connection.execute("""INSERT INTO calls
                (filename,file_path,transcript,transcript_json,summary,reviewed)
                VALUES (?,?, 'Transcripción guardada', '{"text":"evidencia"}', 'Resumen guardado',1)""",
                (audio.name, str(audio)))
            connection.execute("INSERT INTO app_settings(key,value) VALUES ('theme','dark')")
            connection.execute("""INSERT INTO api_credentials(service,provider,model,encrypted_key)
                VALUES ('analysis','groq','openai/gpt-oss-120b','encrypted-test')""")
            connection.execute("""INSERT INTO api_provider_keys(service,provider,encrypted_key)
                VALUES ('analysis','groq','encrypted-test')""")
            connection.execute("""INSERT INTO api_usage(service,provider,model,cost_usd)
                VALUES ('analysis','groq','openai/gpt-oss-120b',.25)""")
        usage = self.database.processing_cache_usage()
        self.assertEqual({item["entries"] for item in usage.values()}, {1})
        with self.database.connect() as connection:
            value = connection.execute('SELECT transcript_json FROM transcription_cache').fetchone()[0]
            self.assertGreaterEqual(usage["transcription_cache"]["bytes"], len(value.encode('utf-8')))
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                      if row[0] not in usage and not row[0].startswith("sqlite_")]
            before = {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table}')]
                      for table in tables}
        before_size = self.database.path.stat().st_size
        self.assertEqual(self.database.clear_processing_cache(), "")
        self.assertEqual(audio.read_bytes(), b"original")
        self.assertLess(self.database.path.stat().st_size, before_size)
        self.assertTrue(all(item == {"entries": 0, "bytes": 0}
                            for item in self.database.processing_cache_usage().values()))
        with self.database.connect() as connection:
            after = {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table}')]
                     for table in tables}
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(before, after)

    def test_delete_failure_rolls_back_every_cache(self):
        self.seed_cache()
        with self.database.connect() as connection:
            connection.execute("""CREATE TRIGGER fail_cleanup BEFORE DELETE ON analysis_cache
                BEGIN SELECT RAISE(ABORT,'test failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.clear_processing_cache()
        self.assertEqual({item['entries'] for item in self.database.processing_cache_usage().values()}, {1})

    def test_compaction_failure_reports_that_the_cache_was_already_deleted(self):
        self.seed_cache()
        connection = self.database.connect()
        with patch.object(self.database, 'connect', side_effect=[connection, sqlite3.OperationalError('disk full')]):
            message = self.database.clear_processing_cache()
        self.assertIn('no se pudo compactar', message)
        self.assertTrue(all(item['entries'] == 0 for item in self.database.processing_cache_usage().values()))


class CacheDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.folder.name) / "audit.db")
        self.database.cache_set("analysis_cache", "test", {"summary": "Ejemplo"})
        self.parent = QWidget()
        self.parent.database, self.parent.theme = self.database, 'light'
        self.busy = False
        self.parent.updates_panel = SimpleNamespace(worker=None, _work_is_active=lambda: self.busy)
        self.dialog = CacheDialog(self.parent)
        self.dialog.show()
        self.wait_for(lambda: self.dialog.usage is not None and self.dialog.worker is None)

    def tearDown(self):
        if self.dialog.worker is not None:
            self.dialog.worker.wait(35_000)
            self.app.processEvents()
        self.dialog.reject()
        self.parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.folder.cleanup()

    def wait_for(self, condition):
        deadline = time.monotonic() + 10
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertTrue(condition(), 'La tarea de caché no terminó a tiempo.')

    def test_clear_updates_sizes_and_empty_state(self):
        self.assertTrue(self.dialog.clear_button.isEnabled())
        self.assertEqual(self.dialog.counts['analysis_cache'].text(), '1 registro')
        self.dialog.clear_button.click()
        self.wait_for(lambda: self.dialog.worker is None)
        self.assertEqual(self.dialog.total.text(), 'Tamaño aproximado: 0 B')
        self.assertEqual(self.dialog.status.text(), 'Caché eliminada.')
        self.assertFalse(self.dialog.clear_button.isEnabled())
        self.assertTrue(self.dialog.close_button.isEnabled())

    def test_active_tasks_block_cleanup(self):
        self.busy = True
        self.dialog._update_actions()
        self.assertFalse(self.dialog.clear_button.isEnabled())
        with patch.object(self.database, 'clear_processing_cache') as clear:
            self.dialog._clear()
            clear.assert_not_called()
        self.busy = False
        self.dialog._update_actions()
        self.assertTrue(self.dialog.clear_button.isEnabled())

    def test_read_failure_can_be_retried_without_deleting(self):
        self.dialog.usage = None
        with patch.object(self.database, 'processing_cache_usage', side_effect=sqlite3.OperationalError('locked')):
            self.dialog._start(False)
            self.wait_for(lambda: self.dialog.worker is None)
        self.assertIn('locked', self.dialog.status.text())
        self.assertEqual(self.dialog.clear_button.text(), 'Reintentar')
        self.dialog.clear_button.click()
        self.wait_for(lambda: self.dialog.worker is None)
        self.assertEqual(self.dialog.usage['analysis_cache']['entries'], 1)

    def test_config_button_opens_only_the_cache_dialog(self):
        window = SentryWindow(Path(self.folder.name) / 'window.db')
        try:
            with patch('app.ui.views.main_window.CacheDialog') as dialog:
                window.clear_cache_button.click()
                dialog.assert_called_once_with(window)
                dialog.return_value.exec.assert_called_once()
                dialog.return_value.deleteLater.assert_called_once()
                self.assertIsNone(window.cache_dialog)
        finally:
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
