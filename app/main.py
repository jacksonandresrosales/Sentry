from __future__ import annotations

import sys
import sqlite3
import ctypes
from pathlib import Path
from app.about import APP_NAME, APP_VERSION
from app.instance_lock import ApplicationInstanceLock


def application_icon_path() -> Path:
    """La entrada congelada vive en la raíz; los recursos siguen dentro de app."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / "app" / "ui" / "assets" / "sentry-app-icon.ico"


def main() -> int:
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Ecuaconexion.Sentry")
        except (AttributeError, OSError):
            pass
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ModuleNotFoundError as exc:
        if exc.name in {"PySide6", "openpyxl", "xlsxwriter", "requests"}:
            raise SystemExit(
                f"Falta {exc.name}. Ejecuta: python -m pip install -r requirements.txt"
            ) from exc
        raise

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("Ecuaconexión")
    # El mismo ICO multirresolución para el EXE, título y barra de tareas.
    window_icon = QIcon(str(application_icon_path()))
    app.setWindowIcon(window_icon)
    app.setStyle("Fusion")
    instance_lock = ApplicationInstanceLock()
    try:
        try:
            acquired = instance_lock.acquire()
        except OSError as exc:
            QMessageBox.critical(None, "No se pudo iniciar Sentry",
                                 f"No se pudo comprobar si Sentry ya está abierto.\n\n{exc}")
            return 1
        if not acquired:
            QMessageBox.information(None, "Sentry ya está abierto",
                                    "Sentry ya está abierto o se está actualizando. "
                                    "Utiliza la ventana existente o espera a que termine la instalación.")
            return 0
        # main_window importa Database y prepara su ruta por defecto: nunca importarlo
        # antes de la exclusión, ni siquiera al intentar abrir una segunda ventana.
        from app.ui.theme import apply_app_theme
        from app.ui.views.main_window import SentryWindow, install_ui_font
        apply_app_theme(app, "light")
        install_ui_font(app)
        try:
            window = SentryWindow()
        except (sqlite3.Error, OSError, RuntimeError) as exc:
            QMessageBox.critical(None, "No se pudo iniciar Sentry",
                                 f"No se pudo preparar la base de datos local. Revisa permisos y espacio en disco.\n\n{exc}")
            return 1
        window.setWindowIcon(window_icon)
        window.showMaximized()
        return app.exec()
    except ModuleNotFoundError as exc:
        if exc.name in {"PySide6", "openpyxl", "xlsxwriter", "requests"}:
            raise SystemExit(
                f"Falta {exc.name}. Ejecuta: python -m pip install -r requirements.txt"
            ) from exc
        raise
    finally:
        instance_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
