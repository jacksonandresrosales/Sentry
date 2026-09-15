from __future__ import annotations

import html
import json
import os
import re
import wave
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
import sqlite3

from app.database import Database, DEFAULT_DATABASE
from app.secret_store import SecretStoreError, protect, unprotect
from app.services.audio_analysis import analyze_file, assign_roles
from app.services.base_conversion import BaseAudioIndex, load_hoja1_audio_index, normalize_phone_number
from app.services.winscp_client import (
    WinSCPError, download_remote_audio, scan_host_fingerprint, search_remote_audio, test_connection,
)
from app.ui.theme import (
    DEFAULT_THEME, apply_app_theme, normalize_theme, theme_asset, theme_colors,
    theme_options,
)
from app.ui.views.bases_page import BasesPage

from PySide6.QtCore import (
    QByteArray, QEasingCurve, QPoint, QRectF, QSize, Qt, QThread, QTimer, QUrl,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

PALETTE = dict(theme_colors(DEFAULT_THEME))

AUDIO_SUFFIXES = {".mp3", ".wav"}
_CATEGORY_PIXMAPS: dict[str, QPixmap] = {}


def category_pixmap(name: str) -> QPixmap:
    pixmap = _CATEGORY_PIXMAPS.get(name)
    if pixmap is None:
        path = Path(__file__).resolve().parents[1] / "assets" / name
        pixmap = QIcon(str(path)).pixmap(14, 14)
        _CATEGORY_PIXMAPS[name] = pixmap
    return pixmap


def audio_phone_from_filename(path: Path) -> str | None:
    parts = path.stem.split("-")
    if len(parts) < 3 or parts[0].casefold() != "q" or not parts[1].isdigit() or len(parts[1]) != 3:
        return None
    phone = normalize_phone_number(parts[2])
    return phone or None


def audio_key_from_filename(path: Path) -> tuple[str, str] | None:
    parts = path.stem.split("-")
    phone = audio_phone_from_filename(path)
    if phone is None or len(parts) < 4 or not re.fullmatch(r"20\d{6}", parts[3]):
        return None
    try:
        datetime.strptime(parts[3], "%Y%m%d")
    except ValueError:
        return None
    return phone, parts[3]


def audio_matches_index(path: Path, index: BaseAudioIndex) -> bool:
    key = audio_key_from_filename(path)
    if key is None or key[0] not in index.phone_dates:
        return False
    expected_dates = index.phone_dates[key[0]]
    return not expected_dates or key[1] in expected_dates


def scan_audio_files(
    directory: Path,
    allowed_phones: set[str] | None = None,
    phone_dates: dict[str, frozenset[str]] | None = None,
) -> tuple[Path, ...]:
    if not directory.exists():
        raise FileNotFoundError(directory)
    if not directory.is_dir():
        raise NotADirectoryError(directory)

    normalized_phones = (
        {phone for value in allowed_phones if (phone := normalize_phone_number(value))}
        if allowed_phones is not None else None
    )
    return tuple(
        sorted(
            (
                path for path in directory.rglob("*")
                if path.is_file()
                and path.suffix.casefold() in AUDIO_SUFFIXES
                and (
                    phone_dates is None and (
                        normalized_phones is None or audio_phone_from_filename(path) in normalized_phones
                    )
                    or phone_dates is not None and (
                        (key := audio_key_from_filename(path)) is not None
                        and key[0] in phone_dates
                        and (not phone_dates[key[0]] or key[1] in phone_dates[key[0]])
                    )
                )
            ),
            key=lambda path: str(path).casefold(),
        )
    )


def issabel_directories(remote_root: str, dates: set[str]) -> list[str]:
    root = PurePosixPath(remote_root or "/var/spool/asterisk/monitor")
    directories = []
    for value in sorted(dates):
        if not re.fullmatch(r"20\d{6}", value):
            continue
        year, month, day = value[:4], value[4:6], value[6:8]
        if re.fullmatch(r"20\d{2}", root.name):
            base = root if root.name == year else root.parent / year
        else:
            base = root / year
        directories.append(f"{base / month / day}/")
    return directories


def dated_local_directories(root: Path, dates: set[str]) -> tuple[Path, ...]:
    """Reduce un árbol local/NAS a las carpetas de fecha cuando existen."""
    root = Path(root)
    found: set[Path] = set()
    for value in dates:
        if not re.fullmatch(r"20\d{6}", value):
            continue
        year, month, day = value[:4], value[4:6], value[6:8]
        for candidate in (root / year / month / day, root / month / day, root / day):
            if candidate.is_dir():
                found.add(candidate)
    return tuple(sorted(found, key=lambda path: str(path).casefold())) or (root,)


def install_ui_font(app: QApplication) -> str:
    font_path = Path(__file__).resolve().parents[1] / "assets" / "Inter-Variable.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    family = families[0] if families else "Segoe UI"
    app.setFont(QFont(family, 10))
    return family


@dataclass(frozen=True)
class TranscriptLine:
    second: int
    speaker: str
    text: str
    critical: bool = False
    word_seconds: tuple[float, ...] = ()


@dataclass(frozen=True)
class CallRecord:
    call_id: int
    filename: str
    agent: str
    customer: str
    clock: str
    duration: int
    category: str
    sensitive: bool
    keyword: str
    hit_second: int | None
    risk: str
    summary: str
    snippet: str
    transcript: tuple[TranscriptLine, ...]
    source_path: Path | None = None
    category_code: str = ""
    tags: tuple[str, ...] = ()


def format_time(seconds: int) -> str:
    minutes, remaining = divmod(max(0, seconds), 60)
    return f"{minutes:02d}:{remaining:02d}"


def audio_filename_metadata(path: Path, stat_fallback: bool = True) -> tuple[str, str]:
    """Obtiene teléfono anonimizado y hora desde q-000-teléfono-AAAAMMDD-HHMMSS."""
    parts = path.stem.split("-")
    raw_phone = audio_phone_from_filename(path)
    customer = f"{raw_phone[:3]}***{raw_phone[-4:]}" if raw_phone else "Sin identificar"
    if len(parts) >= 5 and parts[0].casefold() == "q" and parts[1].isdigit() and len(parts[1]) == 3:
        _phone, raw_date, raw_time = parts[2:5]
        try:
            parsed = datetime.strptime(raw_date + raw_time, "%Y%m%d%H%M%S")
        except ValueError:
            pass
        else:
            return customer, parsed.strftime("%H:%M:%S")
    if not stat_fallback:
        return customer, "--:--:--"
    try:
        return customer, datetime.fromtimestamp(path.stat().st_mtime).strftime("%H:%M:%S")
    except OSError:
        return customer, "--:--:--"


def audio_sort_timestamp(path: Path | None) -> float:
    if path is None:
        return 0
    parts = path.stem.split("-")
    if len(parts) >= 5:
        try:
            return datetime.strptime(parts[3] + parts[4], "%Y%m%d%H%M%S").timestamp()
        except ValueError:
            pass
    return 0


def call_record_from_audio(path: Path, call_id: int) -> CallRecord:
    customer, clock = audio_filename_metadata(path)

    duration = 0
    if path.suffix.casefold() == ".wav":
        try:
            with wave.open(str(path), "rb") as audio:
                duration = max(1, round(audio.getnframes() / audio.getframerate()))
        except (EOFError, OSError, ZeroDivisionError, wave.Error):
            pass

    return CallRecord(
        call_id=call_id,
        filename=path.name,
        agent="",
        customer=customer,
        clock=clock,
        duration=duration,
        category="Pendiente",
        sensitive=False,
        keyword="Sin analizar",
        hit_second=None,
        risk="Pendiente",
        summary="El audio fue detectado correctamente y está pendiente de transcripción y análisis.",
        snippet="Archivo listo para procesar.",
        transcript=(TranscriptLine(0, "Sistema", "Transcripción pendiente."),),
        source_path=path,
        category_code="PENDIENTE",
    )


def collect_audio_records(
    directory: Path,
    index: BaseAudioIndex | None = None,
    preselected_paths: list[Path] | tuple[Path, ...] | None = None,
) -> tuple[tuple[Path, ...], list[CallRecord]]:
    """Localiza y lee metadatos sin tocar widgets; es seguro ejecutarlo en segundo plano."""
    directory = Path(directory)
    if preselected_paths is None:
        roots = dated_local_directories(directory, index.dates if index is not None else set())
        detected = {
            path
            for root in roots
            for path in scan_audio_files(
                root,
                index.phones if index is not None else None,
                index.phone_dates if index is not None else None,
            )
        }
    else:
        detected = {
            Path(path)
            for path in preselected_paths
            if Path(path).is_file()
            and Path(path).suffix.casefold() in AUDIO_SUFFIXES
            and (index is None or audio_matches_index(Path(path), index))
        }
    paths = tuple(sorted(detected, key=lambda path: str(path).casefold()))
    records = [call_record_from_audio(path, position) for position, path in enumerate(paths, 1)]
    return paths, records


def call_record_from_row(row: dict) -> CallRecord:
    category = row.get("category") or "PENDIENTE"
    if row.get("status") == "ERROR":
        category = "ERROR"
    segments = []
    try:
        stored_transcript = json.loads(row.get("transcript_json") or "[]")
    except json.JSONDecodeError:
        stored_transcript = []
    hits = row.get("hits") or []
    if isinstance(stored_transcript, dict):
        raw_segments = assign_roles(stored_transcript)["segments"]
    else:
        raw_segments = assign_roles({"segments": stored_transcript, "speaker_count":
                                     len({item.get('speaker') for item in stored_transcript})
                                     if stored_transcript else None})["segments"]
    for item in raw_segments:
        critical = any(hit.get("keyword", "").casefold() in str(item.get("text", "")).casefold() for hit in hits)
        raw_word_seconds = item.get("word_seconds", ())
        word_seconds = tuple(
            float(second) for second in raw_word_seconds if isinstance(second, (int, float))
        ) if isinstance(raw_word_seconds, (list, tuple)) else ()
        segments.append(TranscriptLine(round(float(item.get("second", 0))), str(item.get("speaker", "Hablante")),
                                       str(item.get("text", "")), critical, word_seconds))
    path = Path(row["file_path"])
    customer, clock = audio_filename_metadata(path, stat_fallback=False)
    if clock == "--:--:--":
        try:
            clock = datetime.fromisoformat(str(row.get("created_at", ""))).strftime("%H:%M:%S")
        except ValueError:
            pass
    risk = row.get("risk_level") or ("Pendiente" if category == "PENDIENTE" else "Bajo")
    labels = {"ALERTA": "Alerta sensible", "BUZON": "Buzón / sin conversación",
              "NORMAL": "Llamada normal", "PENDIENTE": "Pendiente", "ERROR": "Error"}
    return CallRecord(
        call_id=int(row["id"]), filename=row["filename"], agent="",
        customer=customer, clock=clock, duration=int(row.get("duration_seconds") or 0),
        category=labels.get(category, category.title()), sensitive=category == "ALERTA",
        keyword=hits[0]["keyword"] if hits else ("Sin analizar" if category == "PENDIENTE" else labels.get(category, category)),
        hit_second=round(float(hits[0]["timestamp_seconds"])) if hits else None,
        risk=risk.title(), summary=row.get("summary") or row.get("analysis_error") or
        "El audio está pendiente de análisis.", snippet=row.get("analysis_error") or "",
        transcript=tuple(segments) or (TranscriptLine(0, "Sistema", "Transcripción pendiente."),), source_path=path,
        category_code=category,
        tags=tuple(dict.fromkeys(str(hit["keyword"]) for hit in hits if hit.get("keyword"))),
    )


class AnalysisWorker(QThread):
    progress = Signal(int, int, str)
    row_ready = Signal(object)
    completed = Signal(int, int, bool)

    def __init__(self, database, paths, config, parent=None):
        super().__init__(parent)
        self.database, self.paths, self.config = database, paths, config
        self._stop = False

    def request_stop(self):
        self._stop = True

    def run(self):
        completed = failures = 0
        for index, path in enumerate(self.paths, 1):
            if self._stop:
                break
            try:
                row = analyze_file(self.database, Path(path), self.config)
            except Exception as exc:
                try:
                    self.database.set_call_status(path, "ERROR", str(exc))
                except Exception:
                    pass
                failures += 1
            else:
                completed += 1
                self.row_ready.emit(row)
            finally:
                self.progress.emit(index, len(self.paths), Path(path).name)
        self.completed.emit(completed, failures, self._stop)


class LocalScanWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        directory: Path,
        index: BaseAudioIndex | None,
        source: str,
        preselected_paths: list[Path] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.directory = Path(directory)
        self.index = index
        self.source = source
        self.preselected_paths = list(preselected_paths) if preselected_paths is not None else None

    def run(self):
        try:
            paths, records = collect_audio_records(self.directory, self.index, self.preselected_paths)
        except FileNotFoundError:
            self.failed.emit("La carpeta seleccionada no existe.")
        except NotADirectoryError:
            self.failed.emit("La ruta seleccionada no es una carpeta.")
        except OSError as exc:
            self.failed.emit(f"No se pudo leer la carpeta seleccionada: {exc}")
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit({
                "directory": self.directory,
                "paths": paths,
                "records": records,
                "source": self.source,
            })


class WinSCPWorker(QThread):
    succeeded = Signal(str, str)
    failed = Signal(str)

    def __init__(self, mode: str, config: dict, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.config = dict(config)

    def run(self):
        try:
            if self.mode == "fingerprint":
                result = scan_host_fingerprint(self.config)
            elif self.mode == "connect":
                result = scan_host_fingerprint(self.config)
                self.config["fingerprint"] = result
                test_connection(self.config)
            else:
                test_connection(self.config)
                result = ""
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(self.mode, result)


class RemoteSearchWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, config: dict, query: str, parent=None):
        super().__init__(parent)
        self.config = dict(config)
        self.query = query

    def run(self):
        try:
            result = search_remote_audio(self.config, self.query)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)


class IssabelMatchWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, config: dict, index: BaseAudioIndex, destination: Path, parent=None):
        super().__init__(parent)
        self.config = dict(config)
        self.index = index
        self.destination = Path(destination)

    def run(self):
        try:
            directories = issabel_directories(self.config.get("remote_path", ""), self.index.dates)
            if not directories:
                raise ValueError("La base activa no contiene fechas válidas para localizar los audios en Issabel.")
            request = dict(self.config)
            request["remote_paths"] = directories
            request["recursive"] = False
            request["phones"] = sorted(self.index.phones)
            candidates = search_remote_audio(request, "q-", limit=5000)
            matches = []
            for item in candidates:
                remote_path = str(item.get("path", ""))
                if audio_matches_index(Path(remote_path), self.index):
                    matches.append(remote_path)
            paths = download_remote_audio(request, matches, self.destination)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit({
                "paths": paths,
                "candidate_count": len(candidates),
                "match_count": len(matches),
                "directories": directories,
            })


class AnalysisProgressButton(QPushButton):
    def __init__(self) -> None:
        super().__init__("Analizar")
        self.setMinimumWidth(118)
        self.theme = DEFAULT_THEME
        self.progress_ratio: float | None = None
        self._display_progress = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(220)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._set_display_progress)

    def set_theme(self, theme_name: str) -> None:
        self.theme = normalize_theme(theme_name)
        self.update()

    def set_progress(self, current: int, total: int) -> None:
        total = max(1, total)
        target = max(0.0, min(1.0, current / total))
        self.progress_ratio = target
        self.setText(f"Detener · {current}/{total}")
        self.setAccessibleName(f"Detener análisis. Progreso: {current} de {total}")
        self._animation.stop()
        if target == 0:
            self._display_progress = 0.0
            self.update()
            return
        self._animation.setStartValue(self._display_progress)
        self._animation.setEndValue(target)
        self._animation.start()

    def set_idle(self) -> None:
        self._animation.stop()
        self.progress_ratio = None
        self._display_progress = 0.0
        self.setText("Analizar")
        self.setAccessibleName("Analizar audios automáticamente")
        self.update()

    def _set_display_progress(self, value: object) -> None:
        self._display_progress = float(value)
        self.update()

    def paintEvent(self, event) -> None:
        if self.progress_ratio is None:
            super().paintEvent(event)
            return
        colors = theme_colors(self.theme)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        progress_rect = QRectF(
            rect.x(), rect.y(), rect.width() * self._display_progress, rect.height()
        )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colors["panel"] if self.isEnabled() else colors["disabled_bg"]))
        painter.drawRoundedRect(rect, 7, 7)
        if self._display_progress > 0:
            painter.save()
            painter.setClipRect(progress_rect)
            painter.setBrush(QColor(colors["green_accessible"]))
            painter.drawRoundedRect(rect, 7, 7)
            painter.restore()

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(colors["green_accessible"]), 1))
        painter.drawRoundedRect(rect, 7, 7)
        painter.setFont(self.font())
        painter.setPen(QColor(colors["text"] if self.isEnabled() else colors["gray_light"]))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())
        if self._display_progress > 0:
            painter.save()
            painter.setClipRect(progress_rect)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())
            painter.restore()


