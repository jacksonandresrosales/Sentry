"""Analítica global de los últimos resultados guardados, sin cargar transcripciones."""
from collections import Counter
from datetime import date, timedelta
import calendar


def period_bounds(day: date, period: str):
    if period == 'week':
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=7)
    if period == 'month':
        start = day.replace(day=1)
        return start, start + timedelta(days=calendar.monthrange(day.year, day.month)[1])
    return day, day + timedelta(days=1)


def load_report(database, start, end):
    # SQLite stores UTC; the reporting calendar uses the operator's local time.
    args = (start.isoformat(), end.isoformat())
    with database.connect() as db:
        calls = [dict(r) for r in db.execute("""
            SELECT id, filename, status,
                   CASE WHEN reviewed <> 0 THEN 'ALERTA' ELSE category END AS category,
                   category AS automatic_category, reviewed, duration_seconds,
                   datetime(COALESCE(processed_at,created_at),'localtime') AS occurred_at
            FROM calls WHERE date(COALESCE(processed_at,created_at),'localtime') >= ?
            AND date(COALESCE(processed_at,created_at),'localtime') < ?
            ORDER BY occurred_at DESC, id DESC
        """, args)]
        hits = [dict(r) for r in db.execute("""
            SELECT h.call_id,h.keyword,h.is_risk_validated FROM keyword_hits h
            JOIN calls c ON c.id=h.call_id
            WHERE date(COALESCE(c.processed_at,c.created_at),'localtime') >= ?
            AND date(COALESCE(c.processed_at,c.created_at),'localtime') < ?
            AND (c.status='COMPLETADO' OR c.reviewed<>0)
        """, args)]
        bases = [dict(r) for r in db.execute("""
            SELECT b.base_path, COUNT(*) AS calls,
                SUM(c.category='ALERTA' OR c.reviewed<>0) AS incidents,
                datetime(MAX(b.analyzed_at),'localtime') AS last_analysis
            FROM call_bases b JOIN calls c ON c.id=b.call_id
            WHERE date(b.analyzed_at,'localtime') >= ? AND date(b.analyzed_at,'localtime') < ?
            GROUP BY b.base_path ORDER BY last_analysis DESC
        """, args)]
        jobs = [dict(r) for r in db.execute("""
            SELECT id,output_path,status,unique_count,
                datetime(created_at,'localtime') AS created_at FROM base_jobs
            WHERE date(created_at,'localtime') >= ? AND date(created_at,'localtime') < ?
            ORDER BY created_at DESC
        """, args)]
    return summarize(calls, hits, bases, jobs, start, end)


def summarize(calls, hits, bases, jobs, start, end):
    completed = [c for c in calls if c['status'] == 'COMPLETADO']
    # Una denuncia confirmada no desaparece si un reanálisis posterior falla.
    # El estado técnico y la decisión humana se contabilizan por separado.
    evaluated = [c for c in calls if c['status'] == 'COMPLETADO' or c['reviewed']]
    incidents = [c for c in evaluated if c['category'] == 'ALERTA']
    keywords = Counter(h['keyword'].strip().casefold() for h in hits if h['keyword'].strip())
    keyword_calls = {}
    for hit in hits:
        keyword_calls.setdefault(hit['keyword'].strip().casefold(), set()).add(hit['call_id'])
    terms_by_call = {}
    for hit in hits:
        terms_by_call.setdefault(hit["call_id"], set()).add(hit["keyword"])
    for call in calls:
        call["keywords"] = ", ".join(sorted(terms_by_call.get(call["id"], ())))
    daily = []
    day = start
    counts = Counter(c['occurred_at'][:10] for c in completed)
    alerts = Counter(c['occurred_at'][:10] for c in incidents)
    while day < end:
        daily.append((day.strftime('%d/%m'), counts[day.isoformat()], alerts[day.isoformat()]))
        day += timedelta(days=1)
    return dict(calls=calls, bases=bases, jobs=jobs, daily=daily,
                keywords=[(k,n,len(keyword_calls[k])) for k,n in keywords.most_common()],
                total=len(calls), completed=len(completed), evaluated=len(evaluated), incidents=len(incidents),
                verified=sum(bool(c['reviewed']) for c in incidents),
                normal=sum(c['category']=='NORMAL' for c in completed),
                mailbox=sum(c['category']=='BUZON' for c in completed),
                errors=sum(c['status']=='ERROR' for c in calls),
                pending=sum(c['status'] not in ('COMPLETADO','ERROR') for c in calls),
                seconds=sum(c['duration_seconds'] or 0 for c in completed))
