"""Identidad y respaldo local de la versión publicada."""
from pathlib import Path
import sys

APP_NAME = "Sentry"
APP_VERSION = "1.0.3"
UPDATE_REPOSITORY = "jacksonandresrosales/Sentry"
APP_AUTHORS = "Jackson Ocaña y Jeremy Godoy"
APP_DESCRIPTION = (
    "Auditoría de llamadas para una atención mejor informada. "
    "Sentry reúne grabaciones, transcripciones y evidencias en un solo lugar "
    "para facilitar la revisión y el seguimiento de cada caso."
)
APP_FEATURES = (
    ("Excel de costes más claro", "El informe organiza resumen, consumo diario, solicitudes y tarifas con navegación entre hojas, gráficos, filtros, fechas y totales. Conserva los identificadores y distingue los costes sin estimación."),
    ("Limpieza de caché", "Configuración incorpora un cuadro con el tamaño aproximado y los registros de transcripciones, análisis e índice de archivos. Permite eliminar la caché y recuperar espacio conservando grabaciones, resultados, historial de costes y claves. La limpieza espera a que terminen las tareas activas; volver a procesar sin caché puede consumir API."),
    ("Validación de Deepgram", "Se comprueba la clave sin exigir acceso a la administración de proyectos ni al catálogo de modelos. Se acepta el prefijo Token al pegarla y se diferencian las credenciales rechazadas de los problemas de permisos."),
    ("Universal-2 en español", "Se corrige el error HTTP 400 al transcribir con AssemblyAI Universal-2: se omite el refuerzo de vocabulario que ese modelo solo admite en inglés. Las palabras clave siguen utilizándose para detectar alertas en el análisis posterior; Universal-3.5 Pro conserva su refuerzo de vocabulario."),
)


def license_text() -> str:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    try:
        return (root / "LICENSE").read_text(encoding="utf-8")
    except OSError:
        return "No se pudo cargar la licencia incluida con esta instalación."
