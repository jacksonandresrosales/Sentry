"""Transcripción, clasificación y caché persistente de grabaciones."""
from __future__ import annotations

import hashlib
import json
import mimetypes
from concurrent.futures import Future
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import re
import threading
import time
import unicodedata
from urllib.parse import quote, urlsplit

import requests


TRANSCRIPTION_VERSION = "2"
ANALYSIS_VERSION = "4"
_HTTP = threading.local()
_IN_FLIGHT: dict[tuple[str, str, str], Future] = {}
_IN_FLIGHT_LOCK = threading.Lock()
_PROVIDER_COOLDOWNS: dict[str, float] = {}
_PROVIDER_LOCK = threading.Lock()


class AnalysisError(RuntimeError):
    pass


def _cached_computation(database, table: str, key: str, compute):
    """Comparte una petición en curso entre audios idénticos, incluso de rutas distintas."""
    cached = database.cache_get(table, key)
    if cached is not None:
        return cached, True
    scope = str(getattr(database, "path", id(database)))
    flight_key = (scope, table, key)
    with _IN_FLIGHT_LOCK:
        future = _IN_FLIGHT.get(flight_key)
        owner = future is None
        if owner:
            future = _IN_FLIGHT[flight_key] = Future()
    if not owner:
        return future.result(), True
    try:
        # Otro hilo pudo terminar entre la primera lectura y la adquisición del turno.
        result = database.cache_get(table, key)
        was_cached = result is not None
        if result is None:
            result = compute()
            database.cache_set(table, key, result)
        future.set_result(result)
        return result, was_cached
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.pop(flight_key, None)


def _retry_after_seconds(response) -> float | None:
    value = str(response.headers.get("Retry-After", "")).strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if seconds < float("inf") else None


def _defer_provider(url: str, seconds: float):
    origin = urlsplit(url).netloc.casefold()
    with _PROVIDER_LOCK:
        _PROVIDER_COOLDOWNS[origin] = max(
            _PROVIDER_COOLDOWNS.get(origin, 0), time.monotonic() + seconds,
        )


def _wait_for_provider(url: str):
    origin = urlsplit(url).netloc.casefold()
    while True:
        with _PROVIDER_LOCK:
            remaining = _PROVIDER_COOLDOWNS.get(origin, 0) - time.monotonic()
            if remaining <= 0:
                _PROVIDER_COOLDOWNS.pop(origin, None)
                return
            if remaining > 300:
                raise AnalysisError("La API pidió una pausa superior a cinco minutos. Vuelve a analizar más tarde.")
        # Revisa extensiones del límite compartido sin bloquear el candado.
        time.sleep(min(remaining, 1.0))


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cached_file_hash(database, path: Path) -> str:
    path = Path(path).resolve(strict=True)
    stat = path.stat()
    digest = database.cached_file_digest(path, stat.st_size, stat.st_mtime_ns)
    if digest is None:
        digest = file_hash(path)
        database.save_file_digest(path, stat.st_size, stat.st_mtime_ns, digest)
    return digest


