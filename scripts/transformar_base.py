#!/usr/bin/env python3
"""Convierte bases de llamadas CSV/XLSX al esquema del archivo NO.xlsx."""

from __future__ import annotations

import argparse
import csv
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import chain
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

import openpyxl
import xlsxwriter
from xlsxwriter.utility import xl_col_to_name


HEADERS = (
    "Teléfono", "Estado Llamada", "Agente", "Fecha y hora", "Duración(Seg)",
    "Nombre", "ID", "TIPO ID", "ESTADO", "T BASE", "N° BASE",
)
REQUIRED = tuple(HEADERS[i] for i in (0, 1, 2, 3, 4, 5, 6, 8))
DEFAULT_STATE = "ELIMINAR DE BASE DE DATOS"


class BaseError(ValueError):
    """Error de estructura o configuración de la base."""


class OutputCollisionError(BaseError):
    """Otro archivo ya ocupa el destino; nunca se reemplaza implícitamente."""


def normalize(value: object) -> str:
    """Compara encabezados sin acentos, espacios o signos de puntuación."""
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(c for c in value if c.isalnum() and not unicodedata.combining(c))


ALIASES = {
    "Teléfono": ("telefono", "telfono", "phone"),
    "Estado Llamada": ("estadollamada", "estadodelallamada"),
    "Agente": ("agente", "agent"),
    "Fecha y hora": ("fechayhora", "fechahora"),
    "Duración(Seg)": ("duracionseg", "duracinseg", "duracionsegundos"),
    "Nombre": ("nombre", "name"),
    "ID": ("id", "identificacion", "identificacin"),
    "TIPO ID": ("tipoid", "tipodeid"),
    "ESTADO": ("estado",),
    "T BASE": ("tbase", "tipobase", "tipodebase"),
    "N° BASE": ("nbase", "numerobase", "numerodebase", "numbase"),
}


def column_map(headers: list[object]) -> dict[str, int]:
    result = {}
    for index, header in enumerate(headers):
        key = normalize(header)
        for canonical, aliases in ALIASES.items():
            if key in aliases:
                if canonical in result:
                    raise BaseError(f"La columna {canonical!r} aparece más de una vez.")
                result[canonical] = index
    return result


def find_header(rows, label: str, preamble: list | None = None):
    """Omite títulos, FORMULARIO y Column1... hasta hallar la cabecera real."""
    best = set()
    for number, row in enumerate(rows, 1):
        mapping = column_map(list(row))
        if len(mapping) > len(best):
            best = set(mapping)
        if all(name in mapping for name in REQUIRED):
            return list(row), mapping, number
        if preamble is not None:
            preamble.append([cell_text(v) for v in row])
        if number >= 50:
            break
    missing = ", ".join(name for name in REQUIRED if name not in best)
    raise BaseError(f"{label}: no se encontró la cabecera en las primeras 50 filas. "
                    f"Columnas faltantes: {missing}.")


def cell_text(value: object, number_format: str = "General") -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not value.is_integer():
            return str(value)
        # Recupera ceros si el Excel tiene un formato explícito como 0000000000.
        digits = str(int(value))
        if re.fullmatch(r"0+", number_format):
            digits = digits.zfill(len(number_format))
        return digits
    return str(value).strip()


@dataclass
class Source:
    name: str
    headers: list[str]
    mapping: dict[str, int]
    rows: list[list[str]]
    header_row: int
    preamble: list[list[str]] = field(default_factory=list)
    assigned_base_number: str = ""


def read_csv(path: Path, encoding: str | None, delimiter: str | None) -> list[Source]:
    if encoding:
        text = path.read_text(encoding=encoding)
    else:
        for candidate in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = path.read_text(encoding=candidate)
                break
            except UnicodeDecodeError:
                continue
    if delimiter:
        reader = csv.reader(text.splitlines(keepends=True), delimiter=delimiter)
    else:
        try:
            dialect = csv.Sniffer().sniff(text[:131072], delimiters=",;\t|")
        except csv.Error as exc:
            raise BaseError(f"{path.name}: no se detectó el separador. Usa --separador.") from exc
        reader = csv.reader(text.splitlines(keepends=True), dialect)
    preamble = []
    headers, mapping, header_row = find_header(reader, path.name, preamble)
    rows = []
    for number, row in enumerate(reader, header_row + 1):
        if not any(str(v).strip() for v in row):
            continue
        if len(row) != len(headers):
            raise BaseError(f"{path.name}, registro {number}: tiene {len(row)} columnas; "
                            f"se esperaban {len(headers)}.")
        rows.append([cell_text(v) for v in row])
    return [Source(path.stem, [cell_text(h) for h in headers], mapping, rows, header_row, preamble)]


