"""Identidad y respaldo local de la versión publicada."""
from pathlib import Path
import sys

APP_NAME = "Sentry"
APP_VERSION = "1.0.2"
UPDATE_REPOSITORY = "jacksonandresrosales/Sentry"
APP_AUTHORS = "Jackson Ocaña y Jeremy Godoy"
APP_DESCRIPTION = (
    "Auditoría de llamadas para una atención mejor informada. "
    "Sentry reúne grabaciones, transcripciones y evidencias en un solo lugar "
    "para facilitar la revisión y el seguimiento de cada caso."
)
APP_FEATURES = (
    ("Transcripción con AssemblyAI", "Puedes elegir Universal-2 o Universal-3.5 Pro. Las claves de cada proveedor se guardan cifradas con Windows."),
    ("Costes API renovados", "Consulta costes y consumo por modelo, gráficos por día, semana o mes y filtros por periodo, servicio y proveedor. El Excel incluye todas las solicitudes del periodo."),
    ("Audio y facturación", "El recorte elimina silencios digitales conservando la voz y los tiempos originales. Se evitan reenvíos automáticos de audio tras errores que podrían duplicar cargos."),
    ("Fiabilidad y experiencia de uso", "Se corrige la reutilización de transcripciones entre modelos y proveedores, se afinan las estimaciones de costes y se mejora la lectura y los controles en los temas claro y oscuro."),
)


def license_text() -> str:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    try:
        return (root / "LICENSE").read_text(encoding="utf-8")
    except OSError:
        return "No se pudo cargar la licencia incluida con esta instalación."
