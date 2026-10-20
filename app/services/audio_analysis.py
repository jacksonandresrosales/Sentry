"""Transcripción, clasificación y caché persistente de grabaciones."""
from __future__ import annotations

import hashlib
import io
import json
import mimetypes
from contextvars import ContextVar
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

from app.services.audio_trim import trim_silence


TRANSCRIPTION_VERSION = "2"
ANALYSIS_VERSION = "4"
_HTTP = threading.local()
_IN_FLIGHT: dict[tuple[str, str, str], Future] = {}
_IN_FLIGHT_LOCK = threading.Lock()
_PROVIDER_COOLDOWNS: dict[str, float] = {}
_PROVIDER_LOCK = threading.Lock()
_COST_CONTEXT: ContextVar[dict | None] = ContextVar('sentry_cost_context', default=None)


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


def _record_usage(service: str, provider: str, payload: dict, *, keyterm: bool = False):
    context = _COST_CONTEXT.get()
    if context is not None and isinstance(payload, dict):
        model = payload.get('speech_model_used') if provider == 'assemblyai' else None
        context['database'].record_api_usage(
            context['path'], service, provider, model or context['config'][f'{service}_model'],
            payload, context['duration_seconds'], keyterm=keyterm,
        )


def _request(method, url, *, retries=3, billable=False, **kwargs):
    """`billable`: el proveedor cobra al recibir el audio; tras un corte ambiguo no se reenvía."""
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
            # Si el audio pudo llegar al proveedor, reenviarlo podría cobrarse dos veces.
            if billable and not isinstance(exc, requests.ConnectTimeout):
                raise AnalysisError("Se cortó la conexión después de enviar el audio; no se reenvía para "
                                    f"evitar un cobro doble. Vuelve a analizar más tarde: {exc}") from exc
            if attempt + 1 == retries:
                raise AnalysisError(f"No se pudo conectar con la API: {exc}") from exc
        else:
            if response.status_code < 400:
                if method == 'DELETE' and response.status_code == 204:
                    return {}
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise AnalysisError("La API devolvió una respuesta que no es JSON.") from exc
                if not isinstance(payload, dict):
                    raise AnalysisError("La API devolvió un formato de respuesta inesperado.")
                context = _COST_CONTEXT.get()
                if context is not None and isinstance(payload, dict):
                    if 'api.deepgram.com/v1/listen' in url:
                        service, provider = 'transcription', 'deepgram'
                    elif 'api.openai.com/v1/audio/transcriptions' in url:
                        service, provider = 'transcription', 'openai'
                    elif 'generativelanguage.googleapis.com/' in url:
                        service, provider = 'analysis', 'gemini'
                    elif 'api.openai.com/v1/chat/completions' in url:
                        service, provider = 'analysis', 'openai'
                    elif 'api.deepseek.com/chat/completions' in url:
                        service, provider = 'analysis', 'deepseek'
                    elif 'api.groq.com/openai/v1/chat/completions' in url:
                        service, provider = 'analysis', 'groq'
                    else:
                        return payload
                    _record_usage(service, provider, payload, keyterm=(provider == 'deepgram' and any(
                        item[0] == 'keyterm' for item in (kwargs.get('params') or ()))))
                return payload
            message = ""
            try:
                body = response.json()
                if isinstance(body, dict):
                    message = body.get("error", {}).get("message", "") if isinstance(body.get("error"), dict) else str(body.get("error", ""))
            except ValueError:
                pass
            retry_after = _retry_after_seconds(response)
            if response.status_code == 429:
                _defer_provider(url, retry_after if retry_after is not None else 2 ** attempt)
            if billable and response.status_code in {500, 502, 503, 504}:
                raise AnalysisError(f"La API respondió {response.status_code}; no se reenvía el audio "
                                    "para evitar un cobro doble.")
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


def _audio_stream(path: Path, audio: bytes | None):
    """El audio recortado viaja desde memoria; si no hubo recorte se lee el archivo original."""
    return io.BytesIO(audio) if audio is not None else path.open("rb")


