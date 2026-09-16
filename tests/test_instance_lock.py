"""Mutex de instancia única y arranque sin tocar bases de producción."""
from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app import main as app_main
from app.instance_lock import ApplicationInstanceLock, INSTANCE_MUTEX


class InstanceLockTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Named mutex de Windows")
    def test_only_one_handle_holder_and_reacquire_after_release(self):
        name = "Sentry.Test." + uuid.uuid4().hex
        first = ApplicationInstanceLock(name)
        second = ApplicationInstanceLock(name)
        try:
            self.assertTrue(first.acquire())
            self.assertTrue(first.acquire(), "Adquirir dos veces el mismo objeto no debe filtrar handles.")
            self.assertFalse(second.acquire())
            first.release()
            first.release()
            self.assertTrue(second.acquire())
            self.assertFalse(first.acquire())
        finally:
            first.release()
            second.release()

    @unittest.skipUnless(sys.platform == "win32", "Named mutex de Windows")
    def test_other_process_cannot_start_until_current_releases(self):
        name = "Sentry.Test." + uuid.uuid4().hex
        first = ApplicationInstanceLock(name)
        script = (
            "import sys; from app.instance_lock import ApplicationInstanceLock; "
            "lock=ApplicationInstanceLock(sys.argv[1]); "
            "print(lock.acquire()); lock.release(); "
            "assert 'app.database' not in sys.modules"
        )
        def probe():
            result = subprocess.run(
                [sys.executable, "-c", script, name], cwd=Path(__file__).resolve().parents[1],
                text=True, capture_output=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()
        try:
            self.assertTrue(first.acquire())
            self.assertEqual(probe(), "False")
            first.release()
            self.assertEqual(probe(), "True")
        finally:
            first.release()

    def test_name_is_the_installer_contract(self):
        self.assertEqual(INSTANCE_MUTEX, "Ecuaconexion.Sentry")


class MainInstanceGuardTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.qt_application = self.stack.enter_context(patch("PySide6.QtWidgets.QApplication"))
        self.app = self.qt_application.instance.return_value
        self.app.exec.return_value = 17
        self.stack.enter_context(patch("PySide6.QtGui.QIcon"))
        self.message = self.stack.enter_context(patch("PySide6.QtWidgets.QMessageBox"))
        self.lock_type = self.stack.enter_context(patch.object(app_main, "ApplicationInstanceLock"))
        self.lock = self.lock_type.return_value
        self.lock.acquire.return_value = True
        self.events = []
        self.lock.acquire.side_effect = lambda: self.events.append("acquire") or True
        self.lock.release.side_effect = lambda: self.events.append("release")
        self.window = Mock()
        self.window_factory = Mock(side_effect=lambda: self.events.append("window") or self.window)
        fake_theme = ModuleType("app.ui.theme")
        fake_theme.apply_app_theme = Mock()
        fake_main_window = ModuleType("app.ui.views.main_window")
        fake_main_window.SentryWindow = self.window_factory
        fake_main_window.install_ui_font = Mock()
        self.stack.enter_context(patch.dict(sys.modules, {
            "app.ui.theme": fake_theme, "app.ui.views.main_window": fake_main_window,
        }))

    def test_acquires_before_window_and_releases_after_event_loop(self):
        self.app.exec.side_effect = lambda: self.events.append("exec") or 17
        self.assertEqual(app_main.main(), 17)
        self.assertEqual(self.events, ["acquire", "window", "exec", "release"])
        self.window.showMaximized.assert_called_once()

    def test_existing_instance_never_imports_main_window_or_database(self):
        self.lock.acquire.side_effect = None
        self.lock.acquire.return_value = False
        original_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name in {"app.ui.views.main_window", "app.database"}:
                raise AssertionError("Una segunda instancia no puede importar ni abrir la base.")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            self.assertEqual(app_main.main(), 0)
        self.message.information.assert_called_once()
        self.window_factory.assert_not_called()
        self.app.exec.assert_not_called()
        self.lock.release.assert_called_once()

    def test_mutex_access_failure_aborts_without_database(self):
        self.lock.acquire.side_effect = OSError("mutex denied")
        self.assertEqual(app_main.main(), 1)
        self.message.critical.assert_called_once()
        self.window_factory.assert_not_called()
        self.lock.release.assert_called_once()

    def test_database_initialization_failure_releases_mutex(self):
        self.window_factory.side_effect = RuntimeError("base incompatible")
        self.assertEqual(app_main.main(), 1)
        self.message.critical.assert_called_once()
        self.app.exec.assert_not_called()
        self.lock.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
