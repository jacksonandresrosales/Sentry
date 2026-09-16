"""Flujos de actualización Qt sin red, instaladores reales ni datos del cliente."""
from contextlib import ExitStack, closing
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from app.services.app_updates import ReleaseInfo, REPOSITORY, UpdateCancelled
from app.ui import update_controller as controller
from app.ui.views.main_window import SentryWindow


class UpdateUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.storage = self.root / "client"
        self.storage.mkdir()
        self.program = self.root / "program"
        self.program.mkdir()
        (self.program / "Sentry.exe").write_bytes(b"MZ-fake-app-never-executed")
        self.stack = ExitStack()
        self.stack.enter_context(patch.dict(os.environ, {"SENTRY_DISABLE_UPDATE_CHECK": "1"}))
        self.stack.enter_context(patch.object(controller, "APP_STORAGE_ROOT", self.storage))
        self.window = SentryWindow(self.storage / "client.db")
        self.panel = self.window.updates_panel
        self.stack.enter_context(patch.object(controller.sys, "frozen", True, create=True))
        self.stack.enter_context(patch.object(controller.sys, "executable", str(self.program / "Sentry.exe")))
        self.launch = self.stack.enter_context(patch.object(controller, "launch_installer"))
        self.question = self.stack.enter_context(patch.object(controller.QMessageBox, "question", return_value=QMessageBox.StandardButton.No))
        self.window.config_directory.setText(str(self.root / "recordings"))
        self.window.keywords_input.setText("demanda, denuncia")
        version = "9.0.0"
        name = f"Sentry_Setup_{version}.exe"
        self.release = ReleaseInfo(
            version, "v" + version, "Sentry de prueba", "Notas de prueba",
            f"https://github.com/{REPOSITORY}/releases/tag/v{version}", name,
            f"https://github.com/{REPOSITORY}/releases/download/v{version}/{name}", 32, "a" * 64,
        )
        self.downloaded = self.storage / self.release.asset_name
        self.downloaded.write_bytes(b"MZ-fake-installer-never-executed")

    def tearDown(self):
        if self.panel.worker is not None:
            self.panel.worker.requestInterruption()
            self.wait_for_worker()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.stack.close()
        self.temporary.cleanup()

    def wait_for_worker(self):
        deadline = time.monotonic() + 10
        while self.panel.worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertIsNone(self.panel.worker, "El hilo de actualización no terminó.")

    def ready_to_install(self):
        self.panel.release = self.release
        self.panel.downloaded = self.downloaded

    def test_check_download_and_ready_state_never_install_automatically(self):
        with patch.object(controller, "check_for_update", return_value=self.release) as check:
            self.panel.check(manual=True)
            self.wait_for_worker()
        self.assertEqual(self.panel.release, self.release)
        self.assertIn("Disponible", self.panel.status.text())
        self.assertTrue(self.panel.install_button.isEnabled())
        self.assertEqual(self.panel.install_button.text(), "Descargar actualización")
        self.assertIsNone(self.panel.downloaded)
        self.assertIn("updates_last_checked", self.window.database.settings())
        self.assertIn("cancel", check.call_args.kwargs)
        self.launch.assert_not_called()
        self.question.assert_not_called()

        def download(release, folder, *, progress, cancel):
            self.assertEqual(release, self.release)
            self.assertEqual(folder, self.storage / "updates")
            self.assertFalse(cancel())
            progress(16, 32)
            return self.downloaded

        with patch.object(controller, "download_update", side_effect=download):
            self.panel._action()
            self.wait_for_worker()
        self.assertEqual(self.panel.downloaded, self.downloaded)
        self.assertEqual(self.panel.install_button.text(), "Instalar y reiniciar")
        self.assertIn("Descarga verificada", self.panel.status.text())
        self.launch.assert_not_called()
        self.question.assert_not_called()

    def test_current_version_clears_stale_update_and_automatic_can_be_disabled(self):
        self.ready_to_install()
        with patch.object(controller, "check_for_update", return_value=None):
            self.panel.check()
            self.wait_for_worker()
        self.assertIsNone(self.panel.release)
        self.assertIsNone(self.panel.downloaded)
        self.assertFalse(self.panel.install_button.isEnabled())
        self.panel.automatic.setChecked(False)
        self.assertEqual(self.window.database.settings()["updates_enabled"], "0")
        with patch.object(self.panel, "check") as check:
            self.panel._automatic_check()
            check.assert_not_called()

    def test_active_analysis_search_base_or_export_blocks_install_before_confirmation(self):
        self.ready_to_install()
        for name in ("analysis_worker", "local_scan_worker", "issabel_match_worker", "winscp_worker",
                     "remote_search_worker", "report_export_worker"):
            with self.subTest(worker=name), patch.object(self.window, name, object()):
                self.panel._action()
                self.assertIsNone(self.panel.worker)
                self.assertIn("Termina o detén", self.panel.status.text())
        with patch.object(self.window.bases_page, "busy", True):
            self.panel._action()
            self.assertIsNone(self.panel.worker)
        with patch.object(self.window.reports_page, "worker", object()):
            self.panel._action()
            self.assertIsNone(self.panel.worker)
        self.question.assert_not_called()
        self.launch.assert_not_called()

    def test_user_declining_install_does_not_backup_or_launch(self):
        self.ready_to_install()
        with patch.object(self.window.database, "backup_to") as backup:
            self.panel._action()
            backup.assert_not_called()
        self.question.assert_called_once()
        self.launch.assert_not_called()
        self.assertIsNone(self.panel.worker)
        self.assertTrue(self.window.isEnabled())

    def test_work_started_while_confirmation_is_open_blocks_install(self):
        self.ready_to_install()

        def answer(*_args):
            self.window.analysis_worker = object()
            return QMessageBox.StandardButton.Yes

        self.question.side_effect = answer
        try:
            with patch.object(self.panel, "_start") as start:
                self.panel._action()
                start.assert_not_called()
            self.assertIn("Comenzó otra tarea", self.panel.status.text())
            self.assertTrue(self.window.isEnabled())
            self.launch.assert_not_called()
        finally:
            self.window.analysis_worker = None

    def test_saving_current_settings_failure_aborts_before_backup(self):
        self.ready_to_install()
        self.question.return_value = QMessageBox.StandardButton.Yes
        with patch.object(self.window.database, "save_settings", side_effect=OSError("ajustes sin espacio")), \
                patch.object(self.panel, "_start") as start:
            self.panel._action()
            start.assert_not_called()
        self.assertIn("No se pudieron guardar los ajustes", self.panel.status.text())
        self.assertTrue(self.window.isEnabled())
        self.launch.assert_not_called()

    def test_backup_failure_never_launches_and_keeps_database_usable(self):
        self.ready_to_install()
        self.question.return_value = QMessageBox.StandardButton.Yes
        with patch.object(self.window.database, "backup_to", side_effect=OSError("sin espacio para respaldo")):
            self.panel._action()
            self.wait_for_worker()
        self.assertIn("sin espacio para respaldo", self.panel.status.text())
        self.assertTrue(self.window.isEnabled())
        self.assertEqual(self.window.database.settings()["keywords"], "demanda, denuncia")
        self.launch.assert_not_called()
        self.assertFalse(self.panel.install_started)

    def test_successful_verified_backup_precedes_installer_and_window_close(self):
        self.ready_to_install()
        self.question.return_value = QMessageBox.StandardButton.Yes
        events = []
        original_backup = self.window.database.backup_to

        def backup(target, *, cancel):
            self.assertNotEqual(threading.get_ident(), main_thread)
            result = original_backup(target, cancel=cancel)
            with closing(sqlite3.connect(result)) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("SELECT value FROM app_settings WHERE key='keywords'").fetchone()[0], "demanda, denuncia")
            events.append("backup")
            return result

        main_thread = threading.get_ident()
        self.launch.side_effect = lambda *args, **kwargs: events.append("launch")
        with patch.object(self.window.database, "backup_to", side_effect=backup), \
                patch.object(self.window, "close", side_effect=lambda: events.append("close")):
            self.panel._action()
            self.assertFalse(self.window.isEnabled())
            self.wait_for_worker()
        self.assertEqual(events, ["backup", "launch", "close"])
        self.assertTrue(self.panel.install_started)
        self.launch.assert_called_once_with(
            self.downloaded, self.release, self.program, self.storage / "updates" / "installation.log",
            parent_pid=os.getpid(),
        )
        self.assertEqual(len(list((self.storage / "backups").glob("*.db"))), 1)

    def test_failed_launch_retains_backup_and_open_application(self):
        self.ready_to_install()
        self.question.return_value = QMessageBox.StandardButton.Yes
        self.launch.side_effect = OSError("instalador bloqueado")
        with patch.object(self.window, "close") as close:
            self.panel._action()
            self.wait_for_worker()
            close.assert_not_called()
        self.assertTrue(self.window.isEnabled())
        self.assertFalse(self.panel.install_started)
        self.assertIn("Respaldo conservado", self.panel.status.text())
        self.assertEqual(len(list((self.storage / "backups").glob("*.db"))), 1)

    def test_database_in_install_directory_blocks_unsafe_update(self):
        self.ready_to_install()
        with patch.object(self.window.database, "path", self.program / "unsafe.db"):
            self.panel._action()
        self.assertIn("migrarlos", self.panel.status.text())
        self.question.assert_not_called()
        self.launch.assert_not_called()

    def test_close_cancels_worker_waits_for_cleanup_and_never_installs(self):
        entered = threading.Event()
        cancelled = threading.Event()

        def checking(_current, *, cancel):
            entered.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if cancel():
                    cancelled.set()
                    raise UpdateCancelled("Cancelado en prueba")
                time.sleep(0.005)
            raise AssertionError("No se recibió la cancelación del cierre.")

        with patch.object(controller, "check_for_update", side_effect=checking):
            self.panel.check()
            self.assertTrue(entered.wait(2))
            self.assertFalse(self.window.close())
            self.assertTrue(self.panel.closing)
            self.wait_for_worker()
        self.assertTrue(cancelled.is_set())
        self.launch.assert_not_called()
        self.assertFalse(self.panel.request_close())

    def test_query_error_leaves_controls_available_and_throttles_automatic_retry(self):
        with patch.object(controller, "check_for_update", side_effect=OSError("sin red")):
            self.panel.check()
            self.wait_for_worker()
        self.assertIn("sin red", self.panel.status.text())
        self.assertTrue(self.panel.check_button.isEnabled())
        self.assertIn("updates_last_checked", self.window.database.settings())
        with patch.object(self.panel, "check") as check:
            self.panel._automatic_check()
            check.assert_not_called()
        self.launch.assert_not_called()

    def test_rejected_close_does_not_leave_updater_stuck_in_closing_state(self):
        entered = threading.Event()

        def checking(_current, *, cancel):
            entered.set()
            deadline = time.monotonic() + 5
            while not cancel() and time.monotonic() < deadline:
                time.sleep(0.005)
            raise UpdateCancelled("Cancelado en prueba")

        with patch.object(controller, "check_for_update", side_effect=checking), \
                patch.object(self.window.bases_page, "busy", True):
            self.panel.check()
            self.assertTrue(entered.wait(2))
            self.assertFalse(self.window.close())
            self.wait_for_worker()
        # El cierre diferido se rechazó por la base activa; la ventana puede usarse.
        self.assertFalse(self.panel.closing)
        with patch.object(controller, "check_for_update", return_value=self.release), \
                patch.object(self.window, "close") as close:
            self.panel.check()
            self.wait_for_worker()
            close.assert_not_called()
        self.assertEqual(self.panel.release, self.release)
        self.launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
