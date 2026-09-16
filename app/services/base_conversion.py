"""El mismo motor del script, con registro transaccional de sus resultados."""
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
import re
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile
import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from app.database import Database
from scripts.transformar_base import (
    BaseError, HEADERS, cell_text, classify_id, convert_files, normalize, normalize_lucid_phone,
)


# Lucid utiliza grabaciones salientes desde su cambio de sistema en septiembre.
LUCID_AUDIO_SINCE = "20260901"


def normalize_phone_number(value) -> str:
    try:
        return normalize_lucid_phone(value)
    except BaseError:
        # Los archivos Issabel antiguos admitían identificadores cortos u otros
        # valores no nacionales. Conserva esa lectura permisiva para el historial.
        return re.sub(r"\D", "", cell_text(value))


def normalize_call_date(value) -> str:
    """Convierte la fecha de Hoja1 a AAAAMMDD, como aparece en el audio."""
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = cell_text(value).strip()
    for pattern in (
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
        "%Y%m%d%H%M%S", "%Y%m%d",
    ):
        try:
            return datetime.strptime(text, pattern).strftime("%Y%m%d")
        except ValueError:
            continue
    match = re.search(r"\b(20\d{2})[-/]?(0[1-9]|1[0-2])[-/]?([0-2]\d|3[01])\b", text)
    return "".join(match.groups()) if match else ""


@dataclass(frozen=True)
class BaseAudioIndex:
    phone_dates: dict[str, frozenset[str]]
    names: dict[str, str] = field(default_factory=dict)
    source_system: str = "issabel"

    @property
    def phones(self) -> set[str]:
        return set(self.phone_dates)

    @property
    def dates(self) -> set[str]:
        return {day for days in self.phone_dates.values() for day in days}


def hoja1_column_layout(sheet) -> tuple[str, dict[str, int]]:
    """Reconoce bases transformadas por sus columnas, incluso si se reordenaron."""
    headers: dict[str, int] = {}
    for index, row_value in enumerate(next(sheet.iter_rows(max_row=1, values_only=True), ())):
        key = normalize(row_value)
        if not key:
            continue
        if key in headers:
            raise ValueError(f"Hoja1 contiene una columna repetida: {cell_text(row_value)}.")
        headers[key] = index
    if set(headers) in ({"telefono", "nombre", "estado"}, {"telefono", "nombre", "id", "estado"}):
        return "lucid", headers
    missing = [title for title in HEADERS if normalize(title) not in headers]
    if missing:
        raise ValueError(
            "Hoja1 no tiene el formato transformado de Issabel o Lucid. "
            "Para Lucid se requieren Teléfono, Nombre, ID y Estado (también se admite el formato anterior sin ID); "
            "para Issabel faltan: " + ", ".join(missing) + "."
        )
    return "issabel", headers


def load_hoja1_audio_index(path: Path) -> BaseAudioIndex:
    """Lee teléfonos, fechas y nombres de Hoja1 para relacionarlos con los audios."""
    path = Path(path).resolve()
    if not path.is_file() or path.suffix.casefold() != ".xlsx":
        raise ValueError("Selecciona un Excel transformado que todavía exista.")
    try:
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except (OSError, BadZipFile, InvalidFileException, ParseError) as exc:
        raise ValueError(f"No se pudo leer el Excel transformado: {exc}") from exc
    try:
        if "Hoja1" not in book.sheetnames:
            raise ValueError("El Excel seleccionado no contiene la hoja Hoja1.")
        sheet = book["Hoja1"]
        source_system, headers = hoja1_column_layout(sheet)
        needed = ("telefono", "nombre") if source_system == "lucid" else ("telefono", "nombre", "fechayhora")
        maximum = max(headers[key] for key in needed) + 1
        phone_dates: dict[str, set[str]] = {}
        names: dict[str, str] = {}
        for values in sheet.iter_rows(min_row=2, max_col=maximum, values_only=True):
            phone = normalize_phone_number(values[headers["telefono"]])
            if not phone:
                continue
            phone_dates.setdefault(phone, set())
            name = cell_text(values[headers["nombre"]])
            if name and phone not in names:
                names[phone] = name
            day = normalize_call_date(values[headers["fechayhora"]]) if source_system == "issabel" else ""
            if day:
                phone_dates[phone].add(day)
        return BaseAudioIndex(
            {phone: frozenset(days) for phone, days in phone_dates.items()},
            names,
            source_system,
        )
    finally:
        book.close()


def load_hoja1_phones(path: Path) -> set[str]:
    """Lee como identificadores los teléfonos únicos de Hoja1."""
    return load_hoja1_audio_index(path).phones


def process_bases(database: Database, paths: list[Path], output_folder: Path, **options):
    options.setdefault("source_system", "issabel")
    if str(options["source_system"]).strip().casefold() == "lucid":
        options.setdefault("entity_filter", "people")
    job_id = database.start_job(paths, {**options, "output_folder": str(output_folder)})
    result = None
    try:
        result = convert_files(paths, output_folder=output_folder, **options)
        book = openpyxl.load_workbook(result.output, read_only=True, data_only=True)
        try:
            sheet = book["Hoja1"]
            source_system, headers = hoja1_column_layout(sheet)
            rows = sheet.iter_rows(min_row=2, max_col=max(headers.values()) + 1, values_only=True)
            if source_system == "lucid":
                # Mantiene el historial común sin interpretar Nombre como estado
                # de llamada ni Estado como agente, ni inventar fechas o cédulas.
                records = (
                    (cell_text(row[headers["telefono"]]), "", "", "", "",
                     cell_text(row[headers["nombre"]]),
                     cell_text(row[headers["id"]]) if "id" in headers else "",
                     classify_id(cell_text(row[headers["id"]])) if "id" in headers else "",
                     cell_text(row[headers["estado"]]), "V", "")
                    for row in rows
                )
            else:
                records = (tuple(row[headers[normalize(title)]] for title in HEADERS) for row in rows)
            database.finish_job(job_id, result, records)
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