def _deepgram_transcript(path: Path, key: str, model: str, keywords: list[str], audio: bytes | None = None):
    params = [("model", model), ("language", "es-419"), ("smart_format", "true"),
              ("utterances", "true"), ("diarize_model", "latest"), ("mip_opt_out", "true")]
    params.extend(("keyterm", word) for word in keywords)
    with _audio_stream(path, audio) as stream:
        payload = _request("POST", "https://api.deepgram.com/v1/listen", params=params, billable=True,
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


ASSEMBLYAI_API = "https://api.assemblyai.com/v2"
ASSEMBLYAI_KEYTERM_LIMITS = {"universal-2": 200, "universal-3-5-pro": 1000}
ASSEMBLYAI_POLL_SECONDS = 3.0
ASSEMBLYAI_MAX_WAIT_SECONDS = 30 * 60


def _assemblyai_keyterms(model: str, keywords: list[str]) -> list[str]:
    """Respeta los límites publicados: hasta 6 palabras por frase y 200/1000 frases por modelo."""
    terms = [" ".join(str(word).split()) for word in keywords]
    terms = list(dict.fromkeys(term for term in terms if term and len(term.split()) <= 6))
    return terms[:ASSEMBLYAI_KEYTERM_LIMITS.get(model, 200)]


def _assemblyai_transcript(path: Path, key: str, model: str, keywords: list[str], audio: bytes | None = None):
    headers = {"authorization": key}
    with _audio_stream(path, audio) as stream:
        upload = _request("POST", f"{ASSEMBLYAI_API}/upload",
                          headers={**headers, "Content-Type": "application/octet-stream"}, data=stream)
    audio_url = upload.get("upload_url")
    if not audio_url:
        raise AnalysisError("AssemblyAI no devolvió la dirección del audio subido.")
    body = {"audio_url": audio_url, "speech_models": [model], "language_code": "es",
            "speaker_labels": True, "punctuate": True, "format_text": True}
    # Universal-2 solo admite keyterms en inglés; Sentry transcribe en español.
    keyterms = _assemblyai_keyterms(model, keywords) if model == "universal-3-5-pro" else []
    if keyterms:
        body["keyterms_prompt"] = keyterms
    job = _request("POST", f"{ASSEMBLYAI_API}/transcript", headers=headers, json=body, billable=True)
    transcript_id = job.get("id")
    if not transcript_id:
        raise AnalysisError("AssemblyAI no devolvió el identificador de la transcripción.")
    deadline = time.monotonic() + ASSEMBLYAI_MAX_WAIT_SECONDS
    payload = job
    while payload.get("status") not in {"completed", "error"}:
        if time.monotonic() > deadline:
            raise AnalysisError("AssemblyAI no terminó la transcripción en 30 minutos. Vuelve a analizar más tarde.")
        time.sleep(ASSEMBLYAI_POLL_SECONDS)
        payload = _request("GET", f"{ASSEMBLYAI_API}/transcript/{transcript_id}", headers=headers)
    if payload.get("status") == "error":
        raise AnalysisError(f"AssemblyAI no pudo transcribir el audio: {payload.get('error') or 'error desconocido'}")
    _record_usage("transcription", "assemblyai", payload, keyterm=bool(keyterms))
    utterances = payload.get("utterances") or []
    labels: dict[str, str] = {}
    segments = []
    for item in utterances:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        label = labels.setdefault(str(item.get("speaker", "")), f"Hablante {len(labels) + 1}")
        segments.append({"second": float(item.get("start") or 0) / 1000, "speaker": label, "text": text,
                         "word_seconds": [float(word.get("start") or 0) / 1000 for word in (item.get("words") or [])
                                          if isinstance(word, dict)]})
    text = str(payload.get("text") or "").strip()
    if not segments and text:
        segments = [{"second": 0.0, "speaker": "Hablante", "text": text}]
    speakers = len(labels) if utterances else None
    return {"text": text or " ".join(item["text"] for item in segments), "segments": segments,
            "speaker_count": speakers, "provider": "assemblyai",
            "model": str(payload.get("speech_model_used") or model), "request_id": str(transcript_id)}


def _openai_transcript(path: Path, key: str, model: str, audio: bytes | None = None):
    data = {"model": model, "language": "es", "response_format": "json"}
    if "diarize" in model:
        data["response_format"] = "diarized_json"
        data["chunking_strategy"] = "auto"
    with _audio_stream(path, audio) as stream:
        payload = _request("POST", "https://api.openai.com/v1/audio/transcriptions", billable=True,
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


def _shift_transcript(transcript: dict, offset: float) -> dict:
    """Devuelve las marcas de tiempo a la posición que tienen en el audio original."""
    if offset:
        transcript["segments"] = [
            {**segment, "second": round(float(segment.get("second", 0)) + offset, 3),
             **({"word_seconds": [round(float(value) + offset, 3) for value in segment["word_seconds"]]}
                if "word_seconds" in segment else {})}
            for segment in transcript.get("segments") or []
        ]
    return transcript


def _provider_transcript(path: Path, provider: str, key: str, model: str, keywords: list[str],
                         audio: bytes | None = None):
    if provider == "deepgram":
        return _deepgram_transcript(path, key, model, keywords, audio)
    if provider == "assemblyai":
        return _assemblyai_transcript(path, key, model, keywords, audio)
    if provider == "openai":
        return _openai_transcript(path, key, model, audio)
    raise AnalysisError(f"Proveedor de transcripción no compatible: {provider}")


def transcribe(path: Path, provider: str, key: str, model: str, keywords: list[str]):
    """Omite silencio digital inicial/final sin descartar señales de volumen bajo.
    Las marcas de tiempo se corrigen para coincidir con el archivo original."""
    if provider not in {"deepgram", "assemblyai", "openai"}:
        raise AnalysisError(f"Proveedor de transcripción no compatible: {provider}")
    trimmed = trim_silence(path)
    if trimmed is not None and not trimmed.has_speech:
        return {"text": "", "segments": [], "speaker_count": None, "provider": provider, "model": model,
                "skipped_reason": "sin_voz"}
    if trimmed is None or trimmed.data is None:
        return _provider_transcript(path, provider, key, model, keywords)
    context = _COST_CONTEXT.get()
    token = _COST_CONTEXT.set({**context, 'duration_seconds': trimmed.kept_seconds}) if context is not None else None
    try:
        transcript = _provider_transcript(path, provider, key, model, keywords, trimmed.data)
    finally:
        if token is not None:
            _COST_CONTEXT.reset(token)
    transcript["trimmed_seconds"] = round(trimmed.original_seconds - trimmed.kept_seconds, 3)
    return _shift_transcript(transcript, trimmed.offset_seconds)


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


def _deepseek_analysis(text: str, keywords: list[str], key: str, model: str):
    prompt = (_prompt(text, keywords) + "\nDevuelve un objeto JSON válido con las claves category, summary, "
             "sentiment, risk y validated_keywords. Ejemplo: "
             '{"category":"NORMAL","summary":"Resumen breve","sentiment":"NEUTRAL",'
             '"risk":"BAJO","validated_keywords":[]}')
    payload = _request("POST", "https://api.deepseek.com/chat/completions",
                       headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                       json={"model": model, "messages": [{"role": "user", "content": prompt}],
                             "max_tokens": 320, "thinking": {"type": "disabled"},
                             "response_format": {"type": "json_object"}})
    try:
        content = payload["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise AnalysisError("DeepSeek no devolvió un análisis estructurado válido.") from exc


def _groq_analysis(text: str, keywords: list[str], key: str, model: str):
    payload = _request("POST", "https://api.groq.com/openai/v1/chat/completions",
                       headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                       json={"model": model, "messages": [{"role": "user", "content": _prompt(text, keywords)}],
                             "reasoning_effort": "low", "include_reasoning": False,
                             "max_completion_tokens": 768,
                             "response_format": {"type": "json_schema", "json_schema": {
                                 "name": "clasificacion_llamada", "strict": True, "schema": RESULT_SCHEMA}}})
    try:
        return json.loads(payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise AnalysisError("Groq no devolvió un análisis estructurado válido.") from exc


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
    elif provider == "deepseek":
        result = _deepseek_analysis(excerpt, candidates, key, model)
    elif provider == "groq":
        result = _groq_analysis(excerpt, candidates, key, model)
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
    path = Path(path).resolve(strict=True)
    with database.connect() as connection:
        row = connection.execute('SELECT duration_seconds FROM calls WHERE file_path=?', (str(path),)).fetchone()
    token = _COST_CONTEXT.set({'database': database, 'path': path, 'config': config,
                               'duration_seconds': row[0] if row else None})
    try:
        return _analyze_file(database, path, config, digest)
    finally:
        _COST_CONTEXT.reset(token)


def _analyze_file(database, path: Path, config: dict, digest: str | None = None):
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
    if (not transcription_cached and transcript.get("provider") == "assemblyai"
            and transcript.get("request_id")):
        try:
            # El resultado ya está guardado: un fallo de disco no elimina la única copia remota.
            _request("DELETE", f"{ASSEMBLYAI_API}/transcript/{quote(transcript['request_id'], safe='')}",
                     headers={"authorization": config["transcription_key"]}, retries=1)
        except AnalysisError:
            pass
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