def _request(method, url, *, retries=3, **kwargs):
    last = None
    for attempt in range(retries):
        _wait_for_provider(url)
        data = kwargs.get("data")
        if hasattr(data, "seek"):
            data.seek(0)
        files = kwargs.get("files")
        if isinstance(files, dict):
            for value in files.values():
                candidates = value if isinstance(value, tuple) else (value,)
                for stream in candidates:
                    if hasattr(stream, "seek"):
                        stream.seek(0)
                        break
        try:
            session = getattr(_HTTP, "session", None)
            if session is None:
                session = _HTTP.session = requests.Session()
                adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
                session.mount("https://", adapter)
            response = session.request(method, url, timeout=(15, 300), **kwargs)
        except requests.RequestException as exc:
            last = exc
            if attempt + 1 == retries:
                raise AnalysisError(f"No se pudo conectar con la API: {exc}") from exc
        else:
            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    raise AnalysisError("La API devolvió una respuesta que no es JSON.") from exc
            message = ""
            try:
                body = response.json()
                message = body.get("error", {}).get("message", "") if isinstance(body.get("error"), dict) else str(body.get("error", ""))
            except ValueError:
                pass
            retry_after = _retry_after_seconds(response)
            if response.status_code == 429:
                _defer_provider(url, retry_after if retry_after is not None else 2 ** attempt)
            if response.status_code not in {429, 500, 502, 503, 504} or attempt + 1 == retries:
                if response.status_code in {401, 403}:
                    raise AnalysisError("La API rechazó la clave configurada.")
                raise AnalysisError(f"La API respondió {response.status_code}: {message or 'solicitud rechazada'}")
            last = AnalysisError(f"Respuesta temporal {response.status_code}")
            if retry_after is not None and retry_after > 300:
                raise AnalysisError("La API pidió una pausa superior a cinco minutos. Vuelve a analizar más tarde.")
            if response.status_code == 429:
                continue
            if retry_after is not None:
                time.sleep(retry_after)
                continue
        time.sleep(2 ** attempt)
    raise AnalysisError(str(last))


def _deepgram_transcript(path: Path, key: str, model: str, keywords: list[str]):
    params = [("model", model), ("language", "es-419"), ("smart_format", "true"),
              ("utterances", "true"), ("diarize_model", "latest"), ("mip_opt_out", "true")]
    params.extend(("keyterm", word) for word in keywords)
    with path.open("rb") as stream:
        payload = _request("POST", "https://api.deepgram.com/v1/listen", params=params,
                           headers={"Authorization": f"Token {key}",
                                    "Content-Type": mimetypes.guess_type(path.name)[0] or "application/octet-stream"},
                           data=stream)
    results = payload.get("results", {})
    utterances = results.get("utterances") or []
    segments = [{"second": float(item.get("start", 0)), "speaker": f"Hablante {int(item.get('speaker', 0)) + 1}",
                 "text": str(item.get("transcript", "")).strip(),
                 "word_seconds": [float(word.get("start", 0)) for word in (item.get("words") or [])
                                  if isinstance(word, dict)]}
                for item in utterances if item.get("transcript")]
    alternative = (((results.get("channels") or [{}])[0].get("alternatives") or [{}])[0])
    text = str(alternative.get("transcript", "")).strip()
    if not segments and text:
        segments = [{"second": 0.0, "speaker": "Hablante", "text": text}]
    speakers = len({item["speaker"] for item in segments}) if utterances else None
    return {"text": text or " ".join(item["text"] for item in segments), "segments": segments,
            "speaker_count": speakers, "provider": "deepgram", "model": model}


def _openai_transcript(path: Path, key: str, model: str):
    data = {"model": model, "language": "es", "response_format": "json"}
    if "diarize" in model:
        data["response_format"] = "diarized_json"
        data["chunking_strategy"] = "auto"
    with path.open("rb") as stream:
        payload = _request("POST", "https://api.openai.com/v1/audio/transcriptions",
                           headers={"Authorization": f"Bearer {key}"}, data=data,
                           files={"file": (path.name, stream, mimetypes.guess_type(path.name)[0] or "application/octet-stream")})
    text = str(payload.get("text", "")).strip()
    raw_segments = payload.get("segments") or []
    segments = [{"second": float(item.get("start", 0)), "speaker": str(item.get("speaker", "Hablante")),
                 "text": str(item.get("text", "")).strip(),
                 "word_seconds": [float(word.get("start", 0)) for word in (item.get("words") or [])
                                  if isinstance(word, dict)]}
                for item in raw_segments if item.get("text")]
    if not segments and text:
        segments = [{"second": 0.0, "speaker": "Hablante", "text": text}]
    speakers = len({item["speaker"] for item in segments}) if raw_segments else None
    return {"text": text, "segments": segments, "speaker_count": speakers,
            "provider": "openai", "model": model}


