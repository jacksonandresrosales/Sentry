from __future__ import annotations

import sys
import sqlite3
from pathlib import Path


def main() -> int:
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication, QMessageBox
        from app.ui.views.main_window import SentryWindow, install_ui_font
    except ModuleNotFoundError as exc:
        if exc.name in {"PySide6", "openpyxl", "xlsxwriter"}:
            raise SystemExit(
                f"Falta {exc.name}. Ejecuta: python -m pip install -r requirements.txt"
            ) from exc
        raise

    app = QApplication(sys.argv)
    app.setApplicationName("Sentry")
    app.setOrganizationName("Ecuaconexión")
    icon_path = Path(__file__).resolve().parent / "ui" / "assets" / "sentry-app-icon.ico"
    window_icon = QIcon(str(icon_path))
    app.setWindowIcon(window_icon)
    app.setStyle("Fusion")
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


if __name__ == "__main__":
    raise SystemExit(main())
