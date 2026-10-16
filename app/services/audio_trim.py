"""Recorte local de silencios en WAV para no pagar transcripción de audio sin voz.

Solo se recortan el inicio y el final; el audio intermedio se envía intacto, de modo
que las marcas de tiempo devueltas por la API se corrigen sumando un único desfase.
Formatos que no se pueden leer sin dependencias externas se envían sin cambios.
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass
from pathlib import Path
import struct
import sys

# shortcut: solo silencio digital; ampliar con audio calibrado si se necesita detectar ruido de fondo.
WINDOW_SECONDS = 0.03
LEAD_PADDING_SECONDS = 0.3
TAIL_PADDING_SECONDS = 0.5
# Recortar menos de esto no compensa reescribir el archivo.
MIN_SAVED_SECONDS = 1.0

_PCM, _ALAW, _MULAW, _EXTENSIBLE = 1, 6, 7, 0xFFFE


@dataclass(frozen=True)
class TrimmedAudio:
    data: bytes | None  # None: no hubo recorte útil; se envía el archivo original.
    offset_seconds: float
    kept_seconds: float
    original_seconds: float
    has_speech: bool


def _mulaw_magnitude(value: int) -> int:
    value = ~value & 0xFF
    exponent, mantissa = (value >> 4) & 0x07, value & 0x0F
    return ((mantissa << 3) + 0x84 << exponent) - 0x84


def _alaw_magnitude(value: int) -> int:
    value ^= 0x55
    exponent, mantissa = (value >> 4) & 0x07, value & 0x0F
    if exponent == 0:
        return (mantissa << 4) + 8
    return ((mantissa << 4) + 0x108) << (exponent - 1)


_MULAW_TABLE = [_mulaw_magnitude(value) for value in range(256)]
_ALAW_TABLE = [_alaw_magnitude(value) for value in range(256)]


def _chunks(raw: bytes):
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return None
    position, chunks = 12, {}
    while position + 8 <= len(raw):
        name, size = raw[position:position + 4], struct.unpack_from("<I", raw, position + 4)[0]
        start = position + 8
        end = start + size
        if end > len(raw):
            return None
        if name in {b'fmt ', b'data'} and name in chunks:
            return None
        chunks.setdefault(name, (start, end))
        position = start + size + (size & 1)
    return chunks


def _samples(data, code: str):
    if sys.byteorder == "little":
        view = memoryview(data)
        return view[:len(view) - len(view) % array(code).itemsize].cast(code)
    samples = array(code)
    samples.frombytes(bytes(data[:len(data) - len(data) % samples.itemsize]))
    samples.byteswap()
    return samples


def _window_has_signal(data, encoding: int, width: int, channels: int, rate: int):
    """Examina todas las muestras y canales, incluida la última ventana incompleta."""
    frame = width * channels
    frames_per_window = max(1, int(rate * WINDOW_SECONDS))
    window_bytes = frames_per_window * frame
    if encoding in {_MULAW, _ALAW}:
        table = _MULAW_TABLE if encoding == _MULAW else _ALAW_TABLE
        silence = 8 if encoding == _ALAW else 0
        samples = None
    elif width == 2:
        table = None
        samples = _samples(data, "h")
        silence = 0
    elif width == 4:
        table = None
        samples = _samples(data, "i")
        silence = 0
    elif width == 1:
        table = samples = None
        silence = 128
    else:
        return None
    per_window = frames_per_window * channels
    def has_signal(index: int) -> bool:
        if samples is not None:
            values = samples[index * per_window:(index + 1) * per_window]
        else:
            chunk = data[index * window_bytes:(index + 1) * window_bytes]
            values = (table[value] for value in chunk) if table is not None else chunk
        return any(value != silence for value in values)

    return has_signal, (len(data) + window_bytes - 1) // window_bytes, frames_per_window


def trim_silence(path: Path) -> TrimmedAudio | None:
    """Recorta silencio inicial/final de un WAV. None si el formato no es compatible."""
    path = Path(path)
    if path.suffix.casefold() != ".wav":
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    chunks = _chunks(raw)
    if not chunks or b"fmt " not in chunks or b"data" not in chunks:
        return None
    fmt_start, fmt_end = chunks[b"fmt "]
    if fmt_end - fmt_start < 16:
        return None
    encoding, channels, rate, _byte_rate, block_align, bits = struct.unpack_from("<HHIIHH", raw, fmt_start)
    if encoding == _EXTENSIBLE:
        if (fmt_end - fmt_start < 40 or raw[fmt_start + 26:fmt_start + 40]
                != b'\0\0\0\0\x10\0\x80\0\0\xaa\0\x38\x9b\x71'):
            return None
        encoding = struct.unpack_from("<H", raw, fmt_start + 24)[0]
    width = bits // 8
    if (encoding not in {_PCM, _MULAW, _ALAW} or not channels or not rate
            or bits not in {8, 16, 32} or block_align != width * channels
            or (encoding in {_MULAW, _ALAW} and bits != 8)):
        return None
    data_start, data_end = chunks[b"data"]
    data = memoryview(raw)[data_start:data_end]
    data = data[:len(data) - len(data) % block_align]
    total_frames = len(data) // block_align
    original_seconds = total_frames / rate
    measured = _window_has_signal(data, encoding, width, channels, rate)
    if measured is None:
        return None
    has_signal, windows, frames_per_window = measured
    first = last = None
    for index in range(windows):
        if has_signal(index):
            first = last = index
            break
    if first is None:
        return TrimmedAudio(None, 0.0, 0.0, original_seconds, has_speech=False)
    for index in range(windows - 1, last, -1):
        if has_signal(index):
            last = index
            break
    first_frame = max(0, first * frames_per_window - int(LEAD_PADDING_SECONDS * rate))
    last_frame = min(total_frames, (last + 1) * frames_per_window + int(TAIL_PADDING_SECONDS * rate))
    kept_seconds = (last_frame - first_frame) / rate
    if original_seconds - kept_seconds < MIN_SAVED_SECONDS:
        return TrimmedAudio(None, 0.0, original_seconds, original_seconds, has_speech=True)
    body = data[first_frame * block_align:last_frame * block_align]
    fmt = raw[fmt_start:fmt_end]
    # Se conserva la codificación original; solo cambian los tamaños del encabezado.
    parts = [b"fmt ", struct.pack("<I", len(fmt)), fmt, b"\0" * (len(fmt) & 1)]
    if b"fact" in chunks:
        parts += [b"fact", struct.pack("<II", 4, last_frame - first_frame)]
    parts += [b"data", struct.pack("<I", len(body)), body, b"\0" * (len(body) & 1)]
    size = 4 + sum(len(part) for part in parts)
    # Una sola copia del audio: `body` es una vista del archivo leído.
    return TrimmedAudio(b"".join([b"RIFF", struct.pack("<I", size), b"WAVE", *parts]),
                        first_frame / rate, kept_seconds, original_seconds, has_speech=True)