def transcribe(path: Path, provider: str, key: str, model: str, keywords: list[str]):
    if provider == "deepgram":
        return _deepgram_transcript(path, key, model, keywords)
    if provider == "openai":
        return _openai_transcript(path, key, model)
    raise AnalysisError(f"Proveedor de transcripción no compatible: {provider}")


def _normalized(text: str) -> str:
    return "".join(character for character in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(character))


def assign_roles(transcript: dict) -> dict:
    """Asigna Asesor/Cliente con diarización y lenguaje operativo, sin otra llamada API."""
    result = dict(transcript)
    segments = [dict(item) for item in transcript.get("segments", [])]
    speakers = list(dict.fromkeys(str(item.get("speaker", "Hablante")) for item in segments))
    if not speakers or all(speaker in {"Asesor", "Cliente"} for speaker in speakers):
        result["segments"] = segments
        return result
    if len(speakers) == 1:
        if transcript.get("speaker_count") == 1:
            for item in segments:
                item["speaker"] = "Asesor"
        result["segments"] = segments
        return result
    advisor_phrases = (
        ("le atiende", 4), ("le saluda", 4), ("mi nombre es", 4), ("atencion al cliente", 3),
        ("servicio al cliente", 3), ("en que puedo ayudar", 4), ("como puedo ayudar", 4),
        ("con quien tengo el gusto", 4), ("me confirma", 2), ("indiqueme", 2),
        ("permitame validar", 2), ("voy a validar", 2), ("voy a revisar", 2),
        ("numero de caso", 2), ("gracias por comunicarse", 4), ("gracias por llamar", 4),
        ("bienvenido a", 3), ("ecuaconexion", 4),
    )
    customer_phrases = (
        ("llamo porque", 4), ("llamo por", 3), ("quiero cancelar", 3), ("quiero dar de baja", 3),
        ("tengo un problema", 3), ("me cobraron", 3), ("me estan cobrando", 3),
        ("mi factura", 2), ("mi servicio", 2), ("necesito ayuda", 2), ("quiero reclamar", 2),
    )
    advisor_scores = {speaker: 0 for speaker in speakers}
    customer_scores = {speaker: 0 for speaker in speakers}
    for index, item in enumerate(segments):
        speaker = str(item.get("speaker", "Hablante"))
        text = _normalized(str(item.get("text", "")))
        advisor_scores[speaker] += sum(weight for phrase, weight in advisor_phrases if phrase in text)
        customer_scores[speaker] += sum(weight for phrase, weight in customer_phrases if phrase in text)
        if index == 0:
            advisor_scores[speaker] += 1

    def confident_winner(scores):
        ranked = sorted(speakers, key=lambda speaker: scores[speaker], reverse=True)
        return ranked[0] if scores[ranked[0]] >= 3 and (len(ranked) == 1 or scores[ranked[0]] - scores[ranked[1]] >= 2) else None

    advisor = confident_winner(advisor_scores)
    customer = confident_winner(customer_scores)
    if advisor is None and customer is not None and len(speakers) == 2:
        advisor = next(speaker for speaker in speakers if speaker != customer)
    if advisor is None:
        advisor = speakers[0]
    for item in segments:
        item["speaker"] = "Asesor" if str(item.get("speaker", "")) == advisor else "Cliente"
    result["segments"] = segments
    return result


def detected_keyword_hits(transcript: dict, keywords: list[str], validated=()):
    validated_set = {_normalized(str(value)) for value in validated}
    normalized_text = _normalized(str(transcript.get("text", "")))
    normalized_segments = [
        (item, _normalized(str(item.get("text", ""))))
        for item in transcript.get("segments", [])
        if isinstance(item, dict)
    ]
    hits = []
    for keyword in keywords:
        needle = _normalized(keyword.strip())
        if not needle:
            continue
        segment = next((item for item, normalized in normalized_segments if needle in normalized), None)
        if segment is None and needle not in normalized_text:
            continue
        hits.append({"keyword": keyword.strip(), "speaker": segment.get("speaker") if segment else None,
                     "second": segment.get("second", 0) if segment else 0,
                     "snippet": segment.get("text", "") if segment else str(transcript.get("text", ""))[:300],
                     "validated": needle in validated_set})
    return hits


