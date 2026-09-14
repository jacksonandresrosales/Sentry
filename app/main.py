from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
        from app.ui.views.main_window import SentryWindow, install_ui_font
    except ModuleNotFoundError as exc:
        if exc.name == "PySide6":
            raise SystemExit(
                "PySide6 no está instalado. Ejecuta: python -m pip install -r requirements.txt"
            ) from exc
        raise

    app = QApplication(sys.argv)
    app.setApplicationName("Sentry")
    app.setOrganizationName("Ecuaconexión")
    icon_path = Path(__file__).resolve().parent / "ui" / "assets" / "sentry-app-icon.png"
    app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyle("Fusion")
    install_ui_font(app)

    window = SentryWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