class AudioTimeline(QWidget):
    seek_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.duration = 1
        self.current = 0
        self.marker: int | None = None
        self.theme = DEFAULT_THEME
        self.setMinimumHeight(58)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Línea de tiempo del audio")

    def set_theme(self, theme_name: str) -> None:
        self.theme = normalize_theme(theme_name)
        self.update()

    def set_audio(self, duration: int, marker: int | None) -> None:
        self.duration = max(1, duration)
        self.marker = marker
        self.current = 0
        self.update()

    def set_position(self, seconds: int) -> None:
        self.current = max(0, min(seconds, self.duration))
        self.update()

    def _seek_at(self, x: float) -> None:
        left, width = 10, max(1, self.width() - 20)
        ratio = max(0.0, min(1.0, (x - left) / width))
        self.seek_requested.emit(round(ratio * self.duration))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._seek_at(event.position().x())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = -5 if event.key() == Qt.Key.Key_Left else 5
            self.seek_requested.emit(max(0, min(self.duration, self.current + step)))
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right = 10, self.width() - 10
        center = self.height() / 2
        usable = max(1, right - left)
        progress_x = left + usable * (self.current / self.duration)
        palette = theme_colors(self.theme)

        bars = 72
        gap = usable / bars
        for index in range(bars):
            height = 8 + ((index * 17 + index * index * 3) % 24)
            x = left + index * gap
            color = palette["green_deep"] if x <= progress_x else palette["border_strong"]
            painter.setPen(QPen(QColor(color), max(2.0, gap * 0.42), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(int(x), int(center - height / 2), int(x), int(center + height / 2))

        if self.marker is not None:
            marker_x = left + usable * (self.marker / self.duration)
            painter.setPen(QPen(QColor(palette["green_deep"]), 2))
            painter.drawLine(int(marker_x), 5, int(marker_x), self.height() - 5)

        if self.hasFocus():
            painter.setPen(QPen(QColor(palette["green_deep"]), 2))
            painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 7, 7)


class TranscriptRow(QFrame):
    seek_requested = Signal(int)

    def __init__(self, second: int) -> None:
        super().__init__()
        self.second = second
        self.setObjectName("transcriptRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"Ir al segundo {format_time(second)} de la llamada")
        self.setToolTip(f"Ir a {format_time(second)}")

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.seek_requested.emit(self.second)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.seek_requested.emit(self.second)
            return
        super().keyPressEvent(event)


class CallCard(QFrame):
    clicked = Signal(int)

    def __init__(self, call: CallRecord) -> None:
        super().__init__()
        self.call = call
        self.setObjectName("callCard")
        self.setProperty("sensitive", call.sensitive)
        self.setProperty("selected", False)
        self.setFixedHeight(100)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 9)
        layout.setSpacing(5)

        top = QHBoxLayout()
        dot = QFrame()
        dot.setFixedSize(8, 8)
        dot.setObjectName("alertDot" if call.sensitive else "normalDot")
        top.addWidget(dot)

        customer = QLabel(call.customer)
        customer.setObjectName("callCustomer")
        customer.setToolTip(call.customer)
        top.addWidget(customer)

        classification = call.category_code or ("ALERTA" if call.sensitive else "NORMAL")
        classification_labels = {
            "ALERTA": "Demanda / alerta",
            "BUZON": "Buzón",
            "NORMAL": "Llamada normal",
        }
        icon_names = {
            "ALERTA": "category-alert.svg",
            "BUZON": "category-mailbox.svg",
            "NORMAL": "category-normal.svg",
        }
        tag_text = " · ".join(call.tags[:2])
        if len(call.tags) > 2:
            tag_text += f" · +{len(call.tags) - 2}"
        badge = QFrame()
        badge.setObjectName("classificationBadge")
        badge.setProperty("category", classification)
        badge_layout = QHBoxLayout(badge)
        badge_layout.setContentsMargins(6, 2, 6, 2)
        badge_layout.setSpacing(4)
        icon_name = icon_names.get(classification)
        if icon_name:
            icon = QLabel()
            icon.setObjectName("classificationIcon")
            icon.setPixmap(category_pixmap(icon_name))
            badge_layout.addWidget(icon)
        badge_text = QLabel(tag_text or classification_labels.get(classification, call.keyword.capitalize()))
        badge_text.setObjectName("classificationText")
        badge_layout.addWidget(badge_text)
        if call.tags:
            badge.setToolTip("Etiquetas encontradas: " + ", ".join(call.tags))
        else:
            badge.setToolTip("Clasificación: " + classification_labels.get(classification, call.category))
        top.addWidget(badge)
        top.addStretch()

        clock = QLabel(call.clock)
        clock.setObjectName("monoMuted")
        top.addWidget(clock)
        layout.addLayout(top)

        snippet = QLabel(call.snippet)
        snippet.setObjectName("callSnippet")
        snippet.setToolTip(call.snippet)
        layout.addWidget(snippet)

        meta = QHBoxLayout()
        duration = QLabel(f"{format_time(call.duration)} min")
        duration.setObjectName("monoMuted")
        meta.addStretch()
        meta.addWidget(duration)
        layout.addLayout(meta)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.call.call_id)
        super().mouseReleaseEvent(event)


