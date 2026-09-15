from __future__ import annotations

import base64
import ctypes
import html
import json
import mimetypes
import os
import unicodedata
import uuid
import urllib.error
import urllib.parse
import urllib.request
import wave
from ctypes import wintypes
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, QRunnable, QSettings, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
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


PALETTE = {
    "green_deep": "#27953c",
    "green_accessible": "#1f7830",
    "green": "#40b73c",
    "green_lime": "#7eca29",
    "charcoal": "#4f4c4c",
    "gray": "#656263",
    "gray_light": "#7a7879",
    "canvas": "#f5f6f5",
    "panel": "#ffffff",
    "surface": "#fafbfa",
    "green_soft": "#eef7ef",
    "border": "#dfe3df",
    "border_strong": "#c8cdc8",
    "text": "#202220",
    "text_soft": "#4f4c4c",
    "muted": "#656263",
}

AUDIO_SUFFIXES = {".mp3", ".wav"}
TRANSCRIPTION_LANGUAGE = "es-419"

ADVISOR_SIGNALS = (
    ("le atiende", 4),
    ("en que puedo ayudar", 4),
    ("como puedo ayudar", 4),
    ("gracias por comunicarse", 4),
    ("gracias por llamar", 4),
    ("bienvenido a", 3),
    ("servicio al cliente", 3),
    ("voy a validar", 2),
    ("permita validar", 2),
    ("voy a revisar", 2),
    ("me confirma", 2),
    ("numero de caso", 2),
)

CUSTOMER_SIGNALS = (
    ("llamo porque", 4),
    ("llamo por", 3),
    ("quiero cancelar", 3),
    ("quiero dar de baja", 3),
    ("tengo un problema", 3),
    ("me cobraron", 3),
    ("me estan cobrando", 3),
    ("mi factura", 2),
    ("mi servicio", 2),
    ("necesito ayuda", 2),
    ("quiero reclamar", 2),
)


class _DataBlob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte)))


def _dpapi_blob(data: bytes) -> tuple[_DataBlob, object]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, buffer


def protect_secret(secret: str) -> str:
    if not secret:
        return ""
    source, source_buffer = _dpapi_blob(secret.encode("utf-8"))
    protected = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(
        ctypes.byref(source), "Sentry API key", None, None, None, 0x1, ctypes.byref(protected)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return base64.b64encode(ctypes.string_at(protected.pbData, protected.cbData)).decode("ascii")
    finally:
        ctypes.WinDLL("kernel32", use_last_error=True).LocalFree(protected.pbData)
        del source_buffer


def unprotect_secret(protected_secret: str) -> str:
    if not protected_secret:
        return ""
    try:
        encrypted = base64.b64decode(protected_secret, validate=True)
    except (ValueError, TypeError) as error:
        raise OSError("La clave guardada no es válida") from error
    source, source_buffer = _dpapi_blob(encrypted)
    clear = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(clear)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(clear.pbData, clear.cbData).decode("utf-8")
    finally:
        ctypes.WinDLL("kernel32", use_last_error=True).LocalFree(clear.pbData)
        del source_buffer


def scan_audio_files(directory: Path) -> tuple[Path, ...]:
    if not directory.exists():
        raise FileNotFoundError(directory)
    if not directory.is_dir():
        raise NotADirectoryError(directory)

    return tuple(
        sorted(
            (path for path in directory.rglob("*") if path.is_file() and path.suffix.casefold() in AUDIO_SUFFIXES),
            key=lambda path: str(path).casefold(),
        )
    )


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


def format_time(seconds: int) -> str:
    minutes, remaining = divmod(max(0, seconds), 60)
    return f"{minutes:02d}:{remaining:02d}"


def _infer_speaker_roles(utterances: list[object]) -> dict[str, str]:
    samples: dict[str, list[str]] = {}
    for item in utterances:
        if not isinstance(item, dict) or not str(item.get("transcript", "")).strip():
            continue
        speaker = str(item.get("speaker", 0))
        samples.setdefault(speaker, []).append(str(item["transcript"]))

    speakers = list(samples)
    roles = {speaker: f"Participante {index}" for index, speaker in enumerate(speakers, 1)}
    if not speakers:
        return roles

    def score(texts: list[str], signals: tuple[tuple[str, int], ...]) -> int:
        text = " ".join(texts)
        text = "".join(character for character in unicodedata.normalize("NFD", text.casefold()) if not unicodedata.combining(character))
        return sum(weight for phrase, weight in signals if phrase in text)

    def winner(scores: dict[str, int]) -> str | None:
        ranked = sorted(scores, key=scores.get, reverse=True)
        return ranked[0] if scores[ranked[0]] >= 3 and (len(ranked) == 1 or scores[ranked[0]] - scores[ranked[1]] >= 2) else None

    advisor = winner({speaker: score(samples[speaker], ADVISOR_SIGNALS) for speaker in speakers})
    customer = winner({speaker: score(samples[speaker], CUSTOMER_SIGNALS) for speaker in speakers})

    if advisor:
        roles[advisor] = "Asesor"
    if customer and customer != advisor:
        roles[customer] = "Cliente"
    if len(speakers) == 2:
        if advisor and not customer:
            roles[next(speaker for speaker in speakers if speaker != advisor)] = "Cliente"
        elif customer and not advisor:
            roles[next(speaker for speaker in speakers if speaker != customer)] = "Asesor"
    return roles


def call_record_from_audio(path: Path, call_id: int) -> CallRecord:
    parts = path.stem.split("-")
    customer = "Sin identificar"
    try:
        clock = datetime.fromtimestamp(path.stat().st_mtime).strftime("%H:%M")
    except OSError:
        clock = "--:--"
    if len(parts) >= 5:
        phone, raw_time = parts[2], parts[4]
        if phone.isdigit() and len(phone) >= 7:
            customer = f"{phone[:3]}***{phone[-4:]}"
        if raw_time.isdigit() and len(raw_time) == 6:
            clock = f"{raw_time[:2]}:{raw_time[2:4]}"

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
    )