def keyword_signature(keywords) -> str:
    """Huella estable: ignora mayúsculas, acentos, duplicados y el orden."""
    normalized = sorted({_normalized(str(word).strip()) for word in keywords if str(word).strip()})
    return hashlib.sha256("\n".join(normalized).encode()).hexdigest()


RESULT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "category": {"type": "string", "enum": ["ALERTA", "NORMAL"]},
        "summary": {"type": "string"},
        "sentiment": {"type": "string", "enum": ["POSITIVO", "NEUTRAL", "NEGATIVO", "MOLESTO"]},
        "risk": {"type": "string", "enum": ["BAJO", "MEDIO", "ALTO", "CRÍTICO"]},
        "validated_keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["category", "summary", "sentiment", "risk", "validated_keywords"],
}


def _prompt(excerpt: str, keywords: list[str]) -> str:
    return ("Clasifica esta llamada ecuatoriana. ALERTA solo si hay amenaza, reclamo o intención real relacionada "
            "con los términos sensibles; una mención comercial sin riesgo es NORMAL. No inventes hechos. "
            "Devuelve exclusivamente el JSON solicitado y un resumen breve en español.\n"
            f"Términos sensibles: {', '.join(keywords)}\nTranscripción:\n{excerpt[:8000]}")


def _gemini_analysis(text: str, keywords: list[str], key: str, model: str):
    payload = _request("POST", f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model)}:generateContent",
                       headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                       json={"contents": [{"parts": [{"text": _prompt(text, keywords)}]}],
                             "generationConfig": {"temperature": 0, "maxOutputTokens": 160,
                                                   "responseMimeType": "application/json",
                                                   "responseJsonSchema": RESULT_SCHEMA}})
    try:
        content = payload["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise AnalysisError("Gemini no devolvió un análisis estructurado válido.") from exc


def _openai_analysis(text: str, keywords: list[str], key: str, model: str):
    payload = _request("POST", "https://api.openai.com/v1/chat/completions",
                       headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                       json={"model": model, "messages": [{"role": "user", "content": _prompt(text, keywords)}],
                             "temperature": 0, "max_completion_tokens": 160,
                             "response_format": {"type": "json_schema", "json_schema": {
                                 "name": "clasificacion_llamada", "strict": True, "schema": RESULT_SCHEMA}}})
    try:
        return json.loads(payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise AnalysisError("OpenAI no devolvió un análisis estructurado válido.") from exc


def contextual_analysis(transcript, keywords: list[str], provider: str, key: str, model: str):
    text = transcript.get("text", "").strip()
    word_count = len(re.findall(r"\w+", text, re.UNICODE))
    detected = detected_keyword_hits(transcript, keywords)
    likely_mailbox = word_count < 5 or transcript.get("speaker_count") == 1
    mailbox_result = {"category": "BUZON", "summary": "No se detectó una conversación entre cliente y asesor.",
                      "sentiment": "NEUTRAL", "risk": "BAJO", "validated_keywords": [], "hits": detected}
    candidates = [hit["keyword"] for hit in detected]
    # Sin términos candidatos no se consume una segunda API.
    if not candidates:
        if likely_mailbox:
            return mailbox_result
        return {"category": "NORMAL", "summary": "Llamada sin términos sensibles detectados.",
                "sentiment": "NEUTRAL", "risk": "BAJO", "validated_keywords": [], "hits": detected}
    excerpts = []
    normalized_text = _normalized(text)
    for keyword in candidates:
        position = max(0, normalized_text.find(_normalized(keyword)))
        start, end = max(0, position - 600), min(len(text), position + len(keyword) + 600)
        excerpts.append(text[start:end])
    excerpt = "\n[… otro fragmento …]\n".join(excerpts)[:8000]
    if provider == "gemini":
        result = _gemini_analysis(excerpt, candidates, key, model)
    elif provider == "openai":
        result = _openai_analysis(excerpt, candidates, key, model)
    else:
        raise AnalysisError(f"Proveedor de análisis no compatible: {provider}")
    if result.get("category") not in {"ALERTA", "NORMAL"}:
        raise AnalysisError("La API devolvió una categoría inválida.")
    # Un solo hablante o una transcripción breve no descartan una amenaza real.
    # Si el contexto no confirma riesgo, conserva la clasificación de buzón.
    if likely_mailbox and result["category"] == "NORMAL":
        return mailbox_result
    validated = {str(word).casefold() for word in result.get("validated_keywords", [])}
    accepted = validated or ({word.casefold() for word in candidates} if result["category"] == "ALERTA" else set())
    accepted_normalized = {_normalized(word) for word in accepted}
    result["hits"] = [
        {**hit, "validated": _normalized(hit["keyword"]) in accepted_normalized}
        for hit in detected
    ]
    return result


def analyze_file(database, path: Path, config: dict, digest: str | None = None):
    started = time.perf_counter()
    path = Path(path).resolve(strict=True)
    hash_started = time.perf_counter()
    digest = digest or cached_file_hash(database, path)
    hash_ms = (time.perf_counter() - hash_started) * 1000
    keywords = [word.strip() for word in config["keywords"] if word.strip()]
    terms_signature = keyword_signature(keywords)
    transcript_key = hashlib.sha256(
        f"{TRANSCRIPTION_VERSION}|{digest}|{config['transcription_provider']}|{config['transcription_model']}".encode()).hexdigest()
    analysis_key = hashlib.sha256(
        f"{ANALYSIS_VERSION}|{transcript_key}|{config['analysis_provider']}|{config['analysis_model']}|"
        f"{terms_signature}".encode()).hexdigest()
    database.set_call_status(path, "TRANSFIRIENDO")
    transcription_started = time.perf_counter()
    transcript, transcription_cached = _cached_computation(
        database, "transcription_cache", transcript_key,
        lambda: transcribe(path, config["transcription_provider"], config["transcription_key"],
                           config["transcription_model"], keywords),
    )
    transcription_ms = (time.perf_counter() - transcription_started) * 1000
    transcript = assign_roles(transcript)
    database.set_call_status(path, "ANALIZANDO")
    contextual_started = time.perf_counter()
    result, analysis_cached = _cached_computation(
        database, "analysis_cache", analysis_key,
        lambda: contextual_analysis(transcript, keywords, config["analysis_provider"],
                                    config["analysis_key"], config["analysis_model"]),
    )
    contextual_ms = (time.perf_counter() - contextual_started) * 1000
    persistence_started = time.perf_counter()
    database.save_call_result(path, analysis_key, terms_signature, transcript, result)
    persistence_ms = (time.perf_counter() - persistence_started) * 1000
    database.save_analysis_timing(path, {
        "hash_ms": hash_ms,
        "transcription_ms": transcription_ms,
        "contextual_ms": contextual_ms,
        "persistence_ms": persistence_ms,
        "total_ms": (time.perf_counter() - started) * 1000,
        "transcription_cached": transcription_cached,
        "analysis_cached": analysis_cached,
    })
    return database.call_rows([path])[0]


def analyze_files(database, paths, config, progress=None, stop_requested=None):
    results, failures = [], []
    for index, path in enumerate(paths, 1):
        if stop_requested and stop_requested():
            break
        if progress:
            progress(index, len(paths), Path(path).name)
        try:
            results.append(analyze_file(database, Path(path), config))
        except Exception as exc:
            database.set_call_status(path, "ERROR", str(exc))
            failures.append((Path(path), str(exc)))
    return results, failures