def read_xlsx(path: Path, sheet_name: str | None) -> list[Source]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet_name and sheet_name not in workbook.sheetnames:
            raise BaseError(f"{path.name}: no existe la hoja {sheet_name!r}.")
        sheets = [workbook[sheet_name]] if sheet_name else workbook.worksheets
        candidates = []
        for sheet in sheets:
            iterator = sheet.iter_rows()
            preamble = []
            # Conserva los objetos Cell para recuperar formatos de identificadores.
            try:
                headers, mapping, header_row = find_header(
                    (tuple(c.value for c in row) for row in iterator),
                    f"{path.name} / {sheet.title}",
                    preamble,
                )
            except BaseError:
                if sheet_name:
                    raise
                continue
            candidates.append((sheet, headers, mapping, header_row, preamble))
        raw = [entry for entry in candidates
               if any(normalize(h) in ("uniqueid", "failurecode", "plan") for h in entry[1])]
        # En NO.xlsx evita volver a importar BASE*, Validar y Hoja1 como bases originales.
        candidates = raw or candidates
        if not candidates:
            raise BaseError(f"{path.name}: ninguna hoja contiene las columnas requeridas.")
        if len(candidates) > 1 and not raw and not sheet_name:
            raise BaseError(f"{path.name}: hay varias hojas de resultados. Selecciona una con --hoja.")
        sources = []
        for sheet, headers, mapping, header_row, preamble in candidates:
            rows = []
            for row in sheet.iter_rows(min_row=header_row + 1):
                values = [cell_text(c.value, c.number_format) for c in row]
                if any(values):
                    rows.append(values)
            sources.append(Source(sheet.title, [cell_text(h) for h in headers],
                                  mapping, rows, header_row, preamble))
        return sources
    finally:
        workbook.close()


def classify_id(identifier: str) -> str:
    # Equivalente a SI(LARGO(ID)=10;"CÉDULA";SI(LARGO(ID)=13;"RUC";"" )).
    # Clasificación por longitud, no validación del dígito verificador.
    return {10: "CÉDULA", 13: "RUC"}.get(len(identifier), "")


