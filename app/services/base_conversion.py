"""El mismo motor del script, con registro transaccional de sus resultados."""
from pathlib import Path
import openpyxl

from app.database import Database
from scripts.transformar_base import convert_files


def process_bases(database: Database, paths: list[Path], output_folder: Path, **options):
    job_id = database.start_job(paths, {**options, "output_folder": str(output_folder)})
    result = None
    try:
        result = convert_files(paths, output_folder=output_folder, **options)
        book = openpyxl.load_workbook(result.output, read_only=True, data_only=True)
        try:
            database.finish_job(job_id, result, book["Hoja1"].iter_rows(min_row=2, max_col=11, values_only=True))
        finally:
            book.close()
        return result
    except Exception as exc:
        # Un Excel ya publicado permanece disponible aunque falle la persistencia.
        try:
            database.fail_job(job_id, exc, result.output if result else None)
        except Exception:
            pass  # Conserva el error original, no un segundo error de SQLite.
        if result is not None:
            raise RuntimeError(f"El Excel está guardado en {result.output}, pero falló el historial: {exc}") from exc
        raise