class AnalysisSignals(QObject):
    finished = Signal(int, object)
    failed = Signal(int, str)


def _request_json(url: str, method: str, headers: dict[str, str], payload: object) -> object:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def _transcription_request(path: Path, provider: str, model: str, key: str) -> tuple[list[dict[str, object]], str]:
    audio = path.read_bytes()
    content_type = mimetypes.guess_type(path.name)[0] or "audio/wav"
    if provider == "deepgram":
        endpoint = "https://api.deepgram.com/v1/listen?" + urllib.parse.urlencode({
            "model": model or "nova-3",
            "language": TRANSCRIPTION_LANGUAGE,
            "smart_format": "true",
            "diarize": "true",
            "utterances": "true",
        })
        payload = _request_bytes(endpoint, audio, {
            "Authorization": f"Token {key}",
            "Content-Type": content_type,
        })
        results = payload.get("results", {}) if isinstance(payload, dict) else {}
        utterances = results.get("utterances", []) if isinstance(results, dict) else []
        speaker_roles = _infer_speaker_roles(utterances)
        lines = [
            {
                "second": round(float(item.get("start", 0))),
                "speaker": speaker_roles.get(str(item.get("speaker", 0)), "Participante"),
                "text": str(item.get("transcript", "")).strip(),
                "word_seconds": tuple(
                    float(word.get("start", 0))
                    for word in (item.get("words") if isinstance(item.get("words"), list) else [])
                    if isinstance(word, dict)
                ),
            }
            for item in utterances
            if isinstance(item, dict) and str(item.get("transcript", "")).strip()
        ]
        channels = results.get("channels", []) if isinstance(results, dict) else []
        transcript = ""
        if channels and isinstance(channels[0], dict):
            alternatives = channels[0].get("alternatives", [])
            if alternatives and isinstance(alternatives[0], dict):
                transcript = str(alternatives[0].get("transcript", "")).strip()
        return lines or [{"second": 0, "speaker": "Transcripción", "text": transcript}], transcript

    boundary = f"----Sentry{uuid.uuid4().hex}"
    chunks = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{model or 'gpt-4o-mini-transcribe'}\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\nContent-Type: {content_type}\r\n\r\n".encode(),
        audio,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    payload = _request_bytes("https://api.openai.com/v1/audio/transcriptions", b"".join(chunks), {
        "Authorization": f"Bearer {key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    transcript = str(payload.get("text", "")).strip() if isinstance(payload, dict) else ""
    return [{"second": 0, "speaker": "Transcripción", "text": transcript}], transcript


def _request_bytes(url: str, body: bytes, headers: dict[str, str]) -> object:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def _summary_request(provider: str, model: str, key: str, transcript: str) -> str:
    if len(transcript) > 4000:
        transcript = transcript[:3000] + "\n[transcripción recortada]\n" + transcript[-1000:]
    prompt = (
        "Resume esta llamada en español en máximo 2 frases. Indica solo motivo, resultado "
        "y riesgo. No inventes datos.\n\n" + transcript
    )
    if provider == "gemini":
        generation_config = {"temperature": 0.1, "candidateCount": 1, "maxOutputTokens": 100}
        if (model or "").startswith("gemini-2.5-flash"):
            generation_config["thinkingConfig"] = {"thinkingBudget": 0}
        payload = _request_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model or 'gemini-2.5-flash-lite'}:generateContent?key={key}",
            "POST",
            {"Content-Type": "application/json"},
            {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": generation_config},
        )
        candidates = payload.get("candidates", []) if isinstance(payload, dict) else []
        parts = candidates[0].get("content", {}).get("parts", []) if candidates and isinstance(candidates[0], dict) else []
        return str(parts[0].get("text", "")).strip() if parts and isinstance(parts[0], dict) else ""

    payload = _request_json(
        "https://api.openai.com/v1/chat/completions",
        "POST",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {"model": model or "gpt-4o-mini", "temperature": 0.1, "messages": [{"role": "user", "content": prompt}]},
    )
    choices = payload.get("choices", []) if isinstance(payload, dict) else []
    message = choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
    return str(message.get("content", "")).strip() if isinstance(message, dict) else ""


class AudioAnalysisJob(QRunnable):
    def __init__(self, call_id: int, path: Path, transcription: tuple[str, str, str], analysis: tuple[str, str, str], keywords: tuple[str, ...]) -> None:
        super().__init__()
        self.call_id = call_id
        self.path = path
        self.transcription = transcription
        self.analysis = analysis
        self.keywords = keywords
        self.signals = AnalysisSignals()

    def run(self) -> None:
        try:
            lines, transcript = _transcription_request(self.path, *self.transcription)
            lowered = transcript.casefold()
            matches = [word for word in self.keywords if word.casefold() in lowered]
            keyword = matches[0] if matches else "Sin alertas"
            hit_second = next((int(line["second"]) for line in lines if any(word.casefold() in str(line["text"]).casefold() for word in matches)), None)
            summary = ""
            use_gemini = self.analysis[0] == "gemini"
            if self.analysis[2].strip() and transcript and (not use_gemini or matches):
                try:
                    summary = _summary_request(*self.analysis, transcript)
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    summary = ""
            if not summary:
                summary = "Transcripción completada. " + ("Se detectaron términos sensibles." if matches else "No se detectaron términos sensibles.")
            self.signals.finished.emit(self.call_id, {
                "lines": lines,
                "summary": summary,
                "matches": matches,
                "keyword": keyword,
                "hit_second": hit_second,
            })
        except (OSError, ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError) as error:
            self.signals.failed.emit(self.call_id, str(error))


class AudioTimeline(QWidget):
    seek_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.duration = 1
        self.current = 0
        self.marker: int | None = None
        self.setMinimumHeight(58)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Línea de tiempo del audio")

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

        bars = 72
        gap = usable / bars
        for index in range(bars):
            height = 8 + ((index * 17 + index * index * 3) % 24)
            x = left + index * gap
            color = PALETTE["green_deep"] if x <= progress_x else PALETTE["border_strong"]
            painter.setPen(QPen(QColor(color), max(2.0, gap * 0.42), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(int(x), int(center - height / 2), int(x), int(center + height / 2))

        if self.marker is not None:
            marker_x = left + usable * (self.marker / self.duration)
            painter.setPen(QPen(QColor(PALETTE["green_deep"]), 2))
            painter.drawLine(int(marker_x), 5, int(marker_x), self.height() - 5)

        if self.hasFocus():
            painter.setPen(QPen(QColor(PALETTE["green_deep"]), 2))
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

        badge = QLabel(call.keyword.capitalize())
        badge.setObjectName("sensitiveBadge" if call.sensitive else "neutralBadge")
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
    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Sentry · Auditoría de grabaciones")
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().parents[1] / "assets" / "sentry-app-icon.ico")))
        self.resize(1440, 860)
        self.setMinimumSize(1080, 680)

        self.settings = settings if settings is not None else QSettings("Ecuaconexión", "Sentry")
        self.saved_directory = str(self.settings.value("directory", "") or "")
        self.saved_keywords = str(self.settings.value("keywords", "") or "")
        self.secret_load_failed = False
        self.call_records: list[CallRecord] = []
        self.calls: dict[int, CallRecord] = {}
        self.detected_audio_files: tuple[Path, ...] = ()
        self.current_call: CallRecord | None = None
        self.current_second = 0
        self.current_duration = 0
        self.call_cards: dict[int, CallCard] = {}
        self.transcript_rows: list[tuple[TranscriptLine, TranscriptRow, QLabel]] = []
        self.active_transcript_index = -1
        self.nav_buttons: dict[str, QPushButton] = {}
        self.critical_line: QWidget | None = None
        self.network = QNetworkAccessManager(self)
        self.analysis_pool = QThreadPool(self)
        self.analysis_queue: list[int] = []
        self.analysis_running = False
        self.sort_mode = "original"

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
        self.setStyleSheet(self._stylesheet())
        self._show_empty_state()
        if self.secret_load_failed:
            QTimer.singleShot(0, lambda: self._show_toast("No se pudo recuperar una clave guardada; vuelve a ingresarla"))

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
        self._set_directory_display(self.saved_directory)
        directory_layout.addWidget(self.directory_label, 1)
        change = QPushButton("Cambiar")
        change.setObjectName("linkButton")
        change.setCursor(Qt.CursorShape.PointingHandCursor)
        change.clicked.connect(self._choose_directory)
        directory_layout.addWidget(change)
        layout.addWidget(directory)
        layout.addStretch()

        nav = QHBoxLayout()
        nav.setSpacing(4)
        for index, (key, text, icon) in enumerate((
            ("audit", "Auditoría", None),
            ("reports", "Reportes", None),
            ("config", "Configuración", QIcon(str(assets_path / "settings.svg"))),
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

        self.scan_button = QPushButton()
        self.scan_button.setObjectName("iconButton")
        self.scan_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.scan_button.setIconSize(QSize(17, 17))
        self.scan_button.setFixedSize(38, 36)
        self.scan_button.setAccessibleName("Escanear carpeta")
        self.scan_button.setToolTip("Escanear carpeta")
        self.scan_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scan_button.clicked.connect(self._scan_directory)
        layout.addWidget(self.scan_button)
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
            ("sensitive_metric", "0", "Sensibles", True),
            ("normal_metric", "0", "Sin novedad", False),
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
        self.search_input.setPlaceholderText("Buscar por archivo, agente o cliente")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(260)
        self.search_input.textChanged.connect(self._filter_calls)
        layout.addWidget(self.search_input)

        self.status_filter = QComboBox()
        self.status_filter.addItem("Todas las llamadas", "all")
        self.status_filter.addItem("Solo sensibles", "sensitive")
        self.status_filter.addItem("Sin novedad", "normal")
        self.status_filter.currentIndexChanged.connect(self._filter_calls)
        layout.addWidget(self.status_filter)

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
        self.sort_button = QPushButton("Organizar llamadas")
        self.sort_button.setObjectName("sortButton")
        self.sort_button.setAccessibleName("Organizar llamadas")
        self.sort_button.setToolTip("La prioridad sigue el orden de las palabras clave configuradas")
        sort_menu = QMenu(self.sort_button)
        for label, short_label, mode in (
            ("Prioridad de palabras clave", "Prioridad", "priority"),
            ("Mayor peso primero", "Mayor peso", "size_desc"),
            ("Menor peso primero", "Menor peso", "size_asc"),
            ("Más recientes", "Más recientes", "newest"),
            ("Más antiguas", "Más antiguas", "oldest"),
            ("Nombre A-Z", "Nombre A-Z", "name"),
            ("Orden original", "Original", "original"),
        ):
            action = sort_menu.addAction(label)
            action.triggered.connect(lambda _checked=False, m=mode, text=short_label: self._sort_calls(m, text))
        self.sort_button.setMenu(sort_menu)
        heading_layout.addWidget(self.sort_button)
        heading_layout.addStretch()
        layout.addWidget(heading)

        self.call_list = QListWidget()
        self.call_list.setObjectName("callList")
        self.call_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.call_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.call_list.setSpacing(0)
        self.call_list.setContentsMargins(0, 0, 0, 0)
        self.call_list.currentItemChanged.connect(self._on_call_selected)
        self._populate_call_list()
        layout.addWidget(self.call_list, 1)
        return panel

    def _populate_call_list(self) -> None:
        self._apply_call_sort()
        self.call_list.blockSignals(True)
        try:
            self.call_list.clear()
            self.call_cards.clear()
            for call in self.call_records:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, call.call_id)
                card = CallCard(call)
                item.setSizeHint(QSize(0, 100))
                card.clicked.connect(self._select_call_by_id)
                self.call_cards[call.call_id] = card
                self.call_list.addItem(item)
                self.call_list.setItemWidget(item, card)
        finally:
            self.call_list.blockSignals(False)

    def _apply_call_sort(self) -> None:
        def file_value(call: CallRecord, attribute: str) -> float:
            try:
                return float(getattr(call.source_path.stat(), attribute)) if call.source_path else 0
            except OSError:
                return 0

        if self.sort_mode == "priority":
            priorities = {
                keyword.strip().casefold(): index
                for index, keyword in enumerate(self.keywords_input.text().split(","))
                if keyword.strip()
            }
            self.call_records.sort(key=lambda call: (
                not call.sensitive,
                priorities.get(call.keyword.casefold(), len(priorities)),
                call.call_id,
            ))
        elif self.sort_mode == "size_desc":
            self.call_records.sort(key=lambda call: (-file_value(call, "st_size"), call.call_id))
        elif self.sort_mode == "size_asc":
            self.call_records.sort(key=lambda call: (file_value(call, "st_size"), call.call_id))
        elif self.sort_mode == "newest":
            self.call_records.sort(key=lambda call: (-file_value(call, "st_mtime"), call.call_id))
        elif self.sort_mode == "oldest":
            self.call_records.sort(key=lambda call: (file_value(call, "st_mtime"), call.call_id))
        elif self.sort_mode == "name":
            self.call_records.sort(key=lambda call: (call.filename.casefold(), call.call_id))
        else:
            self.call_records.sort(key=lambda call: call.call_id)

    def _sort_calls(self, mode: str, label: str) -> None:
        current = self.call_list.currentItem()
        selected_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.sort_mode = mode
        self.sort_button.setText(f"Orden: {label}")
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
        agent_space = QWidget()
        agent_space.setMinimumWidth(120)
        meta.addWidget(agent_space, 0, 0, 2, 1)
        self.agent_value = QLabel(frame)
        self.agent_value.hide()
        self.customer_value = self._add_meta(meta, 1, "CLIENTE")
        self.time_value = self._add_meta(meta, 2, "HORA")
        self.risk_value = self._add_meta(meta, 3, "RIESGO")
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
        self.jump_button.setObjectName("primaryButton")
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
        mode = QLabel("Diarización activa")
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
        self.reviewed_button.clicked.connect(lambda: self._show_toast("Llamada marcada como revisada"))
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
        metrics.addWidget(self._report_metric("report_normal_value", "Sin novedad", "Llamadas analizadas"))
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

    def _setting_text(self, key: str, default: str = "") -> str:
        return str(self.settings.value(key, default) or "")

    @staticmethod
    def _default_model(service: str, provider: str) -> str:
        return {
            ("transcription", "deepgram"): "nova-3",
            ("transcription", "openai"): "gpt-4o-mini-transcribe",
            ("analysis", "gemini"): "gemini-2.5-flash-lite",
            ("analysis", "openai"): "gpt-4o-mini",
        }[(service, provider)]

    @staticmethod
    def _api_environment_key(service: str, provider: str) -> str:
        if provider == "deepgram":
            return os.getenv("DEEPGRAM_API_KEY", "")
        if provider == "gemini":
            return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
        return os.getenv("OPENAI_API_KEY", "")

    def _load_api_key(self, service: str, provider: str) -> str:
        setting = f"api/{service}/{provider}/key"
        if not self.settings.contains(setting):
            return self._api_environment_key(service, provider)
        try:
            return unprotect_secret(self._setting_text(setting))
        except OSError:
            self.secret_load_failed = True
            return ""

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

        directory = QFrame()
        directory.setObjectName("contentPanel")
        directory_layout = QVBoxLayout(directory)
        directory_layout.setContentsMargins(20, 18, 20, 18)
        directory_layout.setSpacing(8)
        directory_title = QLabel("Directorio de grabaciones")
        directory_title.setObjectName("formTitle")
        directory_help = QLabel("Puede ser una carpeta local, una unidad compartida o una ruta NAS.")
        directory_help.setObjectName("pageSubtitle")
        directory_row = QHBoxLayout()
        self.config_directory = QLineEdit(self.saved_directory)
        directory_title.setBuddy(self.config_directory)
        choose = QPushButton("Examinar…")
        choose.setObjectName("secondaryButton")
        choose.clicked.connect(self._choose_directory)
        directory_row.addWidget(self.config_directory, 1)
        directory_row.addWidget(choose)
        directory_layout.addWidget(directory_title)
        directory_layout.addWidget(directory_help)
        directory_layout.addLayout(directory_row)
        outer.addWidget(directory)

        keywords = QFrame()
        keywords.setObjectName("contentPanel")
        keywords_layout = QVBoxLayout(keywords)
        keywords_layout.setContentsMargins(20, 18, 20, 18)
        keywords_layout.setSpacing(8)
        keywords_title = QLabel("Términos sensibles")
        keywords_title.setObjectName("formTitle")
        keywords_help = QLabel("Sepáralos con comas. Las coincidencias validadas se mostrarán en verde.")
        keywords_help.setObjectName("pageSubtitle")
        self.keywords_input = QLineEdit(self.saved_keywords)
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
            "Las claves se guardan cifradas para tu usuario de Windows."
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
        self._select_provider(self.transcription_provider, self._setting_text(
            "api/transcription/provider", os.getenv("SENTRY_TRANSCRIPTION_PROVIDER", "deepgram")
        ))
        transcription_provider = str(self.transcription_provider.currentData())
        self.transcription_model = QComboBox()
        self.transcription_model.setEditable(True)
        self.transcription_model.addItem(self._setting_text(
            f"api/transcription/{transcription_provider}/model",
            os.getenv("SENTRY_TRANSCRIPTION_MODEL", self._default_model("transcription", transcription_provider)),
        ))
        self.transcription_api_key = QLineEdit(self._load_api_key("transcription", transcription_provider))
        self.transcription_key_toggle, self.transcription_validate, self.transcription_api_status = self._add_api_fields(
            transcription,
            self.transcription_provider,
            self.transcription_model,
            self.transcription_api_key,
            "Clave de transcripción",
            "transcription",
        )
        if self.transcription_api_key.text():
            self._set_api_status(self.transcription_api_status, "Clave cargada", "idle")
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
        self._select_provider(self.analysis_provider, self._setting_text(
            "api/analysis/provider", os.getenv("SENTRY_ANALYSIS_PROVIDER", "gemini")
        ))
        analysis_provider = str(self.analysis_provider.currentData())
        self.analysis_model = QComboBox()
        self.analysis_model.setEditable(True)
        self.analysis_model.addItem(self._setting_text(
            f"api/analysis/{analysis_provider}/model",
            os.getenv("SENTRY_ANALYSIS_MODEL", self._default_model("analysis", analysis_provider)),
        ))
        self.analysis_api_key = QLineEdit(self._load_api_key("analysis", analysis_provider))
        self.analysis_key_toggle, self.analysis_validate, self.analysis_api_status = self._add_api_fields(
            analysis,
            self.analysis_provider,
            self.analysis_model,
            self.analysis_api_key,
            "Clave de análisis",
            "analysis",
        )
        if self.analysis_api_key.text():
            self._set_api_status(self.analysis_api_status, "Clave cargada", "idle")
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
        provider_id = str(provider.currentData())
        model.clear()
        model.addItem(self._setting_text(
            f"api/{service}/{provider_id}/model",
            self._default_model(service, provider_id),
        ))
        key.setText(self._load_api_key(service, provider_id))
        toggle = self.transcription_key_toggle if service == "transcription" else self.analysis_key_toggle
        toggle.setChecked(False)
        key.setEchoMode(QLineEdit.EchoMode.Password)
        button.setEnabled(True)
        button.setText("Validar")
        self._set_api_status(status, "Clave cargada" if key.text() else "Sin validar", "idle")

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
        page_index = {"audit": 0, "reports": 1, "config": 2}[key]
        self.pages.setCurrentIndex(page_index)
        for name, button in self.nav_buttons.items():
            button.setChecked(name == key)

    def _filter_calls(self) -> None:
        query = self.search_input.text().strip().casefold()
        status = self.status_filter.currentData()
        first_visible: QListWidgetItem | None = None
        for row, call in enumerate(self.call_records):
            searchable = f"{call.filename} {call.agent} {call.customer} {call.keyword} {call.snippet}".casefold()
            matches_query = not query or query in searchable
            matches_status = (
                status == "all"
                or (status == "sensitive" and call.sensitive)
                or (status == "normal" and not call.sensitive and call.risk != "Pendiente")
            )
            item = self.call_list.item(row)
            item.setHidden(not (matches_query and matches_status))
            if not item.isHidden():
                first_visible = first_visible or item
        current = self.call_list.currentItem()
        if (current is None or current.isHidden()) and first_visible is not None:
            self.call_list.setCurrentItem(first_visible)

    def _on_call_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        for card in self.call_cards.values():
            card.set_selected(False)
        if current is None:
            if not self.call_records:
                self._show_empty_state()
            return
        call_id = current.data(Qt.ItemDataRole.UserRole)
        self.call_cards[call_id].set_selected(True)
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
        self._update_metrics()

    def _update_metrics(self) -> None:
        total = len(self.call_records)
        sensitive = sum(call.sensitive for call in self.call_records)
        normal = sum(not call.sensitive and call.risk not in ("Pendiente", "Error") for call in self.call_records)
        self.files_metric.setText(str(total))
        self.sensitive_metric.setText(str(sensitive))
        self.normal_metric.setText(str(normal))
        self.normal_metric_label.setText("Sin novedad")
        self.report_total_value.setText(str(total))
        self.report_sensitive_value.setText(str(sensitive))
        self.report_normal_value.setText(str(normal))
        self.report_empty.setVisible(total == 0)
        self.report_download.setEnabled(total > 0)

    def _show_call(self, call: CallRecord) -> None:
        self._stop_playback()
        self.current_call = call
        self.detail_pages.setCurrentIndex(1)
        self.current_second = 0
        self.current_duration = call.duration
        source = call.source_path
        has_audio = source is not None and source.is_file()
        self.play_button.setEnabled(has_audio)
        self.original_button.setEnabled(has_audio)
        self.reviewed_button.setEnabled(True)
        self.media_player.setSource(QUrl.fromLocalFile(str(source)) if has_audio else QUrl())
        self.filename_label.setText(call.filename)
        badge_text = "Término sensible" if call.sensitive else "Sin alerta crítica"
        self.risk_badge.setText("Pendiente de análisis" if call.risk == "Pendiente" else badge_text)
        self.risk_badge.setProperty("sensitive", call.sensitive)
        self.risk_badge.style().unpolish(self.risk_badge)
        self.risk_badge.style().polish(self.risk_badge)
        self.duration_label.setText(format_time(call.duration))
        self.agent_value.setText(call.agent)
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

    def _render_transcript(self, call: CallRecord) -> None:
        while self.transcript_layout.count():
            item = self.transcript_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.critical_line = None
        self.transcript_rows.clear()
        self.active_transcript_index = -1
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
            speaker.setFixedWidth(54)
            text = QLabel(html.escape(line.text))
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
            if line.critical:
                self.critical_line = row
        self.transcript_layout.addStretch()
        self._sync_transcript(self.current_second)

    def _sync_transcript(self, position_seconds: float, scroll: bool = False) -> None:
        active = next(
            (index for index in range(len(self.transcript_rows) - 1, -1, -1) if position_seconds >= self.transcript_rows[index][0].second),
            -1,
        )
        for index, (line, row, text_label) in enumerate(self.transcript_rows):
            state = "played" if index < active else "active" if index == active else "upcoming"
            if row.property("playbackState") != state:
                row.setProperty("playbackState", state)
                row.style().unpolish(row)
                row.style().polish(row)

            word_count = len(line.text.split())
            if state == "played":
                heard_words = word_count
            elif state == "active":
                heard_words = self._heard_word_count(index, position_seconds)
            else:
                heard_words = 0
            keyword = self.current_call.keyword if self.current_call else ""
            progress_html = self._transcript_progress_html(line, heard_words, keyword)
            if text_label.text() != progress_html:
                text_label.setText(progress_html)

        if scroll and active >= 0 and active != self.active_transcript_index:
            self.transcript_scroll.ensureWidgetVisible(self.transcript_rows[active][1], 0, 50)
        self.active_transcript_index = active

    def _heard_word_count(self, index: int, position_seconds: float) -> int:
        line = self.transcript_rows[index][0]
        word_count = len(line.text.split())
        if not word_count:
            return 0
        if line.word_seconds:
            return min(word_count, sum(timestamp <= position_seconds for timestamp in line.word_seconds))
        next_second = (
            self.transcript_rows[index + 1][0].second
            if index + 1 < len(self.transcript_rows)
            else self.current_duration
        )
        duration = max(1, next_second - line.second)
        ratio = max(0.0, min(1.0, (position_seconds - line.second) / duration))
        return min(word_count, max(1, int(ratio * word_count) + 1))

    @staticmethod
    def _transcript_progress_html(line: TranscriptLine, heard_words: int, keyword: str) -> str:
        rendered = []
        for index, word in enumerate(line.text.split()):
            safe_word = html.escape(word)
            styles = []
            if index < heard_words:
                styles.append(f"color:{PALETTE['green_accessible']}")
            if line.critical and keyword.casefold() in word.casefold():
                styles.extend((f"color:{PALETTE['green_accessible']}", "font-weight:700"))
            rendered.append(f"<span style='{';'.join(styles)}'>{safe_word}</span>" if styles else safe_word)
        return " ".join(rendered)

    def _toggle_playback(self) -> None:
        if self.current_call is None:
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
        previous_second = self.current_second
        self.current_second = max(0, position_ms // 1000)
        self.timeline.set_position(self.current_second)
        self._update_time_display()
        if self.current_second != previous_second:
            playing = self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            self._sync_transcript(position_ms / 1000, scroll=playing)

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
        if self.current_call is None:
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
        if self.current_call is None:
            return
        source = self.current_call.source_path
        if source is None or not source.is_file():
            self._show_toast("El archivo de audio ya no está disponible")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(source))):
            self._show_toast("Windows no pudo abrir el archivo con el reproductor predeterminado")

    def _jump_to_evidence(self) -> None:
        if self.current_call is None or self.current_call.hit_second is None:
            return
        self._seek_audio(self.current_call.hit_second)
        if self.critical_line is not None:
            self.transcript_scroll.ensureWidgetVisible(self.critical_line, 0, 40)
        self._show_toast(f"Evidencia localizada en {format_time(self.current_call.hit_second)}")

    def _choose_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de grabaciones", self.config_directory.text())
        if not selected:
            return
        normalized = selected.replace("/", "\\")
        self.config_directory.setText(normalized)
        self._set_directory_display(normalized)
        self._show_toast("Directorio actualizado")

    def _set_directory_display(self, directory: str) -> None:
        self.directory_label.setText(Path(directory).name if directory else "Sin directorio")
        self.directory_label.setToolTip(directory or "No se ha configurado una carpeta")

    def _save_settings(self) -> None:
        directory = self.config_directory.text().strip()
        keywords = ", ".join(word.strip() for word in self.keywords_input.text().split(",") if word.strip())
        services = {
            "transcription": (
                str(self.transcription_provider.currentData()),
                self.transcription_model.currentText().strip(),
                self.transcription_api_key.text().strip(),
            ),
            "analysis": (
                str(self.analysis_provider.currentData()),
                self.analysis_model.currentText().strip(),
                self.analysis_api_key.text().strip(),
            ),
        }
        try:
            protected_keys = {service: protect_secret(values[2]) for service, values in services.items()}
        except OSError:
            self._show_toast("Windows no pudo proteger las claves; no se guardaron los cambios")
            return

        self.settings.setValue("directory", directory)
        self.settings.setValue("keywords", keywords)
        for service, (provider, model, _key) in services.items():
            self.settings.setValue(f"api/{service}/provider", provider)
            self.settings.setValue(f"api/{service}/{provider}/model", model)
            self.settings.setValue(f"api/{service}/{provider}/key", protected_keys[service])
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            self._show_toast("No se pudieron guardar los ajustes en Windows")
            return

        self.saved_directory = directory
        self.saved_keywords = keywords
        self.keywords_input.setText(keywords)
        self._set_directory_display(directory)
        for service, (_provider, _model, key) in services.items():
            _combo, _models, _field, _button, status = self._api_controls(service)
            self._set_api_status(status, "Clave guardada" if key else "Sin clave guardada", "valid" if key else "idle")
        self._show_toast("Ajustes guardados de forma permanente")

    def _scan_directory(self) -> None:
        directory_text = self.config_directory.text().strip()
        if not directory_text:
            self._show_toast("Selecciona un directorio antes de escanear")
            self._switch_page("config")
            self.config_directory.setFocus()
            return

        self.scan_button.setEnabled(False)
        self.scan_button.setToolTip("Escaneando…")
        directory = Path(directory_text).expanduser()
        # ponytail: el escaneo síncrono basta para esta etapa; mover a un hilo si un NAS grande bloquea la interfaz.
        QTimer.singleShot(0, lambda: self._finish_scan(directory))

    def _finish_scan(self, directory: Path) -> None:
        try:
            self.detected_audio_files = scan_audio_files(directory)
        except FileNotFoundError:
            message = "La carpeta seleccionada no existe"
        except NotADirectoryError:
            message = "La ruta seleccionada no es una carpeta"
        except OSError:
            message = "No se pudo leer la carpeta seleccionada"
        else:
            count = len(self.detected_audio_files)
            self.call_records = [call_record_from_audio(path, index) for index, path in enumerate(self.detected_audio_files, 1)]
            self.calls = {call.call_id: call for call in self.call_records}
            self._populate_call_list()
            self.search_input.clear()
            self.status_filter.setCurrentIndex(0)
            self._update_metrics()
            if self.call_records:
                self.call_list.setCurrentRow(0)
            else:
                self._show_empty_state(
                    "No se encontraron audios",
                    "La carpeta no contiene archivos WAV o MP3. Elige otra carpeta o vuelve a escanear.",
                )
            self._filter_calls()
            noun = "audio encontrado" if count == 1 else "audios encontrados"
            message = f"Escaneo completado · {count} {noun}"
            self._start_automatic_analysis()
        self.scan_button.setEnabled(True)
        self.scan_button.setToolTip("Escanear carpeta")
        self._show_toast(message)

    def _start_automatic_analysis(self) -> None:
        transcription_key = self.transcription_api_key.text().strip()
        if not transcription_key:
            return
        self.analysis_queue = [call.call_id for call in self.call_records if call.source_path is not None]
        if self.analysis_queue:
            self.analysis_running = True
            self._analyze_next_call()

    def _analyze_next_call(self) -> None:
        if not self.analysis_queue:
            self.analysis_running = False
            self._show_toast("Análisis automático completado")
            return
        call_id = self.analysis_queue.pop(0)
        call = self.calls.get(call_id)
        if call is None or call.source_path is None:
            self._analyze_next_call()
            return
        self._show_toast(f"Analizando {call.filename}…")
        transcription = (
            str(self.transcription_provider.currentData()),
            self.transcription_model.currentText().strip(),
            self.transcription_api_key.text().strip(),
        )
        analysis = (
            str(self.analysis_provider.currentData()),
            self.analysis_model.currentText().strip(),
            self.analysis_api_key.text().strip(),
        )
        keywords = tuple(word.strip() for word in self.keywords_input.text().split(",") if word.strip())
        job = AudioAnalysisJob(call_id, call.source_path, transcription, analysis, keywords)
        job.signals.finished.connect(self._finish_automatic_analysis)
        job.signals.failed.connect(self._fail_automatic_analysis)
        self.analysis_pool.start(job)

    def _finish_automatic_analysis(self, call_id: int, result: object) -> None:
        call = self.calls.get(call_id)
        if call is None or not isinstance(result, dict):
            self._analyze_next_call()
            return
        matches = [str(match) for match in result.get("matches", [])]
        parsed_lines = []
        for line in result.get("lines", []):
            if not isinstance(line, dict) or not str(line.get("text", "")).strip():
                continue
            text = str(line["text"])
            raw_word_seconds = line.get("word_seconds", ())
            word_seconds = tuple(
                float(second)
                for second in raw_word_seconds
                if isinstance(second, (int, float))
            ) if isinstance(raw_word_seconds, (list, tuple)) else ()
            parsed_lines.append(TranscriptLine(
                int(line.get("second", 0)),
                str(line.get("speaker", "Participante")),
                text,
                any(match.casefold() in text.casefold() for match in matches),
                word_seconds,
            ))
        lines = tuple(parsed_lines)
        updated = replace(
            call,
            sensitive=bool(matches),
            keyword=matches[0] if matches else "Sin alertas",
            hit_second=result.get("hit_second"),
            risk="Crítica" if matches else "Baja",
            category="Alerta sensible" if matches else "Sin novedad",
            summary=str(result.get("summary", "Transcripción completada.")),
            snippet=(lines[0].text if lines else "Sin texto transcrito")[:150],
            transcript=lines or (TranscriptLine(0, "Transcripción", "No se recibió texto."),),
        )
        self.calls[call_id] = updated
        self.call_records = [updated if item.call_id == call_id else item for item in self.call_records]
        selected_id = self.current_call.call_id if self.current_call else call_id
        self._populate_call_list()
        self._filter_calls()
        self._select_call_by_id(selected_id if selected_id == call_id else call_id)
        self._update_metrics()
        self._analyze_next_call()

    def _fail_automatic_analysis(self, call_id: int, error: str) -> None:
        call = self.calls.get(call_id)
        if call is not None:
            updated = replace(call, risk="Error", summary="No se pudo analizar este audio. Revisa la API y vuelve a escanear.")
            self.calls[call_id] = updated
            self.call_records = [updated if item.call_id == call_id else item for item in self.call_records]
            self._populate_call_list()
            self._filter_calls()
            self._update_metrics()
        self._show_toast(f"Error al analizar {call.filename if call else 'el audio'}")
        self._analyze_next_call()

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
        self._position_toast()

    @staticmethod
    def _stylesheet() -> str:
        combo_arrow = (Path(__file__).resolve().parents[1] / "assets" / "chevron-down.svg").as_posix()
        return f"""
            * {{
                font-family: "Inter", "Segoe UI", sans-serif;
                font-size: 13px;
                color: {PALETTE['text_soft']};
            }}
            QMainWindow, QWidget#appRoot, QWidget#auditPage, QWidget#reportsPage, QWidget#configPage {{
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
                border: 1px solid #cfe7d3;
            }}
            QPushButton#secondaryButton, QPushButton#iconButton, QPushButton#sortButton {{
                background: {PALETTE['panel']};
                color: {PALETTE['text_soft']};
                border: 1px solid {PALETTE['border_strong']};
            }}
            QPushButton#iconButton {{ padding: 0; }}
            QPushButton#sortButton {{ min-width: 150px; text-align: left; padding-right: 26px; }}
            QPushButton#secondaryButton:hover, QPushButton#iconButton:hover, QPushButton#sortButton:hover {{ color: {PALETTE['text']}; background: {PALETTE['surface']}; border-color: #afb6af; }}
            QPushButton#primaryButton {{
                background: {PALETTE['green_accessible']};
                color: white;
                border: 1px solid {PALETTE['green_accessible']};
            }}
            QPushButton#primaryButton:hover {{ background: #238537; }}
            QPushButton#primaryButton:disabled {{
                background: #eceeec;
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
            QLineEdit::placeholder {{ color: #767676; }}
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
            QFrame#callCard:hover {{ background: {PALETTE['surface']}; }}
            QFrame#callCard[selected="true"] {{
                background: #f3faf4;
                border-left: 1px solid {PALETTE['green_deep']};
                border-bottom: 1px solid {PALETTE['border']};
            }}
            QFrame#alertDot {{ background: {PALETTE['green_lime']}; border-radius: 4px; }}
            QFrame#normalDot {{ background: {PALETTE['border_strong']}; border-radius: 4px; }}
            QLabel#callAgent {{ color: {PALETTE['text']}; font-weight: 600; }}
            QLabel#callCustomer {{ color: {PALETTE['text']}; font-weight: 600; }}
            QLabel#sensitiveBadge, QLabel#riskBadge[sensitive="true"] {{
                color: {PALETTE['green_accessible']};
                background: {PALETTE['green_soft']};
                border: 1px solid #cfe7d3;
                border-radius: 5px;
                padding: 2px 6px;
                font-size: 9px;
                font-weight: 600;
            }}
            QLabel#neutralBadge, QLabel#riskBadge[sensitive="false"] {{
                color: {PALETTE['gray']};
                background: #f0f1f0;
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
            QPushButton#playButton:hover {{ background: #238537; }}
            QPushButton#playButton:disabled {{
                background: #eceeec;
                border-color: {PALETTE['border']};
            }}
            QScrollArea#transcriptScroll, QWidget#transcriptBody {{ background: transparent; border: none; }}
            QFrame#transcriptRow {{
                background: transparent;
                border: none;
                border-bottom: 1px solid #edf0ed;
                border-radius: 0;
            }}
            QFrame#transcriptRow:hover {{ background: #f7faf7; }}
            QFrame#transcriptRow[critical="true"] {{
                background: #f7fbf7;
                border: 1px solid #d8eadb;
                border-radius: 7px;
            }}
            QFrame#transcriptRow[playbackState="active"] {{
                background: {PALETTE['green_soft']};
                border: 1px solid #cbe5cf;
                border-radius: 7px;
            }}
            QFrame#transcriptRow:focus {{ border: 2px solid {PALETTE['green_deep']}; border-radius: 7px; }}
            QLabel#transcriptStamp {{ color: {PALETTE['muted']}; }}
            QLabel#criticalStamp {{ color: {PALETTE['green_accessible']}; font-weight: 650; }}
            QLabel#speaker {{ color: {PALETTE['text']}; font-weight: 600; }}
            QFrame#transcriptRow[playbackState="active"] QLabel#transcriptStamp,
            QFrame#transcriptRow[playbackState="active"] QLabel#speaker {{ color: {PALETTE['green_accessible']}; }}
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
            QPushButton#validateButton:hover {{ background: #238537; }}
            QPushButton#validateButton:disabled {{
                background: #eceeec;
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
                background: {PALETTE['charcoal']};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 11px 16px;
                font-weight: 500;
            }}
            QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
            QScrollBar::handle:vertical {{ background: {PALETTE['border_strong']}; min-height: 28px; border-radius: 4px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QToolTip {{
                background: {PALETTE['charcoal']};
                color: white;
                border: none;
                padding: 6px;
            }}
        """