def clean_name(name: str) -> str:
    """El modelo elimina relleno y bloques de apellidos duplicados al final."""
    words = name.split()
    while words and words[-1] == ".":
        words.pop()
    # No elimina apellidos iguales individuales (por ejemplo GARCIA GARCIA).
    # Conserva al menos dos palabras anteriores al bloque repetido.
    for width in range((len(words) - 2) // 2, 1, -1):
        if [w.casefold() for w in words[-2 * width:-width]] == [w.casefold() for w in words[-width:]]:
            words = words[:-width]
            break
    return " ".join(words)


def infer_base_number(name: str) -> str:
    match = re.search(r"(?:^|[\s_-])B\s*(\d+)(?=$|[\s_-])", name, re.IGNORECASE)
    return f"B{match.group(1)}" if match else ""


def normalize_base_number(value: str) -> str:
    match = re.fullmatch(r"B?(\d+)", value.strip(), re.IGNORECASE)
    digits = match.group(1).lstrip("0") if match else ""
    if not digits or len(digits) > 9:
        raise BaseError("N° BASE debe ser un entero positivo, como B1, B2 o 3 (máximo 9 dígitos).")
    return f"B{digits}"


def transform(source: Source, *, state: str = DEFAULT_STATE,
              call_state: str = "Success", base_type: str | None = None,
              base_number: str | None = None,
              include_ruc_third_9: bool = False) -> list[list[str]]:
    def get(row, name):
        index = source.mapping.get(name)
        return row[index] if index is not None and index < len(row) else ""

    result = []
    for row in source.rows:
        if state != "*" and normalize(get(row, "ESTADO")) != normalize(state):
            continue
        if call_state != "*" and normalize(get(row, "Estado Llamada")) != normalize(call_state):
            continue
        identifier = get(row, "ID")
        # Regla deducida comparando las ocho bases de NO con sus resultados:
        # los diez Success adicionales excluidos tienen 13 dígitos y tercero 9.
        if (not include_ruc_third_9 and len(identifier) == 13
                and identifier.isascii() and identifier.isdigit() and identifier[2] == "9"):
            continue
        output = [get(row, name) for name in HEADERS]
        output[5] = clean_name(output[5])
        output[7] = classify_id(output[6])
        output[9] = base_type if base_type is not None else get(row, "T BASE").upper() or "V"
        if output[9] not in ("V", "R"):
            raise BaseError(f"{source.name}: T BASE debe ser V (verificar) o R (remover).")
        output[10] = (normalize_base_number(base_number) if base_number is not None else
                      normalize_base_number(get(row, "N° BASE")) if get(row, "N° BASE") else
                      source.assigned_base_number or infer_base_number(source.name))
        result.append(output)
    return result


def unique_phones(rows: list[list[str]]) -> list[list[str]]:
    """Como UNIQUE + VLOOKUP del modelo: conserva la primera aparición."""
    seen = set()
    result = []
    for row in rows:
        phone = row[0]
        if phone.casefold() not in seen:
            result.append(row)
            seen.add(phone.casefold())
    return result


def sheet_title(name: str, used: set[str]) -> str:
    name = re.sub(r"[\[\]:*?/\\]", "_", name).strip("'") or "Base"
    title = name[:31]
    number = 2
    while title.casefold() in used:
        suffix = f"_{number}"
        title = name[:31 - len(suffix)] + suffix
        number += 1
    used.add(title.casefold())
    return title


def original_table_rows(source: Source) -> list[list[str]]:
    """Reproduce Column1..., FORMULARIO y cabecera real de las tablas de NO."""
    preamble = list(source.preamble)
    generic = [f"Column{i}" for i in range(1, len(source.headers) + 1)]
    if preamble and [normalize(v) for v in preamble[0]] == [normalize(v) for v in generic]:
        preamble = preamble[1:]
    if not preamble:
        preamble = [[""] * (len(source.headers) - 1) + ["FORMULARIO"]]
    return preamble + [source.headers] + source.rows


def write_workbook(path: Path, sources: list[Source], selected: list[list[list[str]]],
                   *, overwrite: bool = False) -> tuple[int, int]:
    if path.exists() and not overwrite:
        raise OutputCollisionError(f"Ya existe {path}. Usa otra salida o --sobrescribir.")
    path.parent.mkdir(parents=True, exist_ok=True)
    combined = list(chain.from_iterable(selected))
    deduplicated = unique_phones(combined)
    # Guarda completo antes de reemplazar el destino; un fallo no deja un Excel parcial.
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".xlsx", delete=False) as tmp:
        temporary = Path(tmp.name)
    try:
        with xlsxwriter.Workbook(str(temporary)) as book:
            book.set_properties({"title": "Base de llamadas", "comments": "Formato NO"})
            text_style = book.add_format({"font_name": "Aptos Narrow", "font_size": 11,
                                          "num_format": "@"})
            green = book.add_format({"bg_color": "#B5E6A2"})
            red = book.add_format({"bg_color": "#FF0000"})
            used = {"validar", "hoja1"}

            def table(title, headers, rows, mode="base", table_number=0, raw_headers=None):
                if len(rows) > 1_048_575:
                    raise BaseError(f"{title}: excede el límite de filas de Excel.")
                if len(headers) > 16_384:
                    raise BaseError(f"{title}: excede el límite de columnas de Excel.")
                sheet = book.add_worksheet(title)
                sheet.set_default_row(15)
                sheet.set_column(0, len(headers) - 1, 18, text_style)
                if mode != "original":
                    widths = (16, 18, 10, 22, 17, 42, 20, 12, 36, 10, 12)
                    for col, width in enumerate(widths):
                        sheet.set_column(col, col, width, text_style)
                elif raw_headers:
                    for col, header in enumerate(raw_headers):
                        key = normalize(header)
                        if key in ALIASES["Nombre"]:
                            width = 81
                        elif key in ALIASES["ID"]:
                            width = 22
                        elif key in ALIASES["Fecha y hora"]:
                            width = 22
                        elif key in ("plan", "plananterior", "planactual"):
                            width = 45
                        else:
                            width = max(16, min(len(header) + 3, 24))
                        sheet.set_column(col, col, width, text_style)
                for col, header in enumerate(headers):
                    if mode == "unique":
                        letter = xl_col_to_name(col)
                        if col == 0:
                            formula = f'=UNIQUE(Validar!A1:A{len(combined) + 1})'
                        else:
                            formula = (f'=VLOOKUP(ANCHORARRAY($A$1),'
                                       f'Validar!$A$1:$K${len(combined) + 1},{col + 1},FALSE)')
                        sheet.write_dynamic_array_formula(
                            f"{letter}1:{letter}{len(rows) + 1}", formula, text_style, header)
                    else:
                        sheet.write_string(0, col, header, text_style)
                for row_index, row in enumerate(rows, 1):
                    for col, value in enumerate(row):
                        if mode in ("base", "consolidated") and col == 7:
                            excel_row = row_index + 1
                            formula = (f'=IF(LEN(G{excel_row})=10,"CÉDULA",'
                                       f'IF(LEN(G{excel_row})=13,"RUC",""))')
                            sheet.write_formula(row_index, col, formula, text_style, value)
                        else:
                            # write_string evita convertir IDs en números o datos en fórmulas.
                            if len(value) > 32_767:
                                raise BaseError(f"{title}, fila {row_index + 1}: un campo excede "
                                                "el límite de 32767 caracteres de Excel.")
                            sheet.write_string(row_index, col, value, text_style)

                if mode == "original":
                    sheet.add_table(0, 0, len(rows), len(headers) - 1, {
                        "name": f"BaseOriginal{table_number}",
                        "style": "Table Style Medium 7",
                        "columns": [{"header": h, "header_format": text_style} for h in headers],
                    })
                elif mode in ("base", "consolidated"):
                    # Las hojas filtradas del modelo tienen AutoFilter, no tablas coloreadas.
                    sheet.autofilter(0, 0, len(rows), len(headers) - 1)
                    if mode == "base" and rows:
                        # NO pinta A:I en rojo cuando T BASE es R; el resto de la base es verde.
                        sheet.conditional_format(1, 0, len(rows), 8,
                                                 {"type": "formula", "criteria": '=$J2="R"',
                                                  "format": red, "stop_if_true": True})
                        sheet.conditional_format(1, 0, len(rows), 10,
                                                 {"type": "formula", "criteria": '=TRUE', "format": green})

            for index, (source, rows) in enumerate(zip(sources, selected), 1):
                table(sheet_title(source.name, used),
                      [f"Column{i}" for i in range(1, len(source.headers) + 1)],
                      original_table_rows(source), mode="original", table_number=index,
                      raw_headers=source.headers)
                number = source.assigned_base_number or infer_base_number(source.name)
                number = int(number[1:]) if number else index
                filtered_name = f"BASE{number}" if number <= 2 else f"Hoja{number}"
                table(sheet_title(filtered_name, used), HEADERS, rows)
            table("Validar", HEADERS, combined, mode="consolidated")
            table("Hoja1", HEADERS, deduplicated, mode="unique")
        if overwrite:
            temporary.replace(path)
        else:
            try:
                if os.name == "nt":
                    # En Windows rename falla si el destino existe, incluso por una carrera.
                    temporary.rename(path)
                else:
                    # Creación atómica sin reemplazo en sistemas con rename sobrescribible.
                    os.link(temporary, path)
            except FileExistsError as exc:
                raise OutputCollisionError(f"El destino {path} ya existe; no se sobrescribió.") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return len(combined), len(deduplicated)


