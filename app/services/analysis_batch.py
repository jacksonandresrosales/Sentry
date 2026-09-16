"""Análisis acotado y progresivo: inicia sin calcular antes el hash del lote completo."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import NamedTuple

from app.services.audio_analysis import analyze_file


DEFAULT_ANALYSIS_CONCURRENCY = 6
MAX_ANALYSIS_CONCURRENCY = 8


class BatchResult(NamedTuple):
    completed: int
    failures: int
    stopped: bool


def analysis_concurrency(value=DEFAULT_ANALYSIS_CONCURRENCY) -> int:
    """Limita solicitudes simultáneas; 1 permite cuentas con cuotas reducidas."""
    try:
        return min(MAX_ANALYSIS_CONCURRENCY, max(1, int(value)))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_ANALYSIS_CONCURRENCY


def run_analysis_batch(database, paths, config, *, max_workers=DEFAULT_ANALYSIS_CONCURRENCY,
                       progress=None, row_ready=None, stop_requested=None) -> BatchResult:
    """Los callbacks se ejecutan en el coordinador, nunca en los hilos de la API.

    Solo hay `max_workers` trabajos pendientes, incluidos lectura/hash y peticiones.
    Detener no inicia nuevos audios; los ya enviados terminan y se guardan.
    `analyze_file` comparte las transcripciones/análisis de contenido idéntico.
    """
    paths = list(dict.fromkeys(Path(path) for path in paths))
    total = len(paths)
    completed = failures = processed = 0
    max_workers = analysis_concurrency(max_workers)
    work = iter(paths)
    stopped = False

    def should_stop():
        nonlocal stopped
        stopped = stopped or bool(stop_requested and stop_requested())
        return stopped

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="sentry-analysis") as pool:
        pending = {}

        def fill_workers():
            while not should_stop() and len(pending) < max_workers:
                try:
                    path = next(work)
                except StopIteration:
                    break
                pending[pool.submit(analyze_file, database, path, config)] = path

        fill_workers()
        while pending:
            done, _remaining = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            should_stop()
            for future in done:
                path = pending.pop(future)
                try:
                    row = future.result()
                    if config.get("base_path"):
                        database.record_analysis_base(row["id"], config["base_path"])
                except Exception as exc:
                    try:
                        database.set_call_status(path, "ERROR", str(exc))
                    except Exception:
                        pass
                    failures += 1
                else:
                    completed += 1
                    if row_ready:
                        row_ready(row)
                processed += 1
                if progress:
                    progress(processed, total, path.name)
            fill_workers()
    return BatchResult(completed, failures, should_stop())
