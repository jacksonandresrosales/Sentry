"""Identidad y respaldo local de la versión publicada."""
from pathlib import Path
import sys

APP_NAME = "Sentry"
APP_VERSION = "0.1.0-beta.8"
UPDATE_REPOSITORY = "jacksonandresrosales/Sentry"
APP_AUTHORS = "Jackson Ocaña y Jeremy Godoy"
APP_DESCRIPTION = (
    "Auditoría de llamadas para una atención mejor informada. "
    "Sentry reúne grabaciones, transcripciones y evidencias en un solo lugar "
    "para facilitar la revisión y el seguimiento de cada caso."
)
APP_FEATURES = (
    ("Notas automáticas", "Acerca de consulta en GitHub los cambios publicados para la versión instalada."),
    ("Historial de versiones", "El selector de versión permite revisar las novedades de publicaciones anteriores."),
    ("Licencia integrada", "La licencia propietaria puede consultarse sin salir de la aplicación."),
)


def license_text() -> str:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    try:
        return (root / "LICENSE").read_text(encoding="utf-8")
    except OSError:
        return "No se pudo cargar la licencia incluida con esta instalación."