def default_output(input_path: Path, folder: Path = Path("outputs"),
                   base_number: str | None = None,
                   reserved_numbers: set[str] | None = None) -> Path:
    explicit = base_number or infer_base_number(input_path.stem)
    number = int(normalize_base_number(explicit)[1:]) if explicit else 1
    version = 1
    while True:
        if not explicit and f"B{number}" in (reserved_numbers or set()):
            number += 1
            continue
        suffix = f"_DB_delete_B{number}" + (f"_{version}" if version > 1 else "")
        candidate = folder / f"{input_path.stem}{suffix}.xlsx"
        if not candidate.exists():
            return candidate
        if explicit:
            version += 1
        else:
            number += 1


@dataclass
class ConversionResult:
    output: Path
    sources: list[tuple[str, int, int]]
    filtered: int
    unique: int
    missing_base_number: bool


def convert_files(input_paths: list[Path], output: Path | None = None, *,
                  state: str = DEFAULT_STATE, call_state: str = "Success",
                  base_type: str | None = None, base_number: str | None = None,
                  sheet: str | None = None, encoding: str | None = None,
                  delimiter: str | None = None, include_ruc_third_9: bool = False,
                  overwrite: bool = False, output_folder: Path = Path("outputs")) -> ConversionResult:
    if not input_paths:
        raise BaseError("Selecciona al menos un archivo de entrada.")
    if delimiter is not None and len(delimiter) != 1:
        raise BaseError("El separador debe contener exactamente un carácter.")
    paths = [Path(p).resolve(strict=True) for p in input_paths]
    automatic_output = output is None
    base_number = normalize_base_number(base_number) if base_number is not None else None
    output = (output or default_output(paths[0], output_folder, base_number)).resolve()
    if output.suffix.lower() != ".xlsx":
        raise BaseError("La salida debe tener extensión .xlsx.")
    if output in paths:
        raise BaseError("La salida no puede reemplazar un archivo de entrada.")
    if output.exists() and not overwrite and not automatic_output:
        raise BaseError(f"Ya existe {output}. Autoriza su reemplazo o selecciona otra carpeta.")
    sources = []
    for path in paths:
        if path.suffix.lower() == ".csv":
            sources.extend(read_csv(path, encoding, delimiter))
        elif path.suffix.lower() == ".xlsx":
            sources.extend(read_xlsx(path, sheet))
        else:
            raise BaseError(f"{path.name}: solo se admiten CSV y XLSX.")
    if base_number and len(sources) != 1:
        raise BaseError("El número de base manual se aplica a una sola base. Para varias, usa nombres "
                        "B1, B2... o la columna N° BASE de cada origen.")
    # Reserva números conocidos antes de asignar números a orígenes sin identificador.
    known = set()
    for source in sources:
        if base_number:
            known.add(base_number)
        else:
            inferred = infer_base_number(source.name)
            if inferred:
                known.add(normalize_base_number(inferred))
            index = source.mapping.get("N° BASE")
            if index is not None:
                known.update(normalize_base_number(row[index]) for row in source.rows if row[index])
    naming_base = base_number or infer_base_number(sources[0].name)
    first_index = sources[0].mapping.get("N° BASE")
    first_numbers = {normalize_base_number(row[first_index]) for row in sources[0].rows if row[first_index]} if first_index is not None and not base_number else set()
    if not naming_base and len(first_numbers) == 1:
        naming_base = next(iter(first_numbers))
    if automatic_output:
        output = default_output(paths[0], output_folder, naming_base, known).resolve()
    for attempt in range(20):
        used = set(known)
        suffix = re.search(r"_DB_delete_B(\d+)(?:_\d+)?$", output.stem) if automatic_output else None
        next_number = int(suffix.group(1)) if suffix else 1
        for source in sources:
            inferred = base_number or infer_base_number(source.name)
            index = source.mapping.get("N° BASE")
            row_numbers = {normalize_base_number(row[index]) for row in source.rows if row[index]} if index is not None and not base_number else set()
            if inferred:
                assigned = normalize_base_number(inferred)
            elif len(row_numbers) == 1:
                assigned = next(iter(row_numbers))
            else:
                while f"B{next_number}" in used:
                    next_number += 1
                assigned = f"B{next_number}"
                next_number += 1
            source.assigned_base_number = assigned
            used.add(assigned)
        selected = [transform(s, state=state, call_state=call_state, base_type=base_type,
                              base_number=base_number, include_ruc_third_9=include_ruc_third_9)
                    for s in sources]
        try:
            total, unique = write_workbook(output, sources, selected,
                                           overwrite=overwrite and not automatic_output)
            break
        except OutputCollisionError:
            if not automatic_output or attempt == 19:
                raise
            output = default_output(paths[0], output_folder, naming_base, known).resolve()
    return ConversionResult(output, [(s.name, len(s.rows), len(rows))
                                     for s, rows in zip(sources, selected)], total, unique,
                            any(not row[10] for rows in selected for row in rows))


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("entradas", nargs="*", type=Path, help="Uno o varios archivos CSV/XLSX.")
    cli.add_argument("-o", "--salida", type=Path,
                     help="Salida automática: outputs/<nombre_original>_DB_delete_B<n>.xlsx, sin reemplazos.")
    cli.add_argument("--estado", default=DEFAULT_STATE, help="Estado del formulario; * incluye todos.")
    cli.add_argument("--estado-llamada", default="Success", help="Estado de llamada; * incluye todos.")
    cli.add_argument("--incluir-ruc-tercer-digito-9", action="store_true",
                     help="Desactiva la exclusión de IDs de 13 dígitos cuyo tercero es 9, deducida de NO.")
    cli.add_argument("--tipo-base", choices=("V", "R"),
                     help="T BASE: V=verificar, R=remover. Si falta en el origen, utiliza V.")
    cli.add_argument("--numero-base", help="N° BASE: detecta el origen o asigna un número si falta.")
    cli.add_argument("--hoja", help="Hoja de entrada XLSX. Si se omite lee las bases originales.")
    cli.add_argument("--codificacion", help="Codificación del CSV, si falla la detección automática.")
    cli.add_argument("--separador", help="Separador del CSV (por ejemplo ;).")
    cli.add_argument("--sobrescribir", action="store_true", help="Permite reemplazar el destino explícito indicado con -o.")
    return cli


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    if not args.entradas:
        # Sin argumentos permite seleccionar archivos mediante una ventana de Windows.
        try:
            import tkinter as tk
            from tkinter.filedialog import askopenfilenames
        except ImportError:
            cli.error("Indica el archivo de entrada: python scripts/transformar_base.py base.csv")
        try:
            root = tk.Tk()
            root.withdraw()
            try:
                paths = askopenfilenames(title="Selecciona las bases de llamadas",
                                        filetypes=[("Bases CSV o Excel", "*.csv *.xlsx")])
            finally:
                root.destroy()
            args.entradas = [Path(p) for p in paths]
        except tk.TclError:
            cli.error("Indica el archivo de entrada: python scripts/transformar_base.py base.csv")
        if not args.entradas:
            print("Selección cancelada.")
            return 0
    try:
        result = convert_files(args.entradas, args.salida, state=args.estado,
                               call_state=args.estado_llamada, base_type=args.tipo_base,
                               base_number=args.numero_base, sheet=args.hoja, encoding=args.codificacion,
                               delimiter=args.separador, include_ruc_third_9=args.incluir_ruc_tercer_digito_9,
                               overwrite=args.sobrescribir)
        for name, read_count, filtered_count in result.sources:
            print(f"{name}: {read_count} registros leídos, {filtered_count} seleccionados.")
        print(f"Validar: {result.filtered}. Hoja1: {result.unique}. "
              f"Duplicados de teléfono retirados: {result.filtered - result.unique}.")
        if result.missing_base_number:
            print("N° BASE quedó vacío donde no se pudo detectar. Puedes usar --numero-base B1.")
        if not result.filtered:
            print("No hay coincidencias con los filtros; el Excel contiene resultados vacíos y las bases originales.")
        print(f"Excel generado: {result.output}")
        return 0
    except (BaseError, OSError, UnicodeError, csv.Error, BadZipFile, ParseError,
            openpyxl.utils.exceptions.InvalidFileException,
            xlsxwriter.exceptions.XlsxWriterException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
