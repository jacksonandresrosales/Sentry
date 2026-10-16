"""Estimaciones locales del consumo de las APIs de análisis."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


# Modelos que el usuario puede elegir por proveedor de transcripción (identificador, etiqueta).
TRANSCRIPTION_MODEL_CHOICES = {
    "deepgram": (("nova-3", "Nova-3"),),
    "assemblyai": (("universal-2", "Universal-2"),
                   ("universal-3-5-pro", "Universal-3.5 Pro")),
}

RECOMMENDED_API_MODELS = {
    "transcription": {"deepgram": "nova-3", "assemblyai": "universal-3-5-pro"},
    "analysis": {"gemini": "gemini-3.5-flash", "deepseek": "deepseek-flash",
                 "groq": "openai/gpt-oss-120b"},
}


# USD, modalidad estándar de pago por uso. Las tarifas se aplican automáticamente
# a solicitudes nuevas y las tarifas de cada solicitud quedan guardadas en la base.
# Fuentes: deepgram.com/pricing, assemblyai.com/pricing, ai.google.dev/gemini-api/docs/pricing,
# api-docs.deepseek.com/quick_start/pricing, console.groq.com/docs/models
# y developers.openai.com/api/docs/pricing (23-09-2026).
# La tarifa por minuto de transcribe-diarize aproxima el costo de gpt-4o-transcribe;
# el modelo de diarización publica precios por tokens y el formato diarized_json
# puede no devolver los contadores necesarios para calcularlos con exactitud.
# AssemblyAI (16-10-2026): incluye diarización estándar (+0,02 USD/h).
# Keyterms suma 0,05 USD/h solo en Universal-3.5 Pro; Universal-2 lo incluye.
DEFAULT_PRICES = {
    ("transcription", "deepgram", "nova-3"): ("minute", 0.0043, 0.0, 0.0),
    ("transcription", "assemblyai", "universal-2"): ("minute", 0.17 / 60, 0.0, 0.0),
    ("transcription", "assemblyai", "universal-3-5-pro"): ("minute", 0.23 / 60, 0.0, 0.0),
    ("transcription", "openai", "gpt-4o-transcribe-diarize"): ("minute", 0.006, 0.0, 0.0),
    ("transcription", "openai", "gpt-4o-transcribe"): ("minute", 0.006, 0.0, 0.0),
    ("transcription", "openai", "gpt-4o-mini-transcribe"): ("minute", 0.003, 0.0, 0.0),
    ("analysis", "gemini", "gemini-3.5-flash"): ("million_tokens", 1.50, 9.0, 0.15),
    ("analysis", "deepseek", "deepseek-flash"): ("million_tokens", 0.30, 1.20, 0.006),
    ("analysis", "groq", "openai/gpt-oss-120b"): ("million_tokens", 0.15, 0.60, 0.075),
    ("analysis", "openai", "gpt-4o-mini"): ("million_tokens", 0.15, 0.60, 0.075),
}
DEEPGRAM_KEYTERM_RATE = 0.0013  # USD/min, audio pregrabado con Keyterm Prompting.
ASSEMBLYAI_KEYTERM_RATE = 0.05 / 60  # USD/min, Keyterms Prompting en audio pregrabado.


def price_for_request(service: str, provider: str, model: str, payload: dict,
                      *, keyterm: bool = False):
    """Devuelve la tarifa publicada aplicable a esta solicitud, si existe."""
    price = DEFAULT_PRICES.get((service, provider, model))
    if price is None:
        return None
    unit, input_rate, output_rate, cached_input_rate = price
    if (service, provider, model) == ('transcription', 'deepgram', 'nova-3') and keyterm:
        input_rate += DEEPGRAM_KEYTERM_RATE
    elif (service, provider, model) == ('transcription', 'assemblyai', 'universal-3-5-pro') and keyterm:
        input_rate += ASSEMBLYAI_KEYTERM_RATE
    if (service, provider, model) == ('analysis', 'deepseek', 'deepseek-flash'):
        try:
            created = datetime.fromtimestamp(int(payload['created']), timezone.utc)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            created = datetime.now(timezone.utc)
        peak = created.weekday() < 5 and (1 <= created.hour < 4 or 6 <= created.hour < 10)
        if not peak:
            input_rate /= 2
            output_rate /= 2
            cached_input_rate /= 2
    return unit, input_rate, output_rate, cached_input_rate


def usage_from_response(service: str, provider: str, payload: dict, fallback_seconds: float | None):
    """Extrae solo contadores de facturación; nunca texto ni credenciales."""
    if service == "transcription":
        metadata = payload.get("metadata") or {}
        usage = payload.get("usage") or {}
        if provider == "deepgram":
            seconds = metadata.get("duration")
        elif provider == "assemblyai":
            seconds = payload.get("audio_duration")
        else:
            seconds = usage.get("seconds") or payload.get("duration")
        if seconds is None:
            seconds = fallback_seconds
        return (_number(seconds), _integer(usage.get("input_tokens")),
                _integer(usage.get("output_tokens")), 0)
    usage = payload.get("usageMetadata") if provider == "gemini" else payload.get("usage")
    usage = usage or {}
    input_tokens = usage.get("promptTokenCount") if provider == "gemini" else usage.get("prompt_tokens")
    output_tokens = usage.get("candidatesTokenCount") if provider == "gemini" else usage.get("completion_tokens")
    if provider == "gemini":
        cached_input = usage.get("cachedContentTokenCount", 0)
    else:
        details = usage.get("prompt_tokens_details") or {}
        cached_input = (usage.get("prompt_cache_hit_tokens") or details.get("cached_tokens", 0))
    if provider == "gemini" and output_tokens is not None:
        # Los tokens de pensamiento están incluidos en la facturación de salida.
        output_tokens = int(output_tokens) + int(usage.get("thoughtsTokenCount") or 0)
    return None, _integer(input_tokens), _integer(output_tokens), _integer(cached_input)


def _number(value):
    try:
        result = float(value)
        return result if 0 <= result < 1e12 else None
    except (TypeError, ValueError):
        return None


def _integer(value):
    number = _number(value)
    return int(number) if number is not None else None


def estimated_cost(unit: str, input_rate: float, output_rate: float,
                   seconds: float | None, input_tokens: int | None, output_tokens: int | None,
                   cached_input_rate: float = 0.0, cached_input_tokens: int = 0):
    if unit == "minute":
        return (seconds / 60 * input_rate) if seconds is not None else None
    if unit == "million_tokens" and input_tokens is not None and output_tokens is not None:
        cached = min(input_tokens, max(0, cached_input_tokens))
        return ((input_tokens-cached) * input_rate + cached * cached_input_rate +
                output_tokens * output_rate) / 1_000_000
    return None


MODEL_LABELS = {
    ("deepgram", "nova-3"): "Deepgram Nova-3",
    ("assemblyai", "universal-2"): "AssemblyAI Universal-2",
    ("assemblyai", "universal-3-5-pro"): "AssemblyAI Universal-3.5 Pro",
    ("openai", "gpt-4o-transcribe-diarize"): "OpenAI GPT-4o Transcribe Diarize",
    ("openai", "gpt-4o-transcribe"): "OpenAI GPT-4o Transcribe",
    ("openai", "gpt-4o-mini-transcribe"): "OpenAI GPT-4o mini Transcribe",
    ("gemini", "gemini-3.5-flash"): "Gemini 3.5 Flash",
    ("deepseek", "deepseek-flash"): "DeepSeek V4.1 Flash",
    ("groq", "openai/gpt-oss-120b"): "Groq GPT-OSS 120B",
    ("openai", "gpt-4o-mini"): "OpenAI GPT-4o mini",
}
PROVIDER_LABELS = {"deepgram": "Deepgram", "assemblyai": "AssemblyAI", "gemini": "Gemini",
                   "deepseek": "DeepSeek", "groq": "Groq", "openai": "OpenAI"}
SERVICE_LABELS = {"transcription": "Transcripción", "analysis": "Análisis"}


def model_label(provider: str, model: str) -> str:
    """Nombre legible del modelo; los modelos antiguos o personalizados conservan su id."""
    label = MODEL_LABELS.get((provider, model))
    if label:
        return label
    return f"{PROVIDER_LABELS.get(provider, provider.title())} · {model}"


def published_rate_label(service: str, provider: str, model: str) -> str:
    """Tarifa base publicada en texto corto, o cadena vacía si Sentry no la conoce."""
    price = DEFAULT_PRICES.get((service, provider, model))
    if price is None:
        return ""
    unit, input_rate, output_rate, _cached = price
    if unit == "minute":
        extra = (DEEPGRAM_KEYTERM_RATE if provider == "deepgram" else
                 ASSEMBLYAI_KEYTERM_RATE if provider == "assemblyai" and model == "universal-3-5-pro" else 0)
        text = f"${input_rate * 60:.2f}/h"
        return text + (f" (+${extra * 60:.2f}/h keyterms)" if extra else "")
    if provider == "deepseek":
        return f"${input_rate:.2f} / ${output_rate:.2f} por 1M tokens (pico)"
    return f"${input_rate:.2f} / ${output_rate:.2f} por 1M tokens"


def _utc_bound(day: date) -> str:
    """Inicio del día local expresado como marca UTC comparable con created_at."""
    local = datetime(day.year, day.month, day.day).astimezone()
    return local.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def _empty_usage(**extra):
    return {"requests": 0, "cost_usd": 0.0, "unpriced": 0, "audio_seconds": 0.0, "priced_audio_seconds": 0.0,
            "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0, **extra}


def _add_usage(target, row):
    target['requests'] += row['requests']
    target['cost_usd'] += row['cost_usd']
    target['unpriced'] += row['unpriced']
    for key in ('audio_seconds', 'priced_audio_seconds', 'input_tokens', 'output_tokens', 'cached_input_tokens'):
        target[key] += row[key] or 0


def load_cost_report(database, start: date, end: date, service: str = '', provider: str = '',
                     *, event_limit: int | None = None):
    """Resume el consumo del período; los totales se agregan en SQLite sin cargar cada fila.

    `event_limit` acota solo la lista `events` (la más reciente primero); los totales,
    el diario y los desgloses siempre cubren todas las solicitudes del filtro.
    """
    bounds = (_utc_bound(start), _utc_bound(end), service, service, provider, provider)
    where = ("u.created_at>=? AND u.created_at<? AND (?='' OR u.service=?) "
             "AND (?='' OR u.provider=?)")
    with database.connect() as connection:
        grouped_rows = [dict(row) for row in connection.execute(f"""
            SELECT date(u.created_at,'localtime') AS day, u.service, u.provider, u.model,
                   COUNT(*) AS requests, TOTAL(u.cost_usd) AS cost_usd,
                   SUM(u.cost_usd IS NULL) AS unpriced, TOTAL(u.audio_seconds) AS audio_seconds,
                   TOTAL(CASE WHEN u.cost_usd IS NOT NULL THEN u.audio_seconds END) AS priced_audio_seconds,
                   TOTAL(u.input_tokens) AS input_tokens, TOTAL(u.output_tokens) AS output_tokens,
                   TOTAL(u.cached_input_tokens) AS cached_input_tokens
            FROM api_usage u WHERE {where}
            GROUP BY day, u.service, u.provider, u.model
        """, bounds)]
        call_rows = [dict(row) for row in connection.execute(f"""
            SELECT COALESCE(c.filename, 'Llamada eliminada') AS filename,
                   COUNT(*) AS requests, TOTAL(u.cost_usd) AS cost_usd,
                   SUM(u.cost_usd IS NULL) AS unpriced,
                   TOTAL(CASE WHEN u.service='transcription' THEN u.cost_usd END) AS transcription_usd,
                   TOTAL(CASE WHEN u.service='analysis' THEN u.cost_usd END) AS analysis_usd,
                   TOTAL(u.audio_seconds) AS audio_seconds, MAX(u.created_at) AS last_at
            FROM api_usage u LEFT JOIN calls c ON c.id=u.call_id
            WHERE {where}
            GROUP BY CASE WHEN u.call_id IS NULL THEN -u.id ELSE u.call_id END
            ORDER BY cost_usd DESC, last_at DESC
        """, bounds)]
        limit = -1 if event_limit is None else max(0, int(event_limit))
        events = [dict(row) for row in connection.execute(f"""
            SELECT u.id, u.call_id, u.created_at, u.service, u.provider, u.model, u.request_id,
                   u.audio_seconds, u.input_tokens, u.output_tokens, u.cached_input_tokens,
                   u.cost_usd, u.price_unit, u.input_rate, u.cached_input_rate, u.output_rate,
                   c.filename, datetime(u.created_at,'localtime') AS local_at
            FROM api_usage u LEFT JOIN calls c ON c.id=u.call_id
            WHERE {where}
            ORDER BY u.created_at DESC, u.id DESC LIMIT ?
        """, (*bounds, limit))]
    daily = []
    daily_by_date = {}
    day = start
    while day < end:
        row = {"date": day.isoformat(), "label": day.strftime('%d/%m'),
               "transcription_usd": 0.0, "analysis_usd": 0.0, "total_usd": 0.0,
               "transcription_requests": 0, "analysis_requests": 0,
               "unpriced": 0, "models": {}}
        daily.append(row)
        daily_by_date[row['date']] = row
        day += timedelta(days=1)
    by_model = {}
    totals = _empty_usage()
    service_usd = {"transcription": 0.0, "analysis": 0.0}
    for item in grouped_rows:
        key = (item['service'], item['provider'], item['model'])
        daily_row = daily_by_date.get(item['day'])
        if daily_row is not None:
            daily_row[f"{item['service']}_requests"] = daily_row.get(f"{item['service']}_requests", 0) + item['requests']
            daily_row[f"{item['service']}_usd"] = daily_row.get(f"{item['service']}_usd", 0.0) + item['cost_usd']
            daily_row['total_usd'] += item['cost_usd']
            daily_row['unpriced'] += item['unpriced']
            model_row = daily_row['models'].setdefault(key, _empty_usage(
                service=key[0], provider=key[1], model=key[2]))
            _add_usage(model_row, item)
        entry = by_model.setdefault(key, _empty_usage(service=key[0], provider=key[1], model=key[2],
                                                      label=model_label(key[1], key[2])))
        _add_usage(entry, item)
        _add_usage(totals, item)
        service_usd[item['service']] = service_usd.get(item['service'], 0.0) + item['cost_usd']
    for row in daily:
        row['models'] = sorted(row['models'].values(),
                               key=lambda item: (-item['cost_usd'], item['service'], item['model']))
    models = sorted(by_model.values(), key=lambda item: (item['service'] != 'transcription',
                                                         -item['cost_usd'], item['label']))
    for item in models:
        item['share'] = item['cost_usd'] / totals['cost_usd'] if totals['cost_usd'] else 0.0
        hours = item['priced_audio_seconds'] / 3600
        priced = item['requests'] - item['unpriced']
        item['usd_per_hour'] = (item['cost_usd'] / hours if item['service'] == 'transcription'
                                and hours and priced else None)
        item['usd_per_request'] = item['cost_usd'] / priced if priced else None
        item['rate_label'] = published_rate_label(*[item[k] for k in ('service', 'provider', 'model')])
    calls_count = len(call_rows)
    return {"events": events, "daily": daily, "by_model": models,
            "by_call": call_rows, "calls": calls_count,
            "requests": totals['requests'], "unpriced": totals['unpriced'],
            "total_usd": totals['cost_usd'],
            "transcription_usd": service_usd.get('transcription', 0.0),
            "analysis_usd": service_usd.get('analysis', 0.0),
            "audio_seconds": sum(item['audio_seconds'] for item in models
                                 if item['service'] == 'transcription'),
            "input_tokens": totals['input_tokens'], "output_tokens": totals['output_tokens'],
            "cached_input_tokens": totals['cached_input_tokens']}


def bucket_daily(daily: list[dict], granularity: str = 'auto') -> tuple[str, list[dict]]:
    """Agrupa el diario por semana o mes para que los períodos largos sigan siendo legibles."""
    if granularity == 'auto':
        granularity = 'day' if len(daily) <= 62 else 'week' if len(daily) <= 370 else 'month'
    if granularity == 'day':
        return 'day', daily
    buckets = {}
    for row in daily:
        day = date.fromisoformat(row['date'])
        first = (day - timedelta(days=day.weekday())) if granularity == 'week' else day.replace(day=1)
        bucket = buckets.get(first)
        if bucket is None:
            bucket = buckets[first] = {
                "date": first.isoformat(), "end": row['date'],
                "label": first.strftime('%d/%m') if granularity == 'week' else first.strftime('%m/%Y'),
                "transcription_usd": 0.0, "analysis_usd": 0.0, "total_usd": 0.0,
                "transcription_requests": 0, "analysis_requests": 0, "unpriced": 0, "models": {}}
        bucket['end'] = row['date']
        for key in ('transcription_usd', 'analysis_usd', 'total_usd', 'transcription_requests',
                    'analysis_requests', 'unpriced'):
            bucket[key] += row[key]
        for model in row['models']:
            key = (model['service'], model['provider'], model['model'])
            target = bucket['models'].setdefault(key, _empty_usage(
                service=key[0], provider=key[1], model=key[2]))
            _add_usage(target, model)
    rows = list(buckets.values())
    for row in rows:
        row['models'] = sorted(row['models'].values(),
                               key=lambda item: (-item['cost_usd'], item['service'], item['model']))
    return granularity, rows
