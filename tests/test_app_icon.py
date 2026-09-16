from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app import main as app_main


REPOSITORY = Path(__file__).resolve().parents[1]
ICON_RELATIVE_PATH = Path("app/ui/assets/sentry-app-icon.ico")


class AppIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_source_icon_path_resolves_to_existing_multiresolution_asset(self):
        with (
            patch.object(app_main, "sys", SimpleNamespace()),
            patch.object(app_main, "__file__", str(REPOSITORY / "app/main.py")),
        ):
            path = app_main.application_icon_path()
        self.assertEqual(path, REPOSITORY / ICON_RELATIVE_PATH)
        self.assertTrue(path.is_file())

    def test_frozen_icon_path_uses_bundle_root_even_when_main_is_at_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary)
            with (
                patch.object(sys, "_MEIPASS", str(bundle), create=True),
                patch.object(app_main, "__file__", str(bundle / "main.py")),
            ):
                self.assertEqual(app_main.application_icon_path(), bundle / ICON_RELATIVE_PATH)

    def test_qicon_contains_small_native_window_sizes(self):
        with patch.object(sys, "_MEIPASS", str(REPOSITORY), create=True):
            icon = QIcon(str(app_main.application_icon_path()))
        self.assertFalse(icon.isNull())
        sizes = {(size.width(), size.height()) for size in icon.availableSizes()}
        self.assertIn((16, 16), sizes)
        self.assertIn((32, 32), sizes)
        self.assertFalse(icon.pixmap(16, 16).isNull())
        self.assertFalse(icon.pixmap(32, 32).isNull())

    @unittest.skipUnless(sys.platform == "win32", "Native Windows icon handles required")
    def test_hidden_native_window_has_titlebar_and_taskbar_icon_handles(self):
        # A separate process is needed: the main test suite uses Qt's offscreen
        # platform, whereas WM_GETICON requires the real Windows Qt backend.
        script = r'''
import ctypes
from ctypes import wintypes
import json
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow
from app.main import application_icon_path

app = QApplication([])
icon = QIcon(str(application_icon_path()))
assert not icon.isNull()
app.setWindowIcon(icon)
window = QMainWindow()
window.setWindowIcon(icon)
handle = int(window.winId())
app.processEvents()
send_message = ctypes.windll.user32.SendMessageW
send_message.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
send_message.restype = ctypes.c_ssize_t
print(json.dumps({
    "small": int(send_message(handle, 0x007F, 0, 0)),
    "large": int(send_message(handle, 0x007F, 1, 0)),
    "visible": window.isVisible(),
}))
window.close()
'''
        environment = {**os.environ, "QT_QPA_PLATFORM": "windows"}
        completed = subprocess.run(
            [sys.executable, "-c", script], cwd=REPOSITORY, env=environment,
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertGreater(result["small"], 0, "Title-bar icon must exist at the native Windows level.")
        self.assertGreater(result["large"], 0, "Taskbar icon must exist at the native Windows level.")
        self.assertFalse(result["visible"], "Native verification must not open a visible window.")


if __name__ == "__main__":
    unittest.main()