class SentryWindow(QMainWindow):
    def __init__(self, database_path: Path = DEFAULT_DATABASE) -> None:
        super().__init__()
        self.database = Database(database_path)
        settings = self.database.settings()
        self.theme = normalize_theme(settings.get("theme", DEFAULT_THEME))
        app = QApplication.instance()
        if app is not None:
            apply_app_theme(app, self.theme)
        self.setWindowTitle("Sentry · Auditoría de grabaciones")
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().parents[1] / "assets" / "sentry-app-icon.ico")))
        self.resize(1440, 860)
        self.setMinimumSize(1080, 680)

        self.call_records: list[CallRecord] = []
        self.calls: dict[int, CallRecord] = {}
        self.detected_audio_files: tuple[Path, ...] = ()
        self.current_call: CallRecord | None = None
        self.current_second = 0
        self.current_duration = 0
        self.call_cards: dict[int, CallCard] = {}
        self.call_items: dict[int, QListWidgetItem] = {}
        self.call_search_cache: dict[int, str] = {}
        self.selected_call_id: int | None = None
        self.nav_buttons: dict[str, QPushButton] = {}
        self.critical_line: QWidget | None = None
        self.transcript_rows: list[tuple[TranscriptLine, TranscriptRow, QLabel]] = []
        self.active_transcript_index = -1
        self.active_heard_words = -1
        self.sort_mode = "original"
        self.sort_label = "original"
        self.active_base_path: Path | None = None
        self.active_base_phones: set[str] = set()
        self.active_base_index = BaseAudioIndex({})
        self.network = QNetworkAccessManager(self)
        self.analysis_worker: AnalysisWorker | None = None
        self.local_scan_worker: LocalScanWorker | None = None
        self.winscp_worker: WinSCPWorker | None = None
        self.remote_search_worker: RemoteSearchWorker | None = None
        self.issabel_match_worker: IssabelMatchWorker | None = None
        self.close_after_analysis = False

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(1.0)
        self.media_player = QMediaPlayer(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.positionChanged.connect(self._on_player_position_changed)
        self.media_player.durationChanged.connect(self._on_player_duration_changed)
        self.media_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.media_player.errorOccurred.connect(self._on_player_error)

        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(self._hide_toast)

        self._build_ui()
        self._set_theme(self.theme)
        if "audio_directory" in settings:
            self.config_directory.setText(settings["audio_directory"])
        if "nas_directory" in settings:
            self.nas_directory.setText(settings["nas_directory"])
        if "audio_source" in settings:
            source_index = self.audio_source.findData(settings["audio_source"])
            if source_index >= 0:
                self.audio_source.setCurrentIndex(source_index)
        self._source_changed()
        if "keywords" in settings:
            self.keywords_input.setText(settings["keywords"])
        self._restore_credentials()
        self._restore_remote_connection()
        persisted = self.database.call_rows()
        if self.active_base_path is not None:
            persisted = [
                row for row in persisted
                if audio_matches_index(Path(row["file_path"]), self.active_base_index)
            ]
        if persisted:
            self.call_records = [call_record_from_row(row) for row in persisted]
            self.calls = {call.call_id: call for call in self.call_records}
            self.detected_audio_files = tuple(call.source_path for call in self.call_records if call.source_path)
            self._populate_call_list()
            self._update_category_metrics()
        if self.call_list.count():
            self.call_list.setCurrentRow(0)
        else:
            self._show_empty_state()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_header())

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_audit_page())
        self.pages.addWidget(self._build_reports_page())
        self.pages.addWidget(self._build_config_page())
        self.bases_page = BasesPage(self.database)
        self.active_base_path = self.bases_page.active_base_path
        self.active_base_phones = set(self.bases_page.active_phones)
        self.active_base_index = self.bases_page.active_index
        self.bases_page.base_selected.connect(self._activate_audio_base)
        self.pages.addWidget(self.bases_page)
        root_layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

        self.toast = QLabel(root)
        self.toast.setObjectName("toast")
        self.toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toast.hide()

    def _build_header(self) -> QWidget:
        assets_path = Path(__file__).resolve().parents[1] / "assets"
        header = QFrame()
        header.setObjectName("topbar")
        header.setFixedHeight(64)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(18)

        brand = QHBoxLayout()
        brand.setSpacing(8)
        logo = QLabel()
        logo.setObjectName("brandLogo")
        logo.setAccessibleName("Logo de Ecuaconexión")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(42, 34)
        logo_path = assets_path / "ecuaconexion-logo.png"
        logo.setPixmap(
            QPixmap(str(logo_path)).scaled(
                40,
                30,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        brand.addWidget(logo)
        title = QLabel("SENTRY")
        title.setObjectName("brandTitle")
        brand.addWidget(title)
        layout.addLayout(brand)

        directory = QFrame()
        directory.setObjectName("directoryBar")
        directory.setMinimumWidth(220)
        directory.setMaximumWidth(290)
        directory_layout = QHBoxLayout(directory)
        directory_layout.setContentsMargins(10, 5, 6, 5)
        directory_layout.setSpacing(7)
        folder_icon = QLabel()
        folder_icon.setAccessibleName("Directorio")
        folder_icon.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon).pixmap(16, 16))
        directory_layout.addWidget(folder_icon)
        self.directory_label = QLabel()
        self.directory_label.setObjectName("directoryPath")
        self.directory_label.setAccessibleName("Directorio actual")
        self.directory_label.setMinimumWidth(90)
        self.directory_label.setMaximumWidth(155)
        self._set_directory_display("")
        directory_layout.addWidget(self.directory_label, 1)
        change = QPushButton("Cambiar")
        change.setObjectName("linkButton")
        change.setCursor(Qt.CursorShape.PointingHandCursor)
        change.clicked.connect(self._choose_directory)
        directory_layout.addWidget(change)
        layout.addWidget(directory)
        self.bases_button = QPushButton("Bases")
        self.bases_button.setObjectName("navButton")
        self.bases_button.setCheckable(True)
        self.bases_button.setIcon(QIcon(str(assets_path / theme_asset(self.theme, "bases"))))
        self.bases_button.setIconSize(QSize(16, 16))
        self.bases_button.setAccessibleName("Preparar bases DB_delete")
        self.bases_button.setToolTip("Preparar bases CSV / Excel · DB_delete")
        self.bases_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bases_button.clicked.connect(lambda: self._switch_page("bases"))
        self.nav_buttons["bases"] = self.bases_button
        layout.addWidget(self.bases_button)
        layout.addStretch()

        nav = QHBoxLayout()
        nav.setSpacing(4)
        settings_icon = QIcon(str(assets_path / theme_asset(self.theme, "settings")))
        for index, (key, text, icon) in enumerate((
            ("audit", "Auditoría", None),
            ("reports", "Reportes", None),
            ("config", "Configuración", settings_icon),
        )):
            button = QPushButton("" if icon else text)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(text)
            if icon:
                button.setIcon(icon)
                button.setIconSize(QSize(17, 17))
                button.setFixedSize(38, 36)
                button.setToolTip(text)
            button.clicked.connect(lambda _checked=False, k=key: self._switch_page(k))
            self.nav_buttons[key] = button
            nav.addWidget(button)
        layout.addLayout(nav)

        self.analyze_button = AnalysisProgressButton()
        self.analyze_button.setObjectName("primaryButton")
        self.analyze_button.setAccessibleName("Analizar audios automáticamente")
        self.analyze_button.setToolTip("Transcribe pendientes y los separa en alertas, buzones y normales")
        self.analyze_button.clicked.connect(self._start_analysis)
        layout.addWidget(self.analyze_button)
        return header

    def _build_audit_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("auditPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_filter_bar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("mainSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_call_queue())
        splitter.addWidget(self._build_detail_panel())
        splitter.setStretchFactor(0, 38)
        splitter.setStretchFactor(1, 62)
        splitter.setSizes([440, 760])
        layout.addWidget(splitter, 1)
        return page

    def _build_filter_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("filterBar")
        bar.setFixedHeight(62)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(18)

        for attribute, value, label, accent in (
            ("files_metric", "0", "Archivos", False),
            ("sensitive_metric", "0", "Alertas", True),
            ("mailbox_metric", "0", "Buzones", False),
            ("normal_metric", "0", "Normales", False),
        ):
            metric = QVBoxLayout()
            metric.setSpacing(0)
            number = QLabel(value)
            number.setObjectName("metricAccent" if accent else "metricValue")
            setattr(self, attribute, number)
            caption = QLabel(label)
            caption.setObjectName("metricLabel")
            if attribute == "normal_metric":
                self.normal_metric_label = caption
            metric.addWidget(number)
            metric.addWidget(caption)
            layout.addLayout(metric)

        layout.addStretch()
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("Buscar por archivo, cliente o etiqueta")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(260)
        self.search_input.textChanged.connect(self._filter_calls)
        layout.addWidget(self.search_input)

        self.status_filter = QComboBox()
        self.status_filter.addItem("Todas las llamadas", "all")
        self.status_filter.addItem("Demandas / alertas", "alert")
        self.status_filter.addItem("Buzones", "mailbox")
        self.status_filter.addItem("Llamadas normales", "normal")
        self.status_filter.addItem("Pendientes / errores", "pending")
        self.status_filter.currentIndexChanged.connect(self._filter_calls)

        self.export_button = QPushButton("Exportar Excel")
        self.export_button.setObjectName("secondaryButton")
        self.export_button.clicked.connect(lambda: self._show_toast("La exportación se conectará en la siguiente etapa"))
        layout.addWidget(self.export_button)
        return bar

    def _build_call_queue(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("queuePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        heading = QFrame()
        heading.setObjectName("sectionHeader")
        heading_layout = QHBoxLayout(heading)
        heading_layout.setContentsMargins(16, 11, 16, 11)
        title = QLabel("Llamadas detectadas")
        title.setObjectName("sectionTitle")
        self.call_count = QLabel("0 llamadas")
        self.call_count.setObjectName("monoMuted")
        heading_layout.addWidget(title)
        heading_layout.addStretch()
        self.sort_button = QPushButton("Orden: original")
        self.sort_button.setObjectName("sortButton")
        self.sort_button.setAccessibleName("Ordenar llamadas")
        self.sort_button.setToolTip("La prioridad respeta el orden de los términos sensibles configurados")
        sort_menu = QMenu(self.sort_button)
        for label, short_label, mode in (
            ("Prioridad de términos sensibles", "prioridad", "priority"),
            ("Mayor duración primero", "mayor duración", "duration_desc"),
            ("Menor duración primero", "menor duración", "duration_asc"),
            ("Más recientes", "más recientes", "newest"),
            ("Más antiguas", "más antiguas", "oldest"),
            ("Nombre A-Z", "nombre A-Z", "name"),
            ("Orden original", "original", "original"),
        ):
            action = sort_menu.addAction(label)
            action.triggered.connect(lambda _checked=False, m=mode, text=short_label: self._sort_calls(m, text))
        self.sort_button.setMenu(sort_menu)
        heading_layout.addWidget(self.sort_button)
        self.status_filter.setMinimumWidth(165)
        self.status_filter.setAccessibleName("Organizar llamadas por clasificación")
        heading_layout.addWidget(self.status_filter)
        heading_layout.addWidget(self.call_count)
        layout.addWidget(heading)

        self.call_list = QListWidget()
        self.call_list.setObjectName("callList")
        self.call_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.call_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.call_list.setUniformItemSizes(True)
        self.call_list.setSpacing(0)
        self.call_list.setContentsMargins(0, 0, 0, 0)
        self.call_list.currentItemChanged.connect(self._on_call_selected)
        self.call_list.verticalScrollBar().valueChanged.connect(self._materialize_visible_cards)
        self._populate_call_list()
        layout.addWidget(self.call_list, 1)
        return panel

    def _populate_call_list(self) -> None:
        self._apply_call_sort()
        self.call_list.blockSignals(True)
        self.call_list.setUpdatesEnabled(False)
        try:
            self.call_list.clear()
            self.call_cards.clear()
            self.call_items.clear()
            self.call_search_cache = {
                call.call_id: self._searchable_call_text(call) for call in self.call_records
            }
            for call in self.call_records:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, call.call_id)
                item.setText(call.filename)
                item.setSizeHint(QSize(0, 100))
                self.call_list.addItem(item)
                self.call_items[call.call_id] = item
            for row in range(min(60, len(self.call_records))):
                self._materialize_call_card(row)
        finally:
            self.call_list.blockSignals(False)
            self.call_list.setUpdatesEnabled(True)
        count = len(self.call_records)
        self.call_count.setText(f"{count} {'llamada' if count == 1 else 'llamadas'}")
        QTimer.singleShot(0, self._materialize_visible_cards)

    def _materialize_call_card(self, row: int) -> CallCard | None:
        if row < 0 or row >= self.call_list.count():
            return None
        item = self.call_list.item(row)
        call_id = item.data(Qt.ItemDataRole.UserRole)
        existing = self.call_cards.get(call_id)
        if existing is not None:
            return existing
        call = self.calls.get(call_id)
        if call is None:
            call = next((record for record in self.call_records if record.call_id == call_id), None)
        if call is None:
            return None
        card = CallCard(call)
        card.clicked.connect(self._select_call_by_id)
        card.set_selected(call_id == self.selected_call_id)
        self.call_cards[call_id] = card
        self.call_list.setItemWidget(item, card)
        return card

    def _materialize_visible_cards(self, _value: int | None = None) -> None:
        if not hasattr(self, "call_list") or not self.call_list.count():
            return
        first = self.call_list.indexAt(QPoint(2, 2)).row()
        last = self.call_list.indexAt(QPoint(2, max(2, self.call_list.viewport().height() - 2))).row()
        if first < 0:
            first = 0
        if last < first:
            last = min(self.call_list.count() - 1, first + 20)
        kept_ids: set[int] = set()
        for row in range(max(0, first - 5), min(self.call_list.count(), last + 11)):
            if not self.call_list.item(row).isHidden():
                self._materialize_call_card(row)
                kept_ids.add(self.call_list.item(row).data(Qt.ItemDataRole.UserRole))
        if self.selected_call_id is not None:
            kept_ids.add(self.selected_call_id)
        for call_id, card in tuple(self.call_cards.items()):
            if call_id in kept_ids:
                continue
            item = self.call_items.get(call_id)
            if item is not None:
                self.call_list.removeItemWidget(item)
            card.deleteLater()
            self.call_cards.pop(call_id, None)

    @staticmethod
    def _searchable_call_text(call: CallRecord) -> str:
        return (
            f"{call.filename} {call.customer} {call.keyword} {' '.join(call.tags)} {call.snippet}"
        ).casefold()

    def _apply_call_sort(self) -> None:
        if self.sort_mode == "priority":
            priorities = {
                term.strip().casefold(): index
                for index, term in enumerate(self.keywords_input.text().split(","))
                if term.strip()
            }

            def priority(call: CallRecord) -> tuple[int, int, int]:
                matches = [priorities[tag.casefold()] for tag in call.tags if tag.casefold() in priorities]
                if call.keyword.casefold() in priorities:
                    matches.append(priorities[call.keyword.casefold()])
                return (not call.sensitive, min(matches, default=len(priorities)), call.call_id)

            self.call_records.sort(key=priority)
        elif self.sort_mode == "duration_desc":
            self.call_records.sort(key=lambda call: (-call.duration, call.call_id))
        elif self.sort_mode == "duration_asc":
            self.call_records.sort(key=lambda call: (call.duration, call.call_id))
        elif self.sort_mode == "newest":
            self.call_records.sort(key=lambda call: (-audio_sort_timestamp(call.source_path), call.call_id))
        elif self.sort_mode == "oldest":
            self.call_records.sort(key=lambda call: (audio_sort_timestamp(call.source_path), call.call_id))
        elif self.sort_mode == "name":
            self.call_records.sort(key=lambda call: (call.filename.casefold(), call.call_id))
        else:
            self.call_records.sort(key=lambda call: call.call_id)

    def _sort_calls(self, mode: str, label: str) -> None:
        current = self.call_list.currentItem()
        selected_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.sort_mode = mode
        self.sort_label = label
        self.sort_button.setText("Orden" if self.width() < 1240 else f"Orden: {label}")
        self.sort_button.setToolTip(f"Orden actual: {label}. La prioridad respeta el orden de los términos configurados")
        self._populate_call_list()
        self._filter_calls()
        if selected_id is not None:
            self._select_call_by_id(selected_id)

    def _build_detail_panel(self) -> QWidget:
        self.detail_pages = QStackedWidget()
        self.detail_pages.setObjectName("detailPages")

        empty = QWidget()
        empty.setObjectName("emptyDetail")
        empty_layout = QVBoxLayout(empty)
        empty_layout.setContentsMargins(48, 48, 48, 48)
        empty_layout.addStretch()
        empty_icon = QLabel()
        empty_icon.setObjectName("emptyIcon")
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_icon.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView).pixmap(34, 34))
        self.empty_detail_title = QLabel("Sin llamadas")
        self.empty_detail_title.setObjectName("emptyTitle")
        self.empty_detail_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_detail_text = QLabel("Configura una carpeta y escanéala para comenzar la auditoría.")
        self.empty_detail_text.setObjectName("pageSubtitle")
        self.empty_detail_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_detail_text.setWordWrap(True)
        empty_action = QPushButton("Ir a configuración")
        empty_action.setObjectName("secondaryButton")
        empty_action.setMaximumWidth(180)
        empty_action.clicked.connect(lambda: self._switch_page("config"))
        empty_layout.addWidget(empty_icon)
        empty_layout.addSpacing(8)
        empty_layout.addWidget(self.empty_detail_title)
        empty_layout.addWidget(self.empty_detail_text)
        empty_layout.addSpacing(12)
        empty_layout.addWidget(empty_action, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch()
        self.detail_pages.addWidget(empty)

        scroll = QScrollArea()
        scroll.setObjectName("detailScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("detailContent")
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.detail_layout = QVBoxLayout(content)
        self.detail_layout.setContentsMargins(22, 18, 22, 18)
        self.detail_layout.setSpacing(14)

        self.detail_layout.addWidget(self._build_call_identity())
        self.detail_layout.addWidget(self._build_summary())
        self.detail_layout.addWidget(self._build_player())
        self.detail_layout.addWidget(self._build_transcript(), 1)
        self.detail_layout.addLayout(self._build_detail_actions())
        scroll.setWidget(content)
        self.detail_pages.addWidget(scroll)
        return self.detail_pages

    def _build_call_identity(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("contentPanel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        top = QHBoxLayout()
        self.filename_label = QLabel()
        self.filename_label.setObjectName("filename")
        self.risk_badge = QLabel()
        self.risk_badge.setObjectName("riskBadge")
        self.duration_label = QLabel()
        self.duration_label.setObjectName("duration")
        top.addWidget(self.filename_label)
        top.addWidget(self.risk_badge)
        top.addStretch()
        top.addWidget(self.duration_label)
        layout.addLayout(top)

        meta = QGridLayout()
        meta.setHorizontalSpacing(26)
        self.customer_value = self._add_meta(meta, 0, "CLIENTE")
        self.time_value = self._add_meta(meta, 1, "HORA")
        self.risk_value = self._add_meta(meta, 2, "RIESGO")
        layout.addLayout(meta)
        return frame

    def _add_meta(self, layout: QGridLayout, column: int, label: str) -> QLabel:
        caption = QLabel(label)
        caption.setObjectName("metricLabel")
        value = QLabel("—")
        value.setObjectName("metaValue")
        layout.addWidget(caption, 0, column)
        layout.addWidget(value, 1, column)
        return value

    def _build_summary(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("summaryPanel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(5)
        label = QLabel("Síntesis contextual")
        label.setObjectName("sectionTitle")
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setMinimumWidth(0)
        self.summary_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.summary_label.setObjectName("summaryText")
        layout.addWidget(label)
        layout.addWidget(self.summary_label)
        return frame

    def _build_player(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("playerPanel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(7)

        controls = QHBoxLayout()
        self.play_button = QPushButton()
        self.play_button.setObjectName("playButton")
        self.play_button.setFixedSize(42, 42)
        self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_button.setAccessibleName("Reproducir audio")
        self.play_button.clicked.connect(self._toggle_playback)
        controls.addWidget(self.play_button)

        time_box = QVBoxLayout()
        now = QLabel("Reproducción")
        now.setObjectName("metricLabel")
        self.time_display = QLabel("00:00 / 00:54")
        self.time_display.setObjectName("timeDisplay")
        time_box.addWidget(now)
        time_box.addWidget(self.time_display)
        controls.addLayout(time_box)
        controls.addStretch()

        self.jump_button = QPushButton()
        self.jump_button.setObjectName("criticalButton")
        self.jump_button.clicked.connect(self._jump_to_evidence)
        controls.addWidget(self.jump_button)
        layout.addLayout(controls)

        self.timeline = AudioTimeline()
        self.timeline.seek_requested.connect(self._seek_audio)
        layout.addWidget(self.timeline)
        return frame

    def _build_transcript(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("transcriptPanel")
        frame.setMinimumHeight(230)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(9)

        heading = QHBoxLayout()
        title = QLabel("Transcripción de la llamada")
        title.setObjectName("sectionTitle")
        mode = QLabel("Audio sincronizado")
        mode.setObjectName("statusOnline")
        heading.addWidget(title)
        heading.addStretch()
        heading.addWidget(mode)
        layout.addLayout(heading)

        self.transcript_scroll = QScrollArea()
        self.transcript_scroll.setObjectName("transcriptScroll")
        self.transcript_scroll.setWidgetResizable(True)
        self.transcript_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.transcript_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transcript_body = QWidget()
        self.transcript_body.setObjectName("transcriptBody")
        self.transcript_layout = QVBoxLayout(self.transcript_body)
        self.transcript_layout.setContentsMargins(0, 5, 5, 0)
        self.transcript_layout.setSpacing(8)
        self.transcript_layout.addStretch()
        self.transcript_scroll.setWidget(self.transcript_body)
        layout.addWidget(self.transcript_scroll, 1)
        return frame

    def _build_detail_actions(self) -> QHBoxLayout:
        actions = QHBoxLayout()
        self.reviewed_button = QPushButton("Marcar como revisada")
        self.reviewed_button.setObjectName("secondaryButton")
        self.reviewed_button.clicked.connect(self._mark_reviewed)
        self.original_button = QPushButton("Abrir audio original")
        self.original_button.setObjectName("linkButton")
        self.original_button.clicked.connect(self._open_original_audio)
        actions.addWidget(self.reviewed_button)
        actions.addStretch()
        actions.addWidget(self.original_button)
        return actions

    def _build_reports_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("reportsPage")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(28, 24, 28, 28)
        outer.setSpacing(18)

        title_row = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Reportes y estadísticas")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Resumen del directorio seleccionado")
        subtitle.setObjectName("pageSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        title_row.addLayout(titles)
        title_row.addStretch()
        self.report_download = QPushButton("Descargar informe")
        self.report_download.setObjectName("primaryButton")
        self.report_download.setEnabled(False)
        self.report_download.clicked.connect(lambda: self._show_toast("La descarga se conectará en la etapa de reportes"))
        title_row.addWidget(self.report_download)
        outer.addLayout(title_row)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        metrics.addWidget(self._report_metric("report_total_value", "Llamadas encontradas", "Archivos del directorio"))
        metrics.addWidget(self._report_metric("report_sensitive_value", "Alertas sensibles", "Términos detectados", True))
        metrics.addWidget(self._report_metric("report_normal_value", "Llamadas normales", "Análisis completados"))
        outer.addLayout(metrics)

        self.report_empty = QLabel("Escanea una carpeta para generar estadísticas reales.")
        self.report_empty.setObjectName("emptyReport")
        self.report_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.report_empty.setMinimumHeight(180)
        outer.addWidget(self.report_empty)
        outer.addStretch()
        return page

    def _report_metric(self, attribute: str, label: str, note: str, accent: bool = False) -> QWidget:
        frame = QFrame()
        frame.setObjectName("reportMetricAccent" if accent else "reportMetric")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 16, 18, 16)
        number = QLabel("0")
        number.setObjectName("reportValueAccent" if accent else "reportValue")
        setattr(self, attribute, number)
        caption = QLabel(label)
        caption.setObjectName("reportLabel")
        hint = QLabel(note)
        hint.setObjectName("mutedLabel")
        layout.addWidget(number)
        layout.addWidget(caption)
        layout.addWidget(hint)
        return frame

    def _build_config_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("configScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        page = QWidget()
        page.setObjectName("configPage")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(28, 24, 28, 28)
        outer.setSpacing(18)

        title = QLabel("Configuración del sistema")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Define grabaciones, términos sensibles y servicios de IA")
        subtitle.setObjectName("pageSubtitle")
        outer.addWidget(title)
        outer.addWidget(subtitle)

        appearance = QFrame()
        appearance.setObjectName("contentPanel")
        appearance_layout = QVBoxLayout(appearance)
        appearance_layout.setContentsMargins(20, 18, 20, 18)
        appearance_layout.setSpacing(8)
        appearance_title = QLabel("Temas")
        appearance_title.setObjectName("formTitle")
        appearance_help = QLabel(
            "Selecciona la apariencia de Sentry. La elección se guarda en este equipo."
        )
        appearance_help.setObjectName("pageSubtitle")
        self.theme_combo = QComboBox()
        self.theme_combo.setAccessibleName("Tema de la aplicación")
        for theme_id, label in theme_options():
            self.theme_combo.addItem(label, theme_id)
        theme_index = self.theme_combo.findData(self.theme)
        self.theme_combo.setCurrentIndex(max(0, theme_index))
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        appearance_title.setBuddy(self.theme_combo)
        appearance_layout.addWidget(appearance_title)
        appearance_layout.addWidget(appearance_help)
        appearance_layout.addWidget(self.theme_combo)
        outer.addWidget(appearance)

        directory = QFrame()
        directory.setObjectName("contentPanel")
        directory_layout = QVBoxLayout(directory)
        directory_layout.setContentsMargins(20, 18, 20, 18)
        directory_layout.setSpacing(8)
        directory_title = QLabel("Directorio de grabaciones")
        directory_title.setObjectName("formTitle")
        directory_help = QLabel(
            "Elige dónde buscar. Local y NAS leen la carpeta directamente; Issabel consulta solo "
            "los días y teléfonos de la base activa."
        )
        directory_help.setObjectName("pageSubtitle")
        directory_help.setWordWrap(True)
        source_row = QHBoxLayout()
        source_label = QLabel("ORIGEN")
        source_label.setObjectName("fieldLabel")
        self.audio_source = QComboBox()
        self.audio_source.addItem("Carpeta local", "local")
        self.audio_source.addItem("NAS / carpeta compartida", "nas")
        self.audio_source.addItem("Issabel / SFTP", "issabel")
        saved_source = self.database.settings().get("audio_source", "local")
        source_index = self.audio_source.findData(saved_source)
        self.audio_source.setCurrentIndex(max(0, source_index))
        source_label.setBuddy(self.audio_source)
        source_row.addWidget(source_label)
        source_row.addWidget(self.audio_source, 1)

        self.local_directory_row = QWidget()
        directory_row = QHBoxLayout(self.local_directory_row)
        directory_row.setContentsMargins(0, 0, 0, 0)
        self.config_directory = QLineEdit(
            self.database.settings().get("audio_directory", "")
        )
        self.config_directory.setPlaceholderText(r"C:\Grabaciones\Llamadas_Entrantes")
        directory_title.setBuddy(self.config_directory)
        choose = QPushButton("Examinar local…")
        choose.setObjectName("secondaryButton")
        choose.clicked.connect(lambda: self._choose_source_directory("local"))
        directory_row.addWidget(self.config_directory, 1)
        directory_row.addWidget(choose)

        self.nas_directory_row = QWidget()
        nas_row = QHBoxLayout(self.nas_directory_row)
        nas_row.setContentsMargins(0, 0, 0, 0)
        self.nas_directory = QLineEdit(self.database.settings().get("nas_directory", ""))
        self.nas_directory.setPlaceholderText(r"\\servidor\grabaciones o una unidad de red")
        self.nas_directory.setAccessibleName("Carpeta de grabaciones del NAS")
        nas_choose = QPushButton("Examinar NAS…")
        nas_choose.setObjectName("secondaryButton")
        nas_choose.clicked.connect(lambda: self._choose_source_directory("nas"))
        nas_row.addWidget(self.nas_directory, 1)
        nas_row.addWidget(nas_choose)

        self.issabel_source_note = QLabel(
            "Se descargan únicamente los audios que coinciden por teléfono y fecha con Hoja1."
        )
        self.issabel_source_note.setObjectName("apiStatus")
        self.issabel_source_note.setWordWrap(True)
        directory_layout.addWidget(directory_title)
        directory_layout.addWidget(directory_help)
        directory_layout.addLayout(source_row)
        directory_layout.addWidget(self.local_directory_row)
        directory_layout.addWidget(self.nas_directory_row)
        directory_layout.addWidget(self.issabel_source_note)
        self.audio_source.currentIndexChanged.connect(self._source_changed)
        self._source_changed()
        outer.addWidget(directory)

        remote = QFrame()
        remote.setObjectName("contentPanel")
        remote_layout = QVBoxLayout(remote)
        remote_layout.setContentsMargins(20, 18, 20, 18)
        remote_layout.setSpacing(10)
        remote_title = QLabel("Servidor de grabaciones (WinSCP / SFTP)")
        remote_title.setObjectName("formTitle")
        remote_help = QLabel(
            "Usa los mismos tres datos con los que inicias sesión en WinSCP. Sentry utilizará "
            "el puerto estándar 22, abrirá la carpeta inicial y validará la huella SSH automáticamente."
        )
        remote_help.setObjectName("pageSubtitle")
        remote_help.setWordWrap(True)
        remote_layout.addWidget(remote_title)
        remote_layout.addWidget(remote_help)

        remote_fields = QGridLayout()
        remote_fields.setHorizontalSpacing(12)
        remote_fields.setVerticalSpacing(7)
        self.remote_host = QLineEdit()
        self.remote_host.setPlaceholderText("IP del servidor")
        self.remote_host.setAccessibleName("IP o servidor SFTP")
        self.remote_port = QLineEdit("22")
        self.remote_port.setAccessibleName("Puerto SFTP")
        self.remote_username = QLineEdit()
        self.remote_username.setText("root")
        self.remote_username.setPlaceholderText("root")
        self.remote_username.setAccessibleName("Usuario de WinSCP")
        self.remote_password = QLineEdit()
        self.remote_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.remote_password.setPlaceholderText("Contraseña")
        self.remote_password.setAccessibleName("Contraseña de WinSCP")
        self.remote_path = QLineEdit("/var/spool/asterisk/monitor/")
        self.remote_path.setPlaceholderText("/grabaciones")
        self.remote_path.setAccessibleName("Carpeta remota")
        self.remote_fingerprint = QLineEdit()
        self.remote_fingerprint.setAccessibleName("Huella SSH del servidor")

        for column, (label_text, field) in enumerate(
            (("IP DEL SERVIDOR", self.remote_host), ("USUARIO", self.remote_username))
        ):
            label = QLabel(label_text)
            label.setObjectName("fieldLabel")
            label.setBuddy(field)
            remote_fields.addWidget(label, 0, column)
            remote_fields.addWidget(field, 1, column)
        password_label = QLabel("CONTRASEÑA")
        password_label.setObjectName("fieldLabel")
        password_label.setBuddy(self.remote_password)
        remote_fields.addWidget(password_label, 2, 0, 1, 2)
        password_row = QHBoxLayout()
        password_row.setSpacing(7)
        password_row.addWidget(self.remote_password, 1)
        self.remote_password_toggle = QPushButton("Mostrar")
        self.remote_password_toggle.setObjectName("secondaryButton")
        self.remote_password_toggle.setCheckable(True)
        self.remote_password_toggle.toggled.connect(
            lambda visible: self._set_secret_visibility(
                self.remote_password, self.remote_password_toggle, visible
            )
        )
        password_row.addWidget(self.remote_password_toggle)
        remote_fields.addLayout(password_row, 3, 0, 1, 2)

        connect_row = QHBoxLayout()
        connect_row.addStretch()
        self.remote_test_button = QPushButton("Conectar servidor")
        self.remote_test_button.setObjectName("validateButton")
        self.remote_test_button.clicked.connect(lambda: self._start_winscp_action("connect"))
        connect_row.addWidget(self.remote_test_button)
        remote_fields.addLayout(connect_row, 4, 0, 1, 2)
        remote_fields.setColumnStretch(0, 1)
        remote_fields.setColumnStretch(1, 1)
        remote_layout.addLayout(remote_fields)
        self.remote_status = QLabel("Sin configurar")
        self.remote_status.setObjectName("apiStatus")
        self.remote_status.setProperty("state", "idle")
        self.remote_status.setWordWrap(True)
        remote_layout.addWidget(self.remote_status)

        remote_search_label = QLabel("BUSCAR AUDIOS EN ISSABEL")
        remote_search_label.setObjectName("fieldLabel")
        self.remote_search_input = QLineEdit()
        self.remote_search_input.setPlaceholderText("Número de teléfono o parte del nombre del archivo")
        self.remote_search_input.setAccessibleName("Buscar audios en Issabel")
        remote_search_label.setBuddy(self.remote_search_input)
        remote_search_row = QHBoxLayout()
        remote_search_row.setSpacing(7)
        remote_search_row.addWidget(self.remote_search_input, 1)
        self.remote_search_button = QPushButton("Buscar")
        self.remote_search_button.setObjectName("secondaryButton")
        self.remote_search_button.setEnabled(False)
        self.remote_search_button.clicked.connect(self._start_remote_search)
        self.remote_search_input.returnPressed.connect(self._start_remote_search)
        remote_search_row.addWidget(self.remote_search_button)
        remote_layout.addWidget(remote_search_label)
        remote_layout.addLayout(remote_search_row)
        self.remote_results = QListWidget()
        self.remote_results.setObjectName("remoteResults")
        self.remote_results.setAccessibleName("Archivos encontrados en Issabel")
        self.remote_results.setMinimumHeight(105)
        self.remote_results.setMaximumHeight(190)
        self.remote_results.setAlternatingRowColors(True)
        self.remote_results.itemClicked.connect(
            lambda item: self._show_toast(f"Ubicación: {item.data(Qt.ItemDataRole.UserRole)}")
        )
        remote_layout.addWidget(self.remote_results)
        self.remote_search_status = QLabel("Conecta el servidor para buscar dentro de sus carpetas.")
        self.remote_search_status.setObjectName("apiStatus")
        self.remote_search_status.setProperty("state", "idle")
        self.remote_search_status.setWordWrap(True)
        remote_layout.addWidget(self.remote_search_status)
        for field in (
            self.remote_host, self.remote_username, self.remote_password,
        ):
            field.textChanged.connect(self._mark_remote_dirty)
        outer.addWidget(remote)

        keywords = QFrame()
        keywords.setObjectName("contentPanel")
        keywords_layout = QVBoxLayout(keywords)
        keywords_layout.setContentsMargins(20, 18, 20, 18)
        keywords_layout.setSpacing(8)
        keywords_title = QLabel("Términos sensibles")
        keywords_title.setObjectName("formTitle")
        keywords_help = QLabel("Sepáralos con comas. Las coincidencias validadas se mostrarán en verde.")
        keywords_help.setObjectName("pageSubtitle")
        self.keywords_input = QLineEdit("demanda, abogado, denuncia, queja, estafa")
        keywords_title.setBuddy(self.keywords_input)
        keywords_layout.addWidget(keywords_title)
        keywords_layout.addWidget(keywords_help)
        keywords_layout.addWidget(self.keywords_input)
        outer.addWidget(keywords)

        api_settings = QFrame()
        api_settings.setObjectName("contentPanel")
        api_layout = QVBoxLayout(api_settings)
        api_layout.setContentsMargins(20, 18, 20, 18)
        api_layout.setSpacing(12)

        api_title = QLabel("Servicios de IA")
        api_title.setObjectName("formTitle")
        api_help = QLabel(
            "Configura el proveedor, modelo y clave usados para transcribir y analizar. "
            "Las claves se cifran con Windows y solo tu usuario puede recuperarlas."
        )
        api_help.setObjectName("pageSubtitle")
        api_help.setWordWrap(True)
        api_layout.addWidget(api_title)
        api_layout.addWidget(api_help)

        services = QHBoxLayout()
        services.setSpacing(28)

        transcription = QVBoxLayout()
        transcription.setSpacing(7)
        transcription_title = QLabel("Transcripción")
        transcription_title.setObjectName("apiGroupTitle")
        transcription.addWidget(transcription_title)
        self.transcription_provider = QComboBox()
        self.transcription_provider.addItem("Deepgram", "deepgram")
        self.transcription_provider.addItem("OpenAI", "openai")
        self._select_provider(self.transcription_provider, os.getenv("SENTRY_TRANSCRIPTION_PROVIDER", "deepgram"))
        self.transcription_model = QComboBox()
        self.transcription_model.setEditable(True)
        transcription_default = ("gpt-4o-transcribe-diarize"
                                 if self.transcription_provider.currentData() == "openai" else "nova-3")
        self.transcription_model.addItem(os.getenv("SENTRY_TRANSCRIPTION_MODEL", transcription_default))
        transcription_key = "OPENAI_API_KEY" if self.transcription_provider.currentData() == "openai" else "DEEPGRAM_API_KEY"
        self.transcription_api_key = QLineEdit(os.getenv(transcription_key, ""))
        self.transcription_key_toggle, self.transcription_validate, self.transcription_api_status = self._add_api_fields(
            transcription,
            self.transcription_provider,
            self.transcription_model,
            self.transcription_api_key,
            "Clave de transcripción",
            "transcription",
        )
        self.transcription_provider.currentIndexChanged.connect(lambda: self._reset_api_service("transcription"))
        self.transcription_api_key.textChanged.connect(lambda: self._mark_api_dirty("transcription"))
        services.addLayout(transcription, 1)

        analysis = QVBoxLayout()
        analysis.setSpacing(7)
        analysis_title = QLabel("Análisis contextual")
        analysis_title.setObjectName("apiGroupTitle")
        analysis.addWidget(analysis_title)
        self.analysis_provider = QComboBox()
        self.analysis_provider.addItem("Google Gemini", "gemini")
        self.analysis_provider.addItem("OpenAI", "openai")
        self._select_provider(self.analysis_provider, os.getenv("SENTRY_ANALYSIS_PROVIDER", "gemini"))
        self.analysis_model = QComboBox()
        self.analysis_model.setEditable(True)
        analysis_default = "gpt-4o-mini" if self.analysis_provider.currentData() == "openai" else "gemini-3.5-flash-lite"
        self.analysis_model.addItem(os.getenv("SENTRY_ANALYSIS_MODEL", analysis_default))
        analysis_key = (
            os.getenv("OPENAI_API_KEY", "")
            if self.analysis_provider.currentData() == "openai"
            else os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
        )
        self.analysis_api_key = QLineEdit(analysis_key)
        self.analysis_key_toggle, self.analysis_validate, self.analysis_api_status = self._add_api_fields(
            analysis,
            self.analysis_provider,
            self.analysis_model,
            self.analysis_api_key,
            "Clave de análisis",
            "analysis",
        )
        self.analysis_provider.currentIndexChanged.connect(lambda: self._reset_api_service("analysis"))
        self.analysis_api_key.textChanged.connect(lambda: self._mark_api_dirty("analysis"))
        services.addLayout(analysis, 1)
        api_layout.addLayout(services)
        outer.addWidget(api_settings)

        save_row = QHBoxLayout()
        save_row.addStretch()
        save = QPushButton("Guardar ajustes")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save_settings)
        save_row.addWidget(save)
        outer.addLayout(save_row)
        outer.addStretch()
        scroll.setWidget(page)
        return scroll

    def _add_api_fields(
        self,
        layout: QVBoxLayout,
        provider: QComboBox,
        model: QComboBox,
        api_key: QLineEdit,
        key_label: str,
        service: str,
    ) -> tuple[QPushButton, QPushButton, QLabel]:
        for label_text, field in (("Proveedor", provider), ("Modelo", model)):
            label = QLabel(label_text)
            label.setObjectName("fieldLabel")
            label.setBuddy(field)
            layout.addWidget(label)
            layout.addWidget(field)

        label = QLabel(key_label)
        label.setObjectName("fieldLabel")
        label.setBuddy(api_key)
        api_key.setEchoMode(QLineEdit.EchoMode.Password)
        api_key.setPlaceholderText("Introduce la clave API")
        api_key.setAccessibleName(key_label)
        key_row = QHBoxLayout()
        key_row.setSpacing(7)
        key_row.addWidget(api_key, 1)
        toggle = QPushButton("Mostrar")
        toggle.setObjectName("secondaryButton")
        toggle.setCheckable(True)
        toggle.setAccessibleName(f"Mostrar {key_label.lower()}")
        toggle.toggled.connect(lambda visible, field=api_key, button=toggle: self._set_secret_visibility(field, button, visible))
        key_row.addWidget(toggle)
        validate = QPushButton("Validar")
        validate.setObjectName("validateButton")
        validate.setAccessibleName(f"Validar {key_label.lower()}")
        validate.clicked.connect(lambda _checked=False, name=service: self._validate_api(name))
        key_row.addWidget(validate)
        layout.addWidget(label)
        layout.addLayout(key_row)
        status = QLabel("Sin validar")
        status.setObjectName("apiStatus")
        status.setProperty("state", "idle")
        status.setWordWrap(True)
        layout.addWidget(status)
        return toggle, validate, status

    @staticmethod
    def _select_provider(combo: QComboBox, requested: str) -> None:
        normalized = requested.strip().casefold()
        aliases = {"google gemini": "gemini", "deepgram": "deepgram", "openai": "openai"}
        index = combo.findData(aliases.get(normalized, normalized))
        combo.setCurrentIndex(max(0, index))

    def _api_controls(self, service: str) -> tuple[QComboBox, QComboBox, QLineEdit, QPushButton, QLabel]:
        if service == "transcription":
            return (
                self.transcription_provider,
                self.transcription_model,
                self.transcription_api_key,
                self.transcription_validate,
                self.transcription_api_status,
            )
        return (
            self.analysis_provider,
            self.analysis_model,
            self.analysis_api_key,
            self.analysis_validate,
            self.analysis_api_status,
        )

    def _reset_api_service(self, service: str) -> None:
        provider, model, key, button, status = self._api_controls(service)
        defaults = {
            ("transcription", "deepgram"): "nova-3",
            ("transcription", "openai"): "gpt-4o-transcribe-diarize",
            ("analysis", "gemini"): "gemini-3.5-flash-lite",
            ("analysis", "openai"): "gpt-4o-mini",
        }
        model.clear()
        model.addItem(defaults[(service, str(provider.currentData()))])
        key.clear()
        toggle = self.transcription_key_toggle if service == "transcription" else self.analysis_key_toggle
        toggle.setChecked(False)
        key.setEchoMode(QLineEdit.EchoMode.Password)
        button.setEnabled(True)
        button.setText("Validar")
        self._set_api_status(status, "Sin validar", "idle")

    def _restore_credentials(self) -> None:
        try:
            saved = self.database.credentials()
            for service in ("transcription", "analysis"):
                item = saved.get(service)
                if not item:
                    continue
                provider, model, field, _button, status = self._api_controls(service)
                provider.blockSignals(True)
                self._select_provider(provider, item["provider"])
                provider.blockSignals(False)
                model.clear()
                model.addItem(item["model"])
                field.setText(unprotect(item["encrypted_key"]))
                self._set_api_status(status, "Clave recuperada de forma segura · pendiente de validar", "idle")
        except (SecretStoreError, sqlite3.Error, OSError) as exc:
            self._show_toast(f"No se pudieron recuperar las claves: {exc}")

    def _persist_credentials(self, services=("transcription", "analysis")) -> int:
        saved = 0
        for service in services:
            provider, model, field, _button, _status = self._api_controls(service)
            key = field.text().strip()
            if not key:
                continue
            self.database.save_credential(service, str(provider.currentData()), model.currentText().strip(), protect(key))
            saved += 1
        return saved

    def _remote_config(self) -> dict[str, str]:
        return {
            "host": self.remote_host.text().strip(),
            "port": self.remote_port.text().strip() or "22",
            "username": self.remote_username.text().strip(),
            "password": self.remote_password.text(),
            "remote_path": self.remote_path.text().strip() or "/",
            "fingerprint": self.remote_fingerprint.text().strip(),
        }

    def _restore_remote_connection(self) -> None:
        try:
            saved = self.database.remote_connection()
            if not saved:
                return
            fields = (
                (self.remote_host, saved["host"]),
                (self.remote_port, str(saved["port"])),
                (self.remote_username, saved["username"]),
                (self.remote_password, unprotect(saved["encrypted_password"])),
                (self.remote_path, saved["remote_path"]),
                (self.remote_fingerprint, saved["host_fingerprint"]),
            )
            for field, value in fields:
                field.blockSignals(True)
                field.setText(value)
                field.blockSignals(False)
            self._set_api_status(
                self.remote_status,
                "Configuración cifrada recuperada · prueba la conexión antes de buscar grabaciones.",
                "idle",
            )
            self.remote_search_button.setEnabled(True)
            self._set_api_status(
                self.remote_search_status,
                f"Servidor listo en {saved['remote_path']} · con una base activa se consultan solo sus fechas",
                "idle",
            )
        except (SecretStoreError, sqlite3.Error, OSError, KeyError) as exc:
            self._set_api_status(self.remote_status, f"No se pudo recuperar la conexión: {exc}", "error")

    def _persist_remote_connection(self) -> int:
        config = self._remote_config()
        identifying = (config["host"], config["password"], config["fingerprint"])
        if not any(identifying):
            return 0
        missing = [
            label for label, value in (
                ("IP o servidor", config["host"]),
                ("usuario", config["username"]),
                ("contraseña", config["password"]),
                ("huella SSH", config["fingerprint"]),
            ) if not value
        ]
        if missing == ["huella SSH"]:
            raise ValueError("Pulsa Conectar servidor antes de guardar la conexión WinSCP.")
        if missing:
            raise ValueError(f"Completa la conexión WinSCP: {', '.join(missing)}.")
        try:
            port = int(config["port"])
        except ValueError as exc:
            raise ValueError("El puerto SFTP debe ser un número entre 1 y 65535.") from exc
        if not 1 <= port <= 65535:
            raise ValueError("El puerto SFTP debe estar entre 1 y 65535.")
        self.database.save_remote_connection(
            config["host"], port, config["username"], protect(config["password"]),
            config["remote_path"], config["fingerprint"],
        )
        return 1

    def _mark_remote_dirty(self) -> None:
        if self.winscp_worker is None and hasattr(self, "remote_status"):
            self._set_api_status(self.remote_status, "Cambios pendientes de probar y guardar", "idle")

    def _set_winscp_busy(self, busy: bool) -> None:
        for control in (
            self.remote_host, self.remote_username, self.remote_password,
            self.remote_password_toggle, self.remote_test_button,
        ):
            control.setEnabled(not busy)

    def _start_winscp_action(self, mode: str) -> None:
        if self.winscp_worker is not None:
            return
        config = self._remote_config()
        self._set_winscp_busy(True)
        action = "Obteniendo la huella SSH" if mode == "fingerprint" else "Conectando con WinSCP"
        self._set_api_status(self.remote_status, f"{action}…", "loading")
        self.winscp_worker = WinSCPWorker(mode, config, self)
        self.winscp_worker.succeeded.connect(self._winscp_succeeded)
        self.winscp_worker.failed.connect(self._winscp_failed)
        self.winscp_worker.finished.connect(self._winscp_finished)
        self.winscp_worker.start()

    def _winscp_succeeded(self, mode: str, result: str) -> None:
        if mode == "fingerprint":
            self.remote_fingerprint.setText(result)
            self._set_api_status(
                self.remote_status,
                "Huella obtenida. Verifícala con el administrador del servidor y luego prueba la conexión.",
                "valid",
            )
            self._show_toast("Huella SSH obtenida; verifícala antes de conectar")
            return
        if mode == "connect":
            self.remote_fingerprint.blockSignals(True)
            self.remote_fingerprint.setText(result)
            self.remote_fingerprint.blockSignals(False)
        try:
            self._persist_remote_connection()
        except (ValueError, SecretStoreError, sqlite3.Error, OSError) as exc:
            self._set_api_status(self.remote_status, f"Conectó, pero no se pudo guardar: {exc}", "error")
            return
        self._set_api_status(
            self.remote_status,
            f"Conexión correcta · credenciales cifradas · huella {self.remote_fingerprint.text()}",
            "valid",
        )
        self.remote_search_button.setEnabled(True)
        self._set_api_status(
            self.remote_search_status,
            f"Servidor listo en {self.remote_path.text()} · con una base activa se consultan solo sus fechas",
            "valid",
        )
        self._show_toast("Servidor WinSCP conectado y guardado de forma segura")

    def _winscp_failed(self, error: str) -> None:
        self._set_api_status(self.remote_status, f"No se pudo conectar: {error}", "error")
        self._show_toast(f"WinSCP: {error}")

    def _winscp_finished(self) -> None:
        worker = self.winscp_worker
        self.winscp_worker = None
        self._set_winscp_busy(False)
        if worker is not None:
            worker.deleteLater()

    def _start_remote_search(self) -> None:
        if self.remote_search_worker is not None:
            return
        query = self.remote_search_input.text().strip()
        if len(query) < 3:
            self._set_api_status(
                self.remote_search_status,
                "Escribe al menos 3 caracteres del teléfono o del nombre del archivo.",
                "error",
            )
            self.remote_search_input.setFocus()
            return
        config = self._remote_config()
        try:
            if not config["fingerprint"]:
                raise ValueError("Conecta primero el servidor de Issabel.")
        except ValueError as exc:
            self._set_api_status(self.remote_search_status, str(exc), "error")
            return
        directories = issabel_directories(config["remote_path"], self.active_base_index.dates)
        if directories:
            config["remote_paths"] = directories
            config["recursive"] = False
        self.remote_search_input.setEnabled(False)
        self.remote_search_button.setEnabled(False)
        self.remote_results.clear()
        self._set_api_status(
            self.remote_search_status,
            (
                f"Buscando “{query}” en {len(directories)} carpeta(s) correspondientes a las fechas de la base…"
                if directories else f"Buscando “{query}” en todas las carpetas de Issabel…"
            ),
            "loading",
        )
        self.remote_search_worker = RemoteSearchWorker(config, query, self)
        self.remote_search_worker.succeeded.connect(self._remote_search_succeeded)
        self.remote_search_worker.failed.connect(self._remote_search_failed)
        self.remote_search_worker.finished.connect(self._remote_search_finished)
        self.remote_search_worker.start()

    def _remote_search_succeeded(self, results: object) -> None:
        files = results if isinstance(results, list) else []
        for result in files:
            if not isinstance(result, dict):
                continue
            path = str(result.get("path", ""))
            size = int(result.get("size", 0) or 0)
            modified = str(result.get("modified", ""))
            item = QListWidgetItem(f"{Path(path).name}  ·  {size / 1024:.1f} KB  ·  {modified}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.remote_results.addItem(item)
        count = self.remote_results.count()
        message = (
            f"{count} archivo{'s' if count != 1 else ''} encontrado{'s' if count != 1 else ''}. "
            "Selecciona uno para ver su ubicación completa."
            if count else "No se encontraron audios con ese número o nombre."
        )
        self._set_api_status(self.remote_search_status, message, "valid" if count else "idle")

    def _remote_search_failed(self, error: str) -> None:
        self._set_api_status(self.remote_search_status, f"No se pudo buscar: {error}", "error")
        self._show_toast(f"Issabel: {error}")

    def _remote_search_finished(self) -> None:
        worker = self.remote_search_worker
        self.remote_search_worker = None
        self.remote_search_input.setEnabled(True)
        self.remote_search_button.setEnabled(bool(self.remote_fingerprint.text().strip()))
        if worker is not None:
            worker.deleteLater()

    def _mark_api_dirty(self, service: str) -> None:
        _provider, _model, _key, button, status = self._api_controls(service)
        if button.isEnabled():
            button.setText("Validar")
            self._set_api_status(status, "Sin validar", "idle")

    def _set_api_busy(self, service: str, busy: bool) -> None:
        provider, model, api_key, button, _status = self._api_controls(service)
        toggle = self.transcription_key_toggle if service == "transcription" else self.analysis_key_toggle
        for control in (provider, model, api_key, button, toggle):
            control.setEnabled(not busy)

    def _validate_api(self, service: str) -> None:
        provider, _model, api_key, button, status = self._api_controls(service)
        key = api_key.text().strip()
        if not key:
            self._set_api_status(status, "Introduce una clave antes de validar.", "error")
            api_key.setFocus()
            return

        provider_id = str(provider.currentData())
        self._set_api_busy(service, True)
        button.setText("Validando…")
        self._set_api_status(status, f"Comprobando {provider.currentText()}…", "loading")
        if provider_id == "deepgram":
            self._send_api_request(service, provider_id, key, "https://api.deepgram.com/v1/projects", "validate")
            return
        endpoint = {
            "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
            "openai": "https://api.openai.com/v1/models",
        }[provider_id]
        self._send_api_request(service, provider_id, key, endpoint, "models")

    def _send_api_request(self, service: str, provider: str, key: str, endpoint: str, stage: str) -> None:
        request = QNetworkRequest(QUrl(endpoint))
        request.setTransferTimeout(15000)
        if provider == "deepgram":
            request.setRawHeader(QByteArray(b"Authorization"), QByteArray(f"Token {key}".encode()))
        elif provider == "gemini":
            request.setRawHeader(QByteArray(b"x-goog-api-key"), QByteArray(key.encode()))
        else:
            request.setRawHeader(QByteArray(b"Authorization"), QByteArray(f"Bearer {key}".encode()))
        reply = self.network.get(request)
        reply.finished.connect(lambda: self._finish_api_request(reply, service, provider, key, stage))

    def _finish_api_request(
        self,
        reply: QNetworkReply,
        service: str,
        provider: str,
        key: str,
        stage: str,
    ) -> None:
        _provider, model, _api_key, button, status = self._api_controls(service)
        status_code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        raw = bytes(reply.readAll().data())
        error = reply.error()
        reply.deleteLater()

        if error != QNetworkReply.NetworkError.NoError or not isinstance(status_code, int) or status_code >= 400:
            if status_code in (401, 403):
                message = "Clave rechazada. Revisa la credencial y vuelve a intentar."
            elif status_code == 429:
                message = "Límite temporal alcanzado. Intenta nuevamente en unos minutos."
            else:
                message = "No se pudo conectar. Revisa Internet y vuelve a intentar."
            self._set_api_busy(service, False)
            button.setText("Reintentar")
            self._set_api_status(status, message, "error")
            return

        if provider == "deepgram" and stage == "validate":
            self._send_api_request(service, provider, key, "https://api.deepgram.com/v1/models", "models")
            return

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._set_api_busy(service, False)
            button.setText("Reintentar")
            self._set_api_status(status, "La clave es válida, pero el catálogo recibido no pudo leerse.", "error")
            return

        models = self._extract_models(provider, service, payload)
        current_model = model.currentText().strip()
        if models:
            model.clear()
            model.addItems(models)
            preferred = self._preferred_model(provider, service, current_model, models)
            model.setCurrentText(preferred)
            message = f"Clave válida · {len(models)} modelos disponibles"
        else:
            message = "Clave válida · escribe manualmente el modelo que deseas usar"
        self._set_api_busy(service, False)
        button.setText("Validada")
        self._set_api_status(status, message, "valid")
        try:
            self._persist_credentials((service,))
        except (SecretStoreError, sqlite3.Error, OSError) as exc:
            self._set_api_status(status, f"Clave válida, pero no se pudo guardar: {exc}", "error")

    @staticmethod
    def _extract_models(provider: str, service: str, payload: object) -> list[str]:
        if not isinstance(payload, dict):
            return []
        if provider == "deepgram":
            entries = payload.get("stt", [])
            names = [entry.get("canonical_name") or entry.get("name") for entry in entries if isinstance(entry, dict)]
        elif provider == "gemini":
            entries = payload.get("models", [])
            names = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                methods = entry.get("supportedGenerationMethods", [])
                if service == "analysis" and methods and "generateContent" not in methods:
                    continue
                name = entry.get("name")
                names.append(name.removeprefix("models/") if isinstance(name, str) else None)
        else:
            entries = payload.get("data", [])
            names = [entry.get("id") for entry in entries if isinstance(entry, dict)]
            if service == "transcription":
                transcription_models = [name for name in names if isinstance(name, str) and ("transcrib" in name or "whisper" in name)]
                names = transcription_models or names
        return list(dict.fromkeys(name for name in names if isinstance(name, str) and name))

    @staticmethod
    def _preferred_model(provider: str, service: str, current: str, models: list[str]) -> str:
        if current in models:
            return current
        hints = {
            ("deepgram", "transcription"): ("nova-3",),
            ("openai", "transcription"): ("mini-transcribe", "transcribe", "whisper"),
            ("gemini", "analysis"): ("flash",),
            ("openai", "analysis"): ("gpt-4o-mini", "mini"),
        }[(provider, service)]
        return next((name for hint in hints for name in models if hint in name), models[0])

    @staticmethod
    def _set_api_status(label: QLabel, message: str, state: str) -> None:
        label.setText(message)
        label.setProperty("state", state)
        label.style().unpolish(label)
        label.style().polish(label)

    @staticmethod
    def _set_secret_visibility(field: QLineEdit, button: QPushButton, visible: bool) -> None:
        field.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        button.setText("Ocultar" if visible else "Mostrar")
        button.setAccessibleName(f"{'Ocultar' if visible else 'Mostrar'} {field.accessibleName().lower()}")

    def _switch_page(self, key: str) -> None:
        page_index = {"audit": 0, "reports": 1, "config": 2, "bases": 3}[key]
        self.pages.setCurrentIndex(page_index)
        for name, button in self.nav_buttons.items():
            button.setChecked(name == key)

    def _activate_audio_base(self, path: str, index_payload: object) -> None:
        self.active_base_path = Path(path).resolve()
        if isinstance(index_payload, BaseAudioIndex):
            self.active_base_index = index_payload
        else:
            try:
                self.active_base_index = load_hoja1_audio_index(self.active_base_path)
            except (OSError, ValueError, KeyError) as exc:
                self._show_toast(f"No se pudo leer teléfono y fecha de Hoja1: {exc}")
                return
        self.active_base_phones = self.active_base_index.phones
        if not self.active_base_phones:
            self._show_toast("La base seleccionada no contiene teléfonos en Hoja1")
            return
        if self._source_key() != "issabel" and not self._selected_directory():
            self._show_toast("Base seleccionada. Configura ahora la carpeta del origen elegido")
            self._switch_page("config")
            (self.nas_directory if self._source_key() == "nas" else self.config_directory).setFocus()
            return
        self._switch_page("audit")
        self._show_toast(
            f"Buscando {len(self.active_base_phones):,} teléfonos en {len(self.active_base_index.dates):,} fecha(s)"
        )
        self._scan_directory()

    def _filter_calls(self) -> None:
        query = self.search_input.text().strip().casefold()
        status = self.status_filter.currentData()
        visible = 0
        first_visible: QListWidgetItem | None = None
        for row, call in enumerate(self.call_records):
            searchable = self.call_search_cache.get(call.call_id)
            if searchable is None:
                searchable = self._searchable_call_text(call)
                self.call_search_cache[call.call_id] = searchable
            matches_query = not query or query in searchable
            matches_status = (
                status == "all"
                or (status == "alert" and (call.category_code == "ALERTA" or call.sensitive))
                or (status == "mailbox" and call.category_code == "BUZON")
                or (status == "normal" and (call.category_code == "NORMAL" or
                                             (not call.category_code and not call.sensitive and call.risk != "Pendiente")))
                or (status == "pending" and call.category_code in {"PENDIENTE", "ERROR", ""})
            )
            item = self.call_list.item(row)
            item.setHidden(not (matches_query and matches_status))
            if not item.isHidden():
                visible += 1
                first_visible = first_visible or item
        self.call_count.setText(f"{visible} {'llamada' if visible == 1 else 'llamadas'}")
        current = self.call_list.currentItem()
        if (current is None or current.isHidden()) and first_visible is not None:
            self.call_list.setCurrentItem(first_visible)
        QTimer.singleShot(0, self._materialize_visible_cards)

    def _on_call_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        previous_id = _previous.data(Qt.ItemDataRole.UserRole) if _previous is not None else self.selected_call_id
        previous_card = self.call_cards.get(previous_id)
        if previous_card is not None:
            previous_card.set_selected(False)
        if current is None:
            self.selected_call_id = None
            return
        call_id = current.data(Qt.ItemDataRole.UserRole)
        self.selected_call_id = call_id
        card = self.call_cards.get(call_id) or self._materialize_call_card(self.call_list.row(current))
        if card is not None:
            card.set_selected(True)
        self._show_call(self.calls[call_id])

    def _select_call_by_id(self, call_id: int) -> None:
        for row in range(self.call_list.count()):
            item = self.call_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == call_id:
                self.call_list.setCurrentItem(item)
                return

    def _show_empty_state(
        self,
        title: str = "Sin llamadas",
        message: str = "Configura una carpeta y escanéala para comenzar la auditoría.",
    ) -> None:
        self.current_call = None
        self.current_second = 0
        self.current_duration = 0
        self._stop_playback()
        self.media_player.setSource(QUrl())
        self.empty_detail_title.setText(title)
        self.empty_detail_text.setText(message)
        self.detail_pages.setCurrentIndex(0)
        self.play_button.setEnabled(False)
        self.original_button.setEnabled(False)
        self.reviewed_button.setEnabled(False)
        self.jump_button.setEnabled(False)
        self.transcript_rows.clear()
        self.active_transcript_index = -1
        self.active_heard_words = -1
        self._update_category_metrics()

    def _set_analysis_controls_locked(self, locked: bool) -> None:
        if locked:
            self._stop_playback()
        has_audio = (
            self.current_call is not None
            and self.current_call.source_path is not None
            and self.current_call.source_path.is_file()
        )
        self.play_button.setEnabled(not locked and has_audio)
        self.original_button.setEnabled(not locked and has_audio)
        self.reviewed_button.setEnabled(not locked and self.current_call is not None)
        self.jump_button.setEnabled(
            not locked and self.current_call is not None and self.current_call.hit_second is not None
        )
        self.timeline.setEnabled(not locked)
        for _line, row, _label in self.transcript_rows:
            row.setEnabled(not locked)

    def _show_call(self, call: CallRecord) -> None:
        self._stop_playback()
        self.current_call = call
        self.detail_pages.setCurrentIndex(1)
        self.current_second = 0
        self.current_duration = call.duration
        self.transcript_rows = []
        self.active_transcript_index = -1
        self.active_heard_words = -1
        source = call.source_path
        has_audio = source is not None and source.is_file()
        self.play_button.setEnabled(has_audio)
        self.original_button.setEnabled(has_audio)
        self.reviewed_button.setEnabled(True)
        self.media_player.setSource(QUrl.fromLocalFile(str(source)) if has_audio else QUrl())
        self.filename_label.setText(call.filename)
        badge_text = ("Etiquetas: " + ", ".join(call.tags)) if call.tags else (
            "Término sensible" if call.sensitive else "Sin alerta crítica")
        self.risk_badge.setText("Pendiente de análisis" if call.risk == "Pendiente" else badge_text)
        self.risk_badge.setProperty("sensitive", call.sensitive)
        self.risk_badge.style().unpolish(self.risk_badge)
        self.risk_badge.style().polish(self.risk_badge)
        self.duration_label.setText(format_time(call.duration))
        self.customer_value.setText(call.customer)
        self.time_value.setText(call.clock)
        self.risk_value.setText(call.risk)
        self.summary_label.setText(call.summary)
        self.timeline.set_audio(self.current_duration, call.hit_second)
        self._update_time_display()

        if call.hit_second is None:
            self.jump_button.setText("Sin evidencia sensible")
            self.jump_button.setEnabled(False)
        else:
            self.jump_button.setText(f"Ir al momento {format_time(call.hit_second)} · {call.keyword}")
            self.jump_button.setEnabled(True)
        self._render_transcript(call)
        self._set_analysis_controls_locked(self.analysis_worker is not None)

    def _render_transcript(self, call: CallRecord) -> None:
        while self.transcript_layout.count():
            item = self.transcript_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.critical_line = None
        self.transcript_rows = []
        self.active_transcript_index = -1
        self.active_heard_words = -1
        sensitive_terms = tuple(term for term in call.tags if term.strip()) if call.sensitive else ()
        if call.sensitive and not sensitive_terms and call.keyword.strip():
            sensitive_terms = (call.keyword,)
        for line in call.transcript:
            row = TranscriptRow(line.second)
            row.setProperty("critical", line.critical)
            row.setProperty("playbackState", "upcoming")
            row.seek_requested.connect(self._seek_audio)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 10, 12, 10)
            row_layout.setSpacing(12)

            stamp = QLabel(format_time(line.second))
            stamp.setObjectName("criticalStamp" if line.critical else "transcriptStamp")
            stamp.setFixedWidth(42)
            speaker = QLabel(line.speaker)
            speaker.setObjectName("speaker")
            speaker.setFixedWidth(72)
            text = QLabel(self._transcript_progress_html(line, 0, sensitive_terms))
            text.setTextFormat(Qt.TextFormat.RichText)
            text.setWordWrap(True)
            text.setObjectName("transcriptText")
            text.setMinimumWidth(0)
            text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

            row_layout.addWidget(stamp, 0, Qt.AlignmentFlag.AlignTop)
            row_layout.addWidget(speaker, 0, Qt.AlignmentFlag.AlignTop)
            row_layout.addWidget(text, 1)
            self.transcript_layout.addWidget(row)
            self.transcript_rows.append((line, row, text))
            if line.critical and self.critical_line is None:
                self.critical_line = row
        self.transcript_layout.addStretch()
        self._sync_transcript(self.current_second)

    def _highlight_keywords(self, text: str, keywords: tuple[str, ...]) -> str:
        palette = theme_colors(self.theme)
        terms = sorted({term.strip() for term in keywords if term.strip()}, key=len, reverse=True)
        if not terms:
            return html.escape(text)
        pattern = re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)
        fragments: list[str] = []
        cursor = 0
        for match in pattern.finditer(text):
            fragments.append(html.escape(text[cursor:match.start()]))
            fragments.append(
                f"<span style='color:{palette['green_accessible']}; font-weight:700'>"
                f"{html.escape(match.group(0))}</span>"
            )
            cursor = match.end()
        fragments.append(html.escape(text[cursor:]))
        return "".join(fragments)

    def _sync_transcript(self, position_seconds: float, scroll: bool = False) -> None:
        active = next(
            (index for index in range(len(self.transcript_rows) - 1, -1, -1)
             if position_seconds >= self.transcript_rows[index][0].second),
            -1,
        )
        terms = ()
        if self.current_call is not None and self.current_call.sensitive:
            terms = tuple(term for term in self.current_call.tags if term.strip())
            if not terms and self.current_call.keyword.strip():
                terms = (self.current_call.keyword,)
        active_changed = active != self.active_transcript_index
        if active >= 0:
            heard_words = self._heard_word_count(active, position_seconds)
        else:
            heard_words = 0
        if not active_changed and heard_words == self.active_heard_words:
            if scroll and active >= 0:
                self.transcript_scroll.ensureWidgetVisible(self.transcript_rows[active][1], 0, 50)
            return
        indexes = range(len(self.transcript_rows)) if active_changed else (active,)
        for index in indexes:
            if index < 0:
                continue
            line, row, text_label = self.transcript_rows[index]
            state = "played" if index < active else "active" if index == active else "upcoming"
            if row.property("playbackState") != state:
                row.setProperty("playbackState", state)
                row.style().unpolish(row)
                row.style().polish(row)
            word_count = len(line.text.split())
            row_heard_words = (
                word_count if state == "played"
                else heard_words if state == "active"
                else 0
            )
            progress_html = self._transcript_progress_html(line, row_heard_words, terms)
            if text_label.text() != progress_html:
                text_label.setText(progress_html)
        if scroll and active >= 0 and active != self.active_transcript_index:
            self.transcript_scroll.ensureWidgetVisible(self.transcript_rows[active][1], 0, 50)
        self.active_transcript_index = active
        self.active_heard_words = heard_words

    def _heard_word_count(self, index: int, position_seconds: float) -> int:
        line = self.transcript_rows[index][0]
        word_count = len(line.text.split())
        if not word_count:
            return 0
        if line.word_seconds:
            return min(word_count, bisect_right(line.word_seconds, position_seconds))
        next_second = (
            self.transcript_rows[index + 1][0].second
            if index + 1 < len(self.transcript_rows)
            else self.current_duration
        )
        duration = max(1, next_second - line.second)
        ratio = max(0.0, min(1.0, (position_seconds - line.second) / duration))
        return min(word_count, max(1, int(ratio * word_count) + 1))

    def _transcript_progress_html(
        self, line: TranscriptLine, heard_words: int, keywords: tuple[str, ...]
    ) -> str:
        palette = theme_colors(self.theme)
        word_matches = list(re.finditer(r"\S+", line.text))
        sensitive_indexes: set[int] = set()
        for term in {term.strip() for term in keywords if term.strip()}:
            for match in re.finditer(re.escape(term), line.text, re.IGNORECASE):
                sensitive_indexes.update(
                    index for index, word in enumerate(word_matches)
                    if word.start() < match.end() and word.end() > match.start()
                )
        rendered = []
        for index, word in enumerate(word_matches):
            safe_word = html.escape(word.group(0))
            styles = []
            if index < heard_words:
                styles.append(f"color:{palette['green_accessible']}")
            if index in sensitive_indexes:
                styles.extend((f"color:{palette['green_accessible']}", "font-weight:700"))
            rendered.append(f"<span style='{';'.join(dict.fromkeys(styles))}'>{safe_word}</span>" if styles else safe_word)
        return " ".join(rendered)

    def _toggle_playback(self) -> None:
        if self.analysis_worker is not None or self.current_call is None:
            return
        source = self.current_call.source_path
        if source is None or not source.is_file():
            self._show_toast("Selecciona un audio real antes de reproducir")
            return

        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            return

        if self.media_player.duration() > 0 and self.media_player.position() >= self.media_player.duration():
            self.media_player.setPosition(0)
        self.media_player.play()

    def _stop_playback(self) -> None:
        self.media_player.stop()
        if hasattr(self, "play_button"):
            self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.play_button.setAccessibleName("Reproducir audio")

    def _on_player_position_changed(self, position_ms: int) -> None:
        self.current_second = max(0, position_ms // 1000)
        self.timeline.set_position(self.current_second)
        self._update_time_display()
        self._sync_transcript(
            position_ms / 1000,
            scroll=self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
        )

    def _on_player_duration_changed(self, duration_ms: int) -> None:
        if self.current_call is None or self.current_call.source_path is None or duration_ms <= 0:
            return
        self.current_duration = max(1, round(duration_ms / 1000))
        self.duration_label.setText(format_time(self.current_duration))
        self.timeline.set_audio(self.current_duration, self.current_call.hit_second)
        self.timeline.set_position(self.current_second)
        self._update_time_display()
        self._sync_transcript(self.current_second)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if not hasattr(self, "play_button"):
            return
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        icon = QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay
        self.play_button.setIcon(self.style().standardIcon(icon))
        self.play_button.setAccessibleName("Pausar audio" if playing else "Reproducir audio")

    def _on_player_error(self, error: QMediaPlayer.Error, error_string: str) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        detail = error_string.strip() or "formato no compatible"
        self._show_toast(f"No se pudo reproducir el audio: {detail}")

    def _seek_audio(self, seconds: int) -> None:
        if self.analysis_worker is not None or self.current_call is None:
            return
        self.current_second = max(0, min(seconds, self.current_duration))
        source = self.current_call.source_path
        if source is not None and source.is_file():
            self.media_player.setPosition(self.current_second * 1000)
        self.timeline.set_position(self.current_second)
        self._update_time_display()
        self._sync_transcript(self.current_second, scroll=True)

    def _update_time_display(self) -> None:
        self.time_display.setText(f"{format_time(self.current_second)} / {format_time(self.current_duration)}")

    def _open_original_audio(self) -> None:
        if self.analysis_worker is not None or self.current_call is None:
            return
        source = self.current_call.source_path
        if source is None or not source.is_file():
            self._show_toast("El archivo de audio ya no está disponible")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(source))):
            self._show_toast("Windows no pudo abrir el archivo con el reproductor predeterminado")

    def _jump_to_evidence(self) -> None:
        if self.analysis_worker is not None or self.current_call is None or self.current_call.hit_second is None:
            return
        self._seek_audio(self.current_call.hit_second)
        self._show_toast(f"Evidencia localizada en {format_time(self.current_call.hit_second)}")

    def _choose_directory(self) -> None:
        source = self._source_key()
        if source == "issabel":
            self._switch_page("config")
            self.remote_search_input.setFocus()
            self._show_toast("Issabel usa la conexión SFTP configurada")
            return
        self._choose_source_directory(source)

    def _source_key(self) -> str:
        return str(self.audio_source.currentData()) if hasattr(self, "audio_source") else "local"

    def _selected_directory(self) -> str:
        return self.nas_directory.text().strip() if self._source_key() == "nas" else self.config_directory.text().strip()

    def _choose_source_directory(self, source: str) -> None:
        field = self.nas_directory if source == "nas" else self.config_directory
        caption = "Seleccionar carpeta del NAS" if source == "nas" else "Seleccionar carpeta local"
        selected = QFileDialog.getExistingDirectory(self, caption, field.text())
        if not selected:
            return
        normalized = selected.replace("/", "\\")
        field.setText(normalized)
        self._set_directory_display(normalized)
        self._show_toast("Carpeta del NAS actualizada" if source == "nas" else "Carpeta local actualizada")

    def _source_changed(self) -> None:
        source = self._source_key()
        self.local_directory_row.setVisible(source == "local")
        self.nas_directory_row.setVisible(source == "nas")
        self.issabel_source_note.setVisible(source == "issabel")
        if source == "issabel":
            self._set_directory_display("Issabel")
        else:
            self._set_directory_display(self._selected_directory())

    def _set_directory_display(self, directory: str) -> None:
        source = self._source_key()
        if source == "issabel":
            self.directory_label.setText("Issabel")
            remote_path = self.remote_path.text().strip() if hasattr(self, "remote_path") else ""
            self.directory_label.setToolTip(remote_path or "/var/spool/asterisk/monitor/")
            return
        label = "NAS" if source == "nas" and directory else Path(directory).name if directory else "Sin directorio"
        self.directory_label.setText(label)
        self.directory_label.setToolTip(directory or "No se ha configurado una carpeta")

    def _on_theme_changed(self) -> None:
        theme_name = self._set_theme(str(self.theme_combo.currentData()))
        try:
            self.database.save_settings({"theme": theme_name})
        except (sqlite3.Error, OSError, ValueError) as exc:
            self._show_toast(f"No se pudo guardar el tema: {exc}")

    def _set_theme(self, theme_name: str) -> str:
        self.theme = normalize_theme(theme_name)
        PALETTE.clear()
        PALETTE.update(theme_colors(self.theme))
        app = QApplication.instance()
        if app is not None:
            apply_app_theme(app, self.theme)
        self.setStyleSheet(self._stylesheet(self.theme))
        if hasattr(self, "timeline"):
            self.timeline.set_theme(self.theme)
        if hasattr(self, "analyze_button"):
            self.analyze_button.set_theme(self.theme)
        if hasattr(self, "theme_combo"):
            index = self.theme_combo.findData(self.theme)
            if index >= 0 and self.theme_combo.currentIndex() != index:
                self.theme_combo.blockSignals(True)
                self.theme_combo.setCurrentIndex(index)
                self.theme_combo.blockSignals(False)
        if "config" in self.nav_buttons:
            assets_path = Path(__file__).resolve().parents[1] / "assets"
            self.bases_button.setIcon(
                QIcon(str(assets_path / theme_asset(self.theme, "bases")))
            )
            self.nav_buttons["config"].setIcon(
                QIcon(str(assets_path / theme_asset(self.theme, "settings")))
            )
        if self.current_call is not None:
            self._sync_transcript(self.current_second)
        return self.theme

    def _save_settings(self) -> None:
        source = self._source_key()
        directory = self._selected_directory()
        keywords = [word.strip() for word in self.keywords_input.text().split(",") if word.strip()]
        if source != "issabel" and not directory:
            self._show_toast("Selecciona una carpeta para el origen elegido")
            (self.nas_directory if source == "nas" else self.config_directory).setFocus()
            return
        if not keywords:
            self._show_toast("Agrega al menos un término sensible")
            self.keywords_input.setFocus()
            return
        self._set_directory_display(directory if source != "issabel" else "Issabel")
        try:
            self.database.save_settings({
                "audio_directory": self.config_directory.text().strip(),
                "nas_directory": self.nas_directory.text().strip(),
                "audio_source": source,
                "keywords": ", ".join(keywords),
                "theme": self.theme,
            })
            api_count = self._persist_credentials()
            remote_count = self._persist_remote_connection()
        except (ValueError, SecretStoreError, sqlite3.Error, OSError) as exc:
            self._show_toast(f"No se pudieron guardar los ajustes: {exc}")
            return
        remote_note = " · WinSCP protegido" if remote_count else ""
        self._show_toast(f"Ajustes guardados · {api_count}/2 claves API protegidas{remote_note}")

    def _scan_directory(self) -> None:
        if self.active_base_path is None or not self.active_base_phones:
            self._show_toast("Selecciona primero en Bases el Excel transformado que deseas relacionar")
            self._switch_page("bases")
            return
        source = self._source_key()
        if source == "issabel":
            self._start_issabel_match()
            return
        directory_text = self._selected_directory()
        if not directory_text:
            self._show_toast("Selecciona una carpeta antes de escanear")
            self._switch_page("config")
            (self.nas_directory if source == "nas" else self.config_directory).setFocus()
            return

        directory = Path(directory_text).expanduser()
        self._start_local_scan(directory, source=source)

    def _set_scan_busy(self, busy: bool, message: str = "") -> None:
        self.scan_button.setEnabled(not busy)
        self.analyze_button.setEnabled(not busy)
        self.audio_source.setEnabled(not busy)
        if busy:
            self.scan_button.setToolTip(message or "Escaneando audios en segundo plano…")
        else:
            self._source_changed()

    def _start_local_scan(
        self,
        directory: Path,
        source: str,
        preselected_paths: list[Path] | None = None,
    ) -> None:
        if self.local_scan_worker is not None:
            return
        self._set_scan_busy(True, "Leyendo carpetas y metadatos en segundo plano…")
        source_label = "NAS" if source == "nas" else "Issabel" if source == "issabel" else "carpeta local"
        self._show_toast(f"Escaneando {source_label} sin bloquear la aplicación…")
        self.local_scan_worker = LocalScanWorker(
            directory,
            self.active_base_index if self.active_base_path is not None else None,
            source,
            preselected_paths,
            self,
        )
        self.local_scan_worker.succeeded.connect(self._local_scan_succeeded)
        self.local_scan_worker.failed.connect(self._local_scan_failed)
        self.local_scan_worker.finished.connect(self._local_scan_finished)
        self.local_scan_worker.start()

    def _local_scan_succeeded(self, result: object) -> None:
        if isinstance(result, dict):
            self._apply_scan_result(result)

    def _local_scan_failed(self, error: str) -> None:
        self._show_toast(f"No se pudo completar el escaneo: {error}")

    def _local_scan_finished(self) -> None:
        worker = self.local_scan_worker
        self.local_scan_worker = None
        if self.issabel_match_worker is None:
            self._set_scan_busy(False)
        if worker is not None:
            worker.deleteLater()

    def _start_issabel_match(self) -> None:
        if self.issabel_match_worker is not None:
            return
        config = self._remote_config()
        if not config["fingerprint"] or not config["password"]:
            self._show_toast("Conecta primero Issabel desde Configuración")
            self._switch_page("config")
            return
        if not self.active_base_index.dates:
            self._show_toast("La base activa no contiene fechas válidas en Hoja1")
            return
        base_name = re.sub(r"[^A-Za-z0-9._-]+", "_", self.active_base_path.stem)[:80]
        destination = Path(__file__).resolve().parents[3] / "data" / "remote_audio" / base_name
        self._set_scan_busy(True, "Buscando por teléfono y fecha en Issabel…")
        self._show_toast(
            f"Issabel: revisando {len(self.active_base_index.dates)} carpeta(s) de fecha, no todo el año"
        )
        self.issabel_match_worker = IssabelMatchWorker(
            config, self.active_base_index, destination, self
        )
        self.issabel_match_worker.succeeded.connect(self._issabel_match_succeeded)
        self.issabel_match_worker.failed.connect(self._issabel_match_failed)
        self.issabel_match_worker.finished.connect(self._issabel_match_finished)
        self.issabel_match_worker.start()

    def _issabel_match_succeeded(self, result: object) -> None:
        payload = result if isinstance(result, dict) else {}
        paths = [Path(path) for path in payload.get("paths", [])]
        if not paths:
            checked = int(payload.get("candidate_count", 0) or 0)
            self._show_toast(
                f"Issabel: se revisaron {checked} audios en las fechas de la base y no hubo teléfonos coincidentes"
            )
            self._show_empty_state(
                "No se encontraron coincidencias en Issabel",
                "Se buscaron teléfono y fecha exactos de Hoja1 dentro de las carpetas correspondientes.",
            )
            return
        self._start_local_scan(paths[0].parent, source="issabel", preselected_paths=paths)

    def _issabel_match_failed(self, error: str) -> None:
        self._show_toast(f"No se pudo emparejar con Issabel: {error}")

    def _issabel_match_finished(self) -> None:
        worker = self.issabel_match_worker
        self.issabel_match_worker = None
        if self.local_scan_worker is None:
            self._set_scan_busy(False)
        if worker is not None:
            worker.deleteLater()

    def _finish_scan(self, directory: Path) -> None:
        """Ruta síncrona conservada para pruebas y carpetas pequeñas internas."""
        try:
            paths, records = collect_audio_records(
                directory,
                self.active_base_index if self.active_base_path is not None else None,
            )
        except FileNotFoundError:
            message = "La carpeta seleccionada no existe"
        except NotADirectoryError:
            message = "La ruta seleccionada no es una carpeta"
        except OSError:
            message = "No se pudo leer la carpeta seleccionada"
        else:
            self._apply_scan_result({
                "directory": Path(directory),
                "paths": paths,
                "records": records,
                "source": self._source_key(),
            })
            return
        self.scan_button.setEnabled(True)
        self.scan_button.setToolTip("Escanear carpeta")
        self._show_toast(message)

    def _apply_scan_result(self, result: dict) -> None:
        directory = Path(result["directory"])
        source = str(result.get("source", self._source_key()))
        self.detected_audio_files = tuple(Path(path) for path in result.get("paths", ()))
        records = list(result.get("records", ()))
        try:
            self.database.register_calls(records)
            if source == "local":
                self.database.save_settings({"audio_directory": str(directory)})
            elif source == "nas":
                self.database.save_settings({"nas_directory": str(directory)})
            stored = self.database.call_rows(self.detected_audio_files)
        except (sqlite3.Error, OSError) as exc:
            self._show_toast(f"No se pudo guardar el escaneo: {exc}")
            return
        self.call_records = [call_record_from_row(row) for row in stored]
        self.calls = {call.call_id: call for call in self.call_records}
        self._populate_call_list()
        self.search_input.clear()
        self.status_filter.setCurrentIndex(0)
        self._update_category_metrics()
        if self.call_records:
            self.call_list.setCurrentRow(0)
        else:
            self._show_empty_state(
                "No se encontraron coincidencias",
                "No hay audios q- cuyo teléfono y fecha aparezcan en Hoja1 de la base seleccionada.",
            )
        self._filter_calls()
        count = len(self.detected_audio_files)
        noun = "audio encontrado" if count == 1 else "audios encontrados"
        base_note = f" · base {self.active_base_path.name}" if self.active_base_path else ""
        source_label = {"local": "local", "nas": "NAS", "issabel": "Issabel"}.get(source, "")
        self._show_toast(f"Escaneo {source_label} completado · {count} {noun}{base_note}")

    def _update_category_metrics(self) -> None:
        counts = {name: sum(call.category_code == name for call in self.call_records)
                  for name in ("ALERTA", "BUZON", "NORMAL")}
        self.files_metric.setText(str(len(self.call_records)))
        self.sensitive_metric.setText(str(counts["ALERTA"]))
        self.mailbox_metric.setText(str(counts["BUZON"]))
        self.normal_metric.setText(str(counts["NORMAL"]))
        self.normal_metric_label.setText("Normales")
        if hasattr(self, "report_total_value"):
            self.report_total_value.setText(str(len(self.call_records)))
            self.report_sensitive_value.setText(str(counts["ALERTA"]))
            self.report_normal_value.setText(str(counts["NORMAL"]))
            self.report_empty.setVisible(not self.call_records)
            self.report_download.setEnabled(bool(self.call_records))
        pending = sum(call.category_code in {"PENDIENTE", "ERROR", ""} for call in self.call_records)
        current = self.status_filter.currentData()
        labels = {
            "all": f"Todas las llamadas ({len(self.call_records)})",
            "alert": f"Demandas / alertas ({counts['ALERTA']})",
            "mailbox": f"Buzones ({counts['BUZON']})",
            "normal": f"Llamadas normales ({counts['NORMAL']})",
            "pending": f"Pendientes / errores ({pending})",
        }
        self.status_filter.blockSignals(True)
        for index in range(self.status_filter.count()):
            value = self.status_filter.itemData(index)
            if value in labels:
                self.status_filter.setItemText(index, labels[value])
        target = self.status_filter.findData(current)
        if target >= 0:
            self.status_filter.setCurrentIndex(target)
        self.status_filter.blockSignals(False)

    def _analysis_config(self):
        keywords = [word.strip() for word in self.keywords_input.text().split(",") if word.strip()]
        transcription_key = self.transcription_api_key.text().strip()
        analysis_key = self.analysis_api_key.text().strip()
        if not transcription_key:
            raise ValueError("Configura la clave de transcripción antes de analizar.")
        if not analysis_key:
            raise ValueError("Configura la clave de análisis contextual antes de analizar.")
        if not keywords:
            raise ValueError("Configura al menos un término sensible.")
        return {
            "keywords": keywords,
            "transcription_provider": str(self.transcription_provider.currentData()),
            "transcription_model": self.transcription_model.currentText().strip(),
            "transcription_key": transcription_key,
            "analysis_provider": str(self.analysis_provider.currentData()),
            "analysis_model": self.analysis_model.currentText().strip(),
            "analysis_key": analysis_key,
        }

    def _start_analysis(self) -> None:
        if self.analysis_worker is not None:
            self.analysis_worker.request_stop()
            self.analyze_button.setEnabled(False)
            self.analyze_button.setText("Deteniendo…")
            self.analyze_button.setAccessibleName("Deteniendo análisis")
            return
        paths = [call.source_path for call in self.call_records if call.source_path is not None]
        if not paths:
            self._show_toast("Escanea primero una carpeta con audios")
            return
        try:
            config = self._analysis_config()
            self._persist_credentials()
        except (ValueError, SecretStoreError, sqlite3.Error, OSError) as exc:
            self._show_toast(str(exc))
            self._switch_page("config")
            return
        self.analysis_worker = AnalysisWorker(self.database, paths, config, self)
        self.analysis_worker.progress.connect(self._analysis_progress)
        self.analysis_worker.row_ready.connect(self._analysis_row_ready)
        self.analysis_worker.completed.connect(self._analysis_complete)
        self.analyze_button.set_progress(0, len(paths))
        self.analyze_button.setToolTip(f"Analizando 0/{len(paths)} llamadas")
        self._set_analysis_controls_locked(True)
        self.analysis_worker.start()

    def _analysis_progress(self, current: int, total: int, filename: str) -> None:
        self.analyze_button.set_progress(current, total)
        self.analyze_button.setToolTip(f"Analizando {current}/{total}: {filename}")

    def _analysis_row_ready(self, row) -> None:
        updated = call_record_from_row(row)
        position = next(
            (index for index, call in enumerate(self.call_records) if call.source_path == updated.source_path),
            None,
        )
        if position is None:
            return
        self.call_records[position] = updated
        self.calls[updated.call_id] = updated
        self.call_search_cache[updated.call_id] = self._searchable_call_text(updated)
        item = self.call_list.item(position)
        selected = item is self.call_list.currentItem()
        previous_card = self.call_list.itemWidget(item)
        if previous_card is not None:
            self.call_list.removeItemWidget(item)
            previous_card.deleteLater()
            self.call_cards.pop(updated.call_id, None)
        if previous_card is not None or selected:
            card = self._materialize_call_card(position)
            if card is not None:
                card.set_selected(selected)
        self._update_category_metrics()
        if selected:
            self._show_call(updated)
        self._filter_calls()

    def _analysis_complete(self, completed: int, failures: int, stopped: bool) -> None:
        worker = self.analysis_worker
        self.analysis_worker = None
        self.analyze_button.setEnabled(True)
        self.analyze_button.set_idle()
        self.analyze_button.setToolTip("Transcribe pendientes y los separa en alertas, buzones y normales")
        stored = self.database.call_rows([call.source_path for call in self.call_records if call.source_path])
        if stored:
            self.call_records = [call_record_from_row(row) for row in stored]
            self.calls = {call.call_id: call for call in self.call_records}
            self._populate_call_list()
            self._update_category_metrics()
            if self.call_list.count():
                self.call_list.setCurrentRow(0)
            self._filter_calls()
        self._set_analysis_controls_locked(False)
        message = f"Análisis {'detenido' if stopped else 'completado'} · {completed} guardados"
        if failures:
            message += f" · {failures} con error (puedes reintentar)"
        self._show_toast(message)
        if worker is not None:
            worker.deleteLater()
        if self.close_after_analysis:
            self.close_after_analysis = False
            QTimer.singleShot(0, self.close)

    def _mark_reviewed(self) -> None:
        if self.analysis_worker is not None:
            return
        if self.current_call is None or self.current_call.source_path is None:
            self._show_toast("Selecciona una llamada antes de marcarla")
            return
        try:
            self.database.set_reviewed(self.current_call.source_path)
        except sqlite3.Error as exc:
            self._show_toast(f"No se pudo guardar la revisión: {exc}")
            return
        self._show_toast("Llamada marcada como revisada y guardada")

    def _show_toast(self, message: str) -> None:
        self.toast.setText(message)
        self.toast.adjustSize()
        self.toast.setMinimumWidth(min(max(self.toast.width() + 28, 260), 520))
        self.toast.adjustSize()
        self._position_toast()
        self.toast.show()
        self.toast.raise_()
        self.toast_timer.start(2600)

    def _hide_toast(self) -> None:
        self.toast.hide()

    def _position_toast(self) -> None:
        if not hasattr(self, "toast"):
            return
        margin = 22
        root = self.centralWidget()
        if root is None:
            return
        self.toast.move(root.width() - self.toast.width() - margin, root.height() - self.toast.height() - margin)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        compact = self.width() < 1240
        if hasattr(self, "directory_label"):
            self.directory_label.setMaximumWidth(120 if compact else 155)
            self.export_button.setText("Excel" if compact else "Exportar Excel")
        if hasattr(self, "sort_button"):
            self.sort_button.setText("Orden" if compact else f"Orden: {self.sort_label}")
            self.call_count.setVisible(not compact)
        self._position_toast()

    def closeEvent(self, event) -> None:
        if self.local_scan_worker is not None:
            self._show_toast("Espera a que termine el escaneo local o del NAS antes de cerrar Sentry")
            event.ignore()
            return
        if self.winscp_worker is not None:
            self._show_toast("Espera a que termine la prueba de conexión antes de cerrar Sentry")
            event.ignore()
            return
        if self.remote_search_worker is not None:
            self._show_toast("Espera a que termine la búsqueda en Issabel antes de cerrar Sentry")
            event.ignore()
            return
        if self.issabel_match_worker is not None:
            self._show_toast("Espera a que termine el emparejamiento y la descarga desde Issabel")
            event.ignore()
            return
        try:
            directory = self.config_directory.text().strip()
            keywords = self.keywords_input.text().strip()
            if directory and keywords:
                self.database.save_settings({
                    "audio_directory": directory,
                    "nas_directory": self.nas_directory.text().strip(),
                    "audio_source": self._source_key(),
                    "keywords": keywords,
                    "theme": self.theme,
                })
            self._persist_credentials()
            self._persist_remote_connection()
        except (ValueError, SecretStoreError, sqlite3.Error, OSError):
            pass
        if hasattr(self, "bases_page") and self.bases_page.busy:
            self._show_toast("Espera a que termine el procesamiento de bases antes de cerrar Sentry")
            event.ignore()
            return
        if self.analysis_worker is not None:
            self.close_after_analysis = True
            self.analysis_worker.request_stop()
            self._show_toast("Guardando el audio actual; Sentry se cerrará cuando termine")
            event.ignore()
            return
        self.media_player.stop()
        self.media_player.setSource(QUrl())
        super().closeEvent(event)

    @staticmethod
    def _stylesheet(theme_name: str = DEFAULT_THEME) -> str:
        arrow_name = theme_asset(theme_name, "chevron")
        combo_arrow = (Path(__file__).resolve().parents[1] / "assets" / arrow_name).as_posix()
        return f"""
            * {{
                font-family: "Inter", "Segoe UI", sans-serif;
                font-size: 13px;
                color: {PALETTE['text_soft']};
            }}
            QMainWindow, QWidget#appRoot, QWidget#auditPage, QWidget#reportsPage, QWidget#configPage, QWidget#basesPage, QWidget#basesContent {{
                background: {PALETTE['canvas']};
            }}
            QScrollArea#configScroll {{ background: {PALETTE['canvas']}; border: none; }}
            QFrame#topbar {{
                background: {PALETTE['panel']};
                border-bottom: 1px solid {PALETTE['border']};
            }}
            QLabel#brandTitle {{ color: {PALETTE['text']}; font-size: 15px; font-weight: 650; letter-spacing: 0.5px; }}
            QFrame#directoryBar {{
                background: {PALETTE['surface']};
                border: 1px solid {PALETTE['border']};
                border-radius: 8px;
            }}
            QLabel#directoryPath {{ color: {PALETTE['text_soft']}; font-size: 12px; }}
            QLabel#mutedLabel, QLabel#pageSubtitle {{ color: {PALETTE['muted']}; }}
            QPushButton {{ min-height: 36px; padding: 0 13px; border-radius: 7px; font-weight: 500; }}
            QPushButton:focus, QLineEdit:focus, QComboBox:focus, QWidget:focus {{
                border: 2px solid {PALETTE['green_deep']};
            }}
            QPushButton#navButton {{
                background: transparent;
                color: {PALETTE['muted']};
                border: 1px solid transparent;
            }}
            QPushButton#navButton:hover {{ color: {PALETTE['text']}; background: {PALETTE['surface']}; }}
            QPushButton#navButton:checked {{
                color: {PALETTE['green_accessible']};
                background: {PALETTE['green_soft']};
                border: 1px solid {PALETTE['nav_checked_border']};
            }}
            QPushButton#secondaryButton, QPushButton#sortButton {{
                background: {PALETTE['panel']};
                color: {PALETTE['text_soft']};
                border: 1px solid {PALETTE['border_strong']};
            }}
            QPushButton#sortButton {{ min-width: 96px; text-align: left; padding-right: 24px; }}
            QPushButton#secondaryButton:hover, QPushButton#sortButton:hover {{ color: {PALETTE['text']}; background: {PALETTE['surface_hover']}; border-color: {PALETTE['secondary_hover_border']}; }}
            QPushButton#secondaryButton:disabled {{ color: {PALETTE['gray_light']}; background: {PALETTE['surface']}; border-color: {PALETTE['border']}; }}
            QTableWidget, QTableView {{
                background-color: {PALETTE['panel']};
                alternate-background-color: {PALETTE['surface']};
                color: {PALETTE['text']};
                border: 1px solid {PALETTE['border']};
                gridline-color: {PALETTE['border']};
                selection-background-color: {PALETTE['green_soft']};
                selection-color: {PALETTE['text']};
            }}
            QTableWidget::item, QTableView::item {{
                background-color: {PALETTE['panel']};
                color: {PALETTE['text']};
            }}
            QTableWidget::item:alternate, QTableView::item:alternate {{
                background-color: {PALETTE['surface']};
            }}
            QTableWidget::item:selected, QTableView::item:selected {{
                background-color: {PALETTE['green_soft']};
                color: {PALETTE['text']};
            }}
            QTableWidget QAbstractScrollArea::viewport, QTableView QAbstractScrollArea::viewport {{
                background-color: {PALETTE['panel']};
            }}
            QHeaderView {{ background-color: {PALETTE['panel']}; }}
            QHeaderView::section {{
                background-color: {PALETTE['surface']};
                color: {PALETTE['text']};
                padding: 7px;
                border: none;
                border-bottom: 1px solid {PALETTE['border']};
            }}
            QTableCornerButton::section {{
                background-color: {PALETTE['surface']};
                border: none;
                border-bottom: 1px solid {PALETTE['border']};
            }}
            QPushButton#primaryButton {{
                background: {PALETTE['green_accessible']};
                color: white;
                border: 1px solid {PALETTE['green_accessible']};
            }}
            QPushButton#primaryButton:hover {{ background: {PALETTE['primary_hover']}; }}
            QPushButton#primaryButton:disabled {{
                background: {PALETTE['disabled_bg']};
                color: {PALETTE['gray_light']};
                border-color: {PALETTE['border']};
            }}
            QPushButton#criticalButton {{
                background: {PALETTE['alert_bg']};
                color: {PALETTE['alert_text']};
                border: 1px solid {PALETTE['alert_border']};
            }}
            QPushButton#criticalButton:hover {{ background: {PALETTE['alert_hover']}; }}
            QPushButton#criticalButton:disabled {{
                background: {PALETTE['disabled_bg']};
                color: {PALETTE['gray_light']};
                border-color: {PALETTE['border']};
            }}
            QPushButton#linkButton {{
                background: transparent;
                color: {PALETTE['green_accessible']};
                border: none;
                text-decoration: underline;
            }}
            QPushButton#linkButton:hover {{ color: {PALETTE['text']}; }}
            QPushButton#linkButton:disabled {{ color: {PALETTE['gray_light']}; }}
            QFrame#filterBar {{
                background: {PALETTE['panel']};
                border-bottom: 1px solid {PALETTE['border']};
            }}
            QLabel#metricValue, QLabel#metricAccent {{ color: {PALETTE['text']}; font-size: 17px; font-weight: 600; }}
            QLabel#metricAccent {{ color: {PALETTE['green_accessible']}; }}
            QLabel#metricLabel, QLabel#sectionTitle {{ color: {PALETTE['muted']}; font-size: 10px; font-weight: 550; letter-spacing: 0.3px; }}
            QLineEdit, QComboBox {{
                min-height: 36px;
                background: {PALETTE['panel']};
                color: {PALETTE['text']};
                border: 1px solid {PALETTE['border_strong']};
                border-radius: 7px;
                padding: 0 11px;
                selection-background-color: {PALETTE['green_deep']};
                selection-color: white;
            }}
            QLineEdit::placeholder {{ color: {PALETTE['placeholder']}; }}
            QComboBox::drop-down {{
                width: 28px;
                background: {PALETTE['surface']};
                border: none;
                border-left: 1px solid {PALETTE['border']};
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
            }}
            QComboBox::down-arrow {{
                image: url("{combo_arrow}");
                width: 12px;
                height: 12px;
            }}
            QComboBox QAbstractItemView {{
                background: {PALETTE['panel']};
                color: {PALETTE['text']};
                border: 1px solid {PALETTE['border_strong']};
                selection-background-color: {PALETTE['green_soft']};
                selection-color: {PALETTE['green_accessible']};
                outline: none;
            }}
            QMenu {{
                background: {PALETTE['panel']};
                color: {PALETTE['text']};
                border: 1px solid {PALETTE['border_strong']};
                padding: 5px;
            }}
            QMenu::item {{ padding: 8px 24px 8px 10px; border-radius: 5px; }}
            QMenu::item:selected {{ background: {PALETTE['green_soft']}; color: {PALETTE['green_accessible']}; }}
            QSplitter#mainSplitter::handle {{ background: {PALETTE['border']}; width: 1px; }}
            QFrame#queuePanel {{ background: {PALETTE['panel']}; }}
            QStackedWidget#detailPages, QWidget#emptyDetail,
            QScrollArea#detailScroll, QWidget#detailContent {{ background: {PALETTE['canvas']}; }}
            QLabel#emptyTitle {{ color: {PALETTE['text']}; font-size: 17px; font-weight: 600; }}
            QLabel#emptyReport {{ color: {PALETTE['muted']}; font-size: 13px; }}
            QFrame#sectionHeader {{ background: {PALETTE['panel']}; border-bottom: 1px solid {PALETTE['border']}; }}
            QListWidget#callList {{
                background: {PALETTE['panel']};
                border: none;
                outline: none;
            }}
            QListWidget#callList::item {{ background: transparent; border: none; }}
            QListWidget#callList::item:selected {{ background: transparent; }}
            QFrame#callCard {{
                background: {PALETTE['panel']};
                border: 1px solid transparent;
                border-bottom: 1px solid {PALETTE['border']};
                border-radius: 0;
            }}
            QFrame#callCard:hover {{ background: {PALETTE['surface_hover']}; }}
            QFrame#callCard[selected="true"] {{
                background: {PALETTE['card_selected_bg']};
                border-left: 1px solid {PALETTE['green_deep']};
                border-bottom: 1px solid {PALETTE['border']};
            }}
            QFrame#alertDot {{ background: {PALETTE['green_lime']}; border-radius: 4px; }}
            QFrame#normalDot {{ background: {PALETTE['border_strong']}; border-radius: 4px; }}
            QLabel#callAgent {{ color: {PALETTE['text']}; font-weight: 600; }}
            QLabel#callCustomer {{ color: {PALETTE['text']}; font-weight: 600; }}
            QFrame#classificationBadge {{
                background: {PALETTE['neutral_badge_bg']};
                border: 1px solid {PALETTE['border_strong']};
                border-radius: 5px;
            }}
            QFrame#classificationBadge QLabel#classificationText {{
                color: {PALETTE['gray']};
                font-size: 9px;
                font-weight: 600;
            }}
            QFrame#classificationBadge[category="ALERTA"] {{
                background: {PALETTE['alert_bg']};
                border: 1px solid {PALETTE['alert_border']};
            }}
            QFrame#classificationBadge[category="ALERTA"] QLabel#classificationText {{
                color: {PALETTE['alert_text']};
                font-size: 9px;
                font-weight: 650;
            }}
            QFrame#classificationBadge[category="BUZON"] {{
                background: {PALETTE['neutral_badge_bg']};
                border: 1px solid {PALETTE['border_strong']};
            }}
            QFrame#classificationBadge[category="BUZON"] QLabel#classificationText {{
                color: {PALETTE['gray']};
                font-size: 9px;
                font-weight: 600;
            }}
            QFrame#classificationBadge[category="NORMAL"] {{
                background: {PALETTE['green_soft']};
                border: 1px solid {PALETTE['mailbox_border']};
            }}
            QFrame#classificationBadge[category="NORMAL"] QLabel#classificationText {{
                color: {PALETTE['green_accessible']};
                font-size: 9px;
                font-weight: 600;
            }}
            QLabel#classificationIcon {{ background: transparent; border: none; }}
            QLabel#sensitiveBadge, QLabel#riskBadge[sensitive="true"] {{
                color: {PALETTE['green_accessible']};
                background: {PALETTE['green_soft']};
                border: 1px solid {PALETTE['mailbox_border']};
                border-radius: 5px;
                padding: 2px 6px;
                font-size: 9px;
                font-weight: 600;
            }}
            QLabel#neutralBadge, QLabel#riskBadge[sensitive="false"] {{
                color: {PALETTE['gray']};
                background: {PALETTE['neutral_badge_bg']};
                border: 1px solid {PALETTE['border']};
                border-radius: 5px;
                padding: 2px 6px;
                font-size: 9px;
                font-weight: 550;
            }}
            QLabel#callSnippet {{ color: {PALETTE['gray']}; }}
            QLabel#monoMuted {{ color: {PALETTE['muted']}; font-size: 11px; }}
            QFrame#contentPanel, QFrame#summaryPanel, QFrame#playerPanel, QFrame#transcriptPanel {{
                background: {PALETTE['panel']};
                border: 1px solid {PALETTE['border']};
                border-radius: 10px;
            }}
            QLabel#filename {{ color: {PALETTE['text']}; font-weight: 600; }}
            QLabel#duration, QLabel#timeDisplay {{ color: {PALETTE['text']}; font-weight: 600; }}
            QLabel#metaValue {{ color: {PALETTE['text']}; font-weight: 500; }}
            QLabel#summaryText {{ color: {PALETTE['text_soft']}; font-size: 14px; line-height: 1.45; }}
            QPushButton#playButton {{
                background: {PALETTE['green_accessible']};
                border: 1px solid {PALETTE['green_accessible']};
                padding: 0;
            }}
            QPushButton#playButton:hover {{ background: {PALETTE['primary_hover']}; }}
            QPushButton#playButton:disabled {{
                background: {PALETTE['disabled_bg']};
                border-color: {PALETTE['border']};
            }}
            QScrollArea#transcriptScroll, QWidget#transcriptBody {{ background: transparent; border: none; }}
            QFrame#transcriptRow {{ background: transparent; border: none; border-bottom: 1px solid {PALETTE['transcript_row_border']}; border-radius: 0; }}
            QFrame#transcriptRow:hover {{ background: {PALETTE['surface_hover']}; }}
            QFrame#transcriptRow[critical="true"] {{
                background: {PALETTE['transcript_critical_bg']};
                border: 1px solid {PALETTE['transcript_critical_border']};
                border-radius: 7px;
            }}
            QFrame#transcriptRow[playbackState="active"] {{
                background: {PALETTE['transcript_played_bg']};
                border: 1px solid {PALETTE['green_accessible']};
                border-radius: 7px;
            }}
            QFrame#transcriptRow:focus {{ border: 2px solid {PALETTE['green_deep']}; border-radius: 7px; }}
            QFrame#transcriptRow[playbackState="active"] QLabel#transcriptStamp,
            QFrame#transcriptRow[playbackState="active"] QLabel#criticalStamp,
            QFrame#transcriptRow[playbackState="active"] QLabel#speaker {{
                color: {PALETTE['green_accessible']};
                font-weight: 700;
            }}
            QLabel#transcriptStamp {{ color: {PALETTE['muted']}; }}
            QLabel#criticalStamp {{ color: {PALETTE['green_accessible']}; font-weight: 650; }}
            QLabel#speaker {{ color: {PALETTE['text']}; font-weight: 600; }}
            QLabel#transcriptText {{ color: {PALETTE['text_soft']}; line-height: 1.4; }}
            QLabel#statusOnline {{
                color: {PALETTE['green_accessible']};
                background: {PALETTE['green_soft']};
                border-radius: 5px;
                padding: 3px 7px;
                font-size: 9px;
                font-weight: 600;
            }}
            QLabel#pageTitle {{ color: {PALETTE['text']}; font-size: 23px; font-weight: 600; }}
            QLabel#formTitle, QLabel#reportLabel {{ color: {PALETTE['text']}; font-size: 14px; font-weight: 600; }}
            QLabel#apiGroupTitle {{ color: {PALETTE['text']}; font-size: 13px; font-weight: 600; padding-bottom: 2px; }}
            QLabel#fieldLabel {{ color: {PALETTE['muted']}; font-size: 10px; font-weight: 550; }}
            QPushButton#validateButton {{
                background: {PALETTE['green_accessible']};
                color: white;
                border: 1px solid {PALETTE['green_accessible']};
            }}
            QPushButton#validateButton:hover {{ background: {PALETTE['primary_hover']}; }}
            QPushButton#validateButton:disabled {{
                background: {PALETTE['disabled_bg']};
                color: {PALETTE['gray_light']};
                border-color: {PALETTE['border']};
            }}
            QLabel#apiStatus {{ color: {PALETTE['muted']}; font-size: 11px; min-height: 18px; }}
            QLabel#apiStatus[state="loading"] {{ color: {PALETTE['charcoal']}; }}
            QLabel#apiStatus[state="valid"] {{ color: {PALETTE['green_accessible']}; font-weight: 600; }}
            QLabel#apiStatus[state="error"] {{ color: {PALETTE['charcoal']}; font-weight: 550; }}
            QFrame#reportMetric, QFrame#reportMetricAccent {{
                background: {PALETTE['panel']};
                border: 1px solid {PALETTE['border']};
                border-radius: 10px;
            }}
            QFrame#reportMetricAccent {{ background: {PALETTE['panel']}; }}
            QLabel#reportValue, QLabel#reportValueAccent {{ color: {PALETTE['text']}; font-size: 28px; font-weight: 600; }}
            QLabel#reportValueAccent {{ color: {PALETTE['green_accessible']}; }}
            QLabel#toast {{
                background: {PALETTE['toast_bg']};
                color: {PALETTE['toast_text']};
                border: none;
                border-radius: 8px;
                padding: 11px 16px;
                font-weight: 500;
            }}
            QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
            QScrollBar::handle:vertical {{ background: {PALETTE['border_strong']}; min-height: 28px; border-radius: 4px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QToolTip {{
                background: {PALETTE['tooltip_bg']};
                color: {PALETTE['tooltip_text']};
                border: none;
                padding: 6px;
            }}
        """
