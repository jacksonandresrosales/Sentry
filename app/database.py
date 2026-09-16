"""Persistencia SQLite; las credenciales se guardan únicamente como blobs cifrados por DPAPI."""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone


_configured_storage = os.environ.get("SENTRY_STORAGE_ROOT", "").strip()
_local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
APP_STORAGE_ROOT = (
    Path(_configured_storage).expanduser().resolve()
    if _configured_storage
    else (Path(_local_app_data) / "Ecuaconexion" / "Sentry").resolve()
    if getattr(sys, "frozen", False) and _local_app_data
    else Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parents[1]
)
def _bundled_database() -> Path:
    """Copia la base inicial junto al EXE durante el primer arranque."""
    destination = APP_STORAGE_ROOT / "data" / "db" / "sentry_audit.db"
    if not getattr(sys, "frozen", False) or destination.exists():
        return destination

    bundle_root = Path(getattr(sys, "_MEIPASS", APP_STORAGE_ROOT))
    bundled_database = bundle_root / "data" / "db" / "sentry_audit.db"
    if bundled_database.is_file() and bundled_database.resolve() != destination.resolve():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled_database, destination)
    return destination


DEFAULT_DATABASE = _bundled_database()

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL UNIQUE,
    file_hash TEXT UNIQUE,
    duration_seconds INTEGER,
    status TEXT NOT NULL DEFAULT 'PENDIENTE'
        CHECK(status IN ('PENDIENTE','TRANSFIRIENDO','ANALIZANDO','COMPLETADO','ERROR')),
    transcript TEXT, summary TEXT, sentiment TEXT,
    risk_level TEXT CHECK(risk_level IN ('BAJO','MEDIO','ALTO','CRÍTICO')),
    has_sensitive_keyword INTEGER NOT NULL DEFAULT 0 CHECK(has_sensitive_keyword IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, processed_at TEXT
);
CREATE TABLE IF NOT EXISTS keyword_hits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    keyword TEXT NOT NULL, speaker TEXT,
    timestamp_seconds REAL NOT NULL CHECK(timestamp_seconds >= 0),
    context_snippet TEXT, is_risk_validated INTEGER CHECK(is_risk_validated IN (0,1))
);
CREATE INDEX IF NOT EXISTS idx_keyword_hits_call ON keyword_hits(call_id);
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS api_credentials (
    service TEXT PRIMARY KEY CHECK(service IN ('transcription','analysis')),
    provider TEXT NOT NULL, model TEXT NOT NULL, encrypted_key TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS remote_connection (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    host TEXT NOT NULL, port INTEGER NOT NULL DEFAULT 22,
    username TEXT NOT NULL, encrypted_password TEXT NOT NULL,
    remote_path TEXT NOT NULL DEFAULT '/', host_fingerprint TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS transcription_cache (
    cache_key TEXT PRIMARY KEY, transcript_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS analysis_cache (
    cache_key TEXT PRIMARY KEY, result_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS analysis_timings (
    call_id INTEGER PRIMARY KEY REFERENCES calls(id) ON DELETE CASCADE,
    hash_ms REAL NOT NULL, transcription_ms REAL NOT NULL,
    contextual_ms REAL NOT NULL, persistence_ms REAL NOT NULL, total_ms REAL NOT NULL,
    transcription_cached INTEGER NOT NULL CHECK(transcription_cached IN (0,1)),
    analysis_cached INTEGER NOT NULL CHECK(analysis_cached IN (0,1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS file_fingerprints (
    file_path TEXT PRIMARY KEY,
    file_size INTEGER NOT NULL,
    modified_ns INTEGER NOT NULL,
    digest TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS base_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    input_paths TEXT NOT NULL, output_path TEXT,
    status TEXT NOT NULL CHECK(status IN ('PROCESANDO','COMPLETADO','ERROR')),
    options_json TEXT NOT NULL,
    read_count INTEGER NOT NULL DEFAULT 0,
    filtered_count INTEGER NOT NULL DEFAULT 0,
    unique_count INTEGER NOT NULL DEFAULT 0,
    error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS base_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES base_jobs(id) ON DELETE CASCADE,
    row_number INTEGER NOT NULL,
    phone TEXT NOT NULL, call_state TEXT NOT NULL, agent TEXT NOT NULL,
    called_at TEXT NOT NULL, duration TEXT NOT NULL, name TEXT NOT NULL,
    identifier TEXT NOT NULL, identifier_type TEXT NOT NULL, state TEXT NOT NULL,
    base_type TEXT NOT NULL CHECK(base_type IN ('V','R')), base_number TEXT NOT NULL,
    UNIQUE(job_id, row_number)
);
CREATE INDEX IF NOT EXISTS idx_base_records_job ON base_records(job_id);
CREATE TABLE IF NOT EXISTS call_bases (
    call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    base_path TEXT NOT NULL,
    analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(call_id, base_path)
);
"""


class Database:
    def __init__(self, path: Path = DEFAULT_DATABASE):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 6:
                raise RuntimeError("La base de datos pertenece a una versión más nueva de Sentry.")
            if version < 6 and connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
            ).fetchone():
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                self.backup_to(self.path.parent / "backups" / f"before-schema-{version}-to-6-{stamp}.db")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            # Incluye DDL y versión en la misma transacción: una migración fallida
            # no deja la base marcada como actualizada ni parcialmente alterada.
            connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(calls)")}
            additions = {
                "category": "TEXT NOT NULL DEFAULT 'PENDIENTE'",
                "transcript_json": "TEXT",
                "analysis_error": "TEXT",
                "cache_key": "TEXT",
                "reviewed": "INTEGER NOT NULL DEFAULT 0",
                "analysis_terms": "TEXT",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE calls ADD COLUMN {name} {definition}")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_calls_status_category ON calls(status, category)")
            connection.execute("UPDATE calls SET status='ERROR', analysis_error="
                               "'El análisis fue interrumpido al cerrar la aplicación.' "
                               "WHERE status IN ('TRANSFIRIENDO','ANALIZANDO')")
            connection.execute("PRAGMA user_version=6")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def settings(self) -> dict[str, str]:
        with self.connect() as connection:
            return dict(connection.execute("SELECT key,value FROM app_settings").fetchall())

    def backup_to(self, destination: Path, *, cancel=None, timeout=60) -> Path:
        """Respaldo SQLite consistente (incluye WAL), publicado sin sobrescritura."""
        destination = Path(destination).resolve()
        if destination == self.path or destination.exists():
            raise ValueError("El respaldo debe usar un archivo nuevo distinto de la base activa.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".sentry-backup-", suffix=".db", dir=destination.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        started = time.monotonic()

        def progress(_status, _remaining, _total):
            if cancel and cancel():
                raise InterruptedError("Respaldo cancelado; la base original permanece intacta.")
            if time.monotonic() - started > timeout:
                raise TimeoutError("El respaldo tardó demasiado; no se instalará la actualización.")

        try:
            progress(0, 0, 0)
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(temporary)) as target:
                    source.backup(target, pages=256, progress=progress, sleep=.05)
                    if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("El respaldo no superó la verificación de integridad.")
            if os.name == "nt":
                temporary.rename(destination)
            else:
                os.link(temporary, destination)
            return destination
        finally:
            temporary.unlink(missing_ok=True)

    def save_settings(self, values: dict[str, str]):
        allowed = {
            "audio_directory", "nas_directory", "audio_source", "keywords",
            "base_output_folder", "base_source_system", "audio_filter_base", "theme",
            "analysis_parallelism", "updates_enabled", "updates_last_checked",
        }
        if set(values) - allowed:
            raise ValueError("Solo se permiten ajustes locales sin credenciales.")
        with self.connect() as connection:
            connection.executemany("INSERT INTO app_settings(key,value) VALUES (?,?) "
                                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", values.items())

    def register_calls(self, calls):
        with self.connect() as connection:
            connection.executemany(
                "INSERT INTO calls(filename,file_path,duration_seconds) VALUES (?,?,?) "
                "ON CONFLICT(file_path) DO UPDATE SET duration_seconds=excluded.duration_seconds",
                ((call.filename, str(call.source_path.resolve()), call.duration)
                 for call in calls if call.source_path is not None),
            )

    def save_credential(self, service: str, provider: str, model: str, encrypted_key: str):
        if service not in {"transcription", "analysis"}:
            raise ValueError("Servicio API desconocido.")
        with self.connect() as connection:
            connection.execute("INSERT INTO api_credentials(service,provider,model,encrypted_key) VALUES (?,?,?,?) "
                               "ON CONFLICT(service) DO UPDATE SET provider=excluded.provider,model=excluded.model,"
                               "encrypted_key=excluded.encrypted_key,updated_at=CURRENT_TIMESTAMP",
                               (service, provider, model, encrypted_key))

    def credentials(self):
        with self.connect() as connection:
            return {row["service"]: dict(row) for row in connection.execute("SELECT * FROM api_credentials")}

    def save_remote_connection(self, host: str, port: int, username: str, encrypted_password: str,
                               remote_path: str, host_fingerprint: str):
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO remote_connection(id,host,port,username,encrypted_password,remote_path,host_fingerprint) "
                "VALUES (1,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET host=excluded.host,port=excluded.port,"
                "username=excluded.username,encrypted_password=excluded.encrypted_password,"
                "remote_path=excluded.remote_path,host_fingerprint=excluded.host_fingerprint,updated_at=CURRENT_TIMESTAMP",
                (host, int(port), username, encrypted_password, remote_path, host_fingerprint),
            )

    def remote_connection(self):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM remote_connection WHERE id=1").fetchone()
            return dict(row) if row else None

    def call_rows(self, paths=None):
        with self.connect() as connection:
            if paths is None:
                result = [dict(row) for row in connection.execute("SELECT * FROM calls ORDER BY id")]
            else:
                normalized = [str(Path(path).resolve()) for path in paths]
                if not normalized:
                    return []
                found = {}
                for start in range(0, len(normalized), 500):
                    group = normalized[start:start + 500]
                    placeholders = ",".join("?" for _ in group)
                    found.update({row["file_path"]: dict(row) for row in connection.execute(
                        f"SELECT * FROM calls WHERE file_path IN ({placeholders})", group)})
                result = [found[path] for path in normalized if path in found]
            hits_by_call: dict[int, list[dict]] = {item["id"]: [] for item in result}
            call_ids = list(hits_by_call)
            for start in range(0, len(call_ids), 500):
                group = call_ids[start:start + 500]
                placeholders = ",".join("?" for _ in group)
                for hit in connection.execute(
                    "SELECT call_id,keyword,speaker,timestamp_seconds,context_snippet,is_risk_validated "
                    f"FROM keyword_hits WHERE call_id IN ({placeholders}) "
                    "ORDER BY call_id,timestamp_seconds", group,
                ):
                    payload = dict(hit)
                    call_id = payload.pop("call_id")
                    hits_by_call[call_id].append(payload)
            for item in result:
                item["hits"] = hits_by_call[item["id"]]
            return result

    def call_paths(self) -> list[str]:
        """Devuelve solo rutas para filtrar grandes historiales sin cargar transcripciones."""
        with self.connect() as connection:
            return [str(row[0]) for row in connection.execute("SELECT file_path FROM calls ORDER BY id")]

    def cached_file_digest(self, file_path, file_size: int, modified_ns: int) -> str | None:
        path = str(Path(file_path).resolve())
        with self.connect() as connection:
            row = connection.execute(
                "SELECT digest FROM file_fingerprints WHERE file_path=? AND file_size=? AND modified_ns=?",
                (path, int(file_size), int(modified_ns)),
            ).fetchone()
            return str(row[0]) if row else None

    def save_file_digest(self, file_path, file_size: int, modified_ns: int, digest: str) -> None:
        path = str(Path(file_path).resolve())
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO file_fingerprints(file_path,file_size,modified_ns,digest) VALUES (?,?,?,?) "
                "ON CONFLICT(file_path) DO UPDATE SET file_size=excluded.file_size,"
                "modified_ns=excluded.modified_ns,digest=excluded.digest,updated_at=CURRENT_TIMESTAMP",
                (path, int(file_size), int(modified_ns), digest),
            )

    def set_call_status(self, file_path, status: str, error=None):
        with self.connect() as connection:
            connection.execute("UPDATE calls SET status=?,analysis_error=? WHERE file_path=?",
                               (status, error, str(Path(file_path).resolve())))

    def cache_get(self, table: str, key: str):
        if table not in {"transcription_cache", "analysis_cache"}:
            raise ValueError("Caché desconocida.")
        column = "transcript_json" if table == "transcription_cache" else "result_json"
        with self.connect() as connection:
            row = connection.execute(f"SELECT {column} FROM {table} WHERE cache_key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def cache_set(self, table: str, key: str, payload):
        if table not in {"transcription_cache", "analysis_cache"}:
            raise ValueError("Caché desconocida.")
        column = "transcript_json" if table == "transcription_cache" else "result_json"
        with self.connect() as connection:
            connection.execute(f"INSERT INTO {table}(cache_key,{column}) VALUES (?,?) "
                               f"ON CONFLICT(cache_key) DO UPDATE SET {column}=excluded.{column}",
                               (key, json.dumps(payload, ensure_ascii=False)))

    def save_call_result(self, file_path, cache_key: str, analysis_terms: str, transcript, result):
        path = str(Path(file_path).resolve())
        hits = result.get("hits", [])
        with self.connect() as connection:
            row = connection.execute("SELECT id FROM calls WHERE file_path=?", (path,)).fetchone()
            if row is None:
                raise ValueError(f"El audio ya no está registrado: {path}")
            call_id = row[0]
            connection.execute("DELETE FROM keyword_hits WHERE call_id=?", (call_id,))
            connection.executemany(
                "INSERT INTO keyword_hits(call_id,keyword,speaker,timestamp_seconds,context_snippet,is_risk_validated) "
                "VALUES (?,?,?,?,?,?)",
                ((call_id, hit.get("keyword", ""), hit.get("speaker"), float(hit.get("second", 0)),
                  hit.get("snippet", ""), int(bool(hit.get("validated", False)))) for hit in hits),
            )
            category = result["category"]
            connection.execute(
                "UPDATE calls SET status='COMPLETADO',transcript=?,transcript_json=?,summary=?,sentiment=?,"
                "risk_level=?,has_sensitive_keyword=?,category=?,analysis_error=NULL,cache_key=?,"
                "analysis_terms=?,processed_at=CURRENT_TIMESTAMP WHERE id=?",
                (transcript.get("text", ""), json.dumps(transcript, ensure_ascii=False),
                 result.get("summary", ""), result.get("sentiment", "NEUTRAL"), result.get("risk", "BAJO"),
                 int(category == "ALERTA"), category, cache_key, analysis_terms, call_id),
            )

    def save_analysis_timing(self, file_path, metrics: dict) -> None:
        path = str(Path(file_path).resolve())
        with self.connect() as connection:
            row = connection.execute("SELECT id FROM calls WHERE file_path=?", (path,)).fetchone()
            if row is None:
                return
            connection.execute(
                "INSERT INTO analysis_timings(call_id,hash_ms,transcription_ms,contextual_ms,"
                "persistence_ms,total_ms,transcription_cached,analysis_cached) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(call_id) DO UPDATE SET hash_ms=excluded.hash_ms,"
                "transcription_ms=excluded.transcription_ms,contextual_ms=excluded.contextual_ms,"
                "persistence_ms=excluded.persistence_ms,total_ms=excluded.total_ms,"
                "transcription_cached=excluded.transcription_cached,analysis_cached=excluded.analysis_cached,"
                "updated_at=CURRENT_TIMESTAMP",
                (row[0], float(metrics["hash_ms"]), float(metrics["transcription_ms"]),
                 float(metrics["contextual_ms"]), float(metrics["persistence_ms"]),
                 float(metrics["total_ms"]), int(bool(metrics["transcription_cached"])),
                 int(bool(metrics["analysis_cached"]))),
            )

    def set_reviewed(self, file_path, reviewed=True):
        with self.connect() as connection:
            connection.execute("UPDATE calls SET reviewed=? WHERE file_path=?",
                               (int(reviewed), str(Path(file_path).resolve())))

    def record_analysis_base(self, call_id: int, base_path: str):
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO call_bases(call_id,base_path) VALUES (?,?) "
                "ON CONFLICT(call_id,base_path) DO UPDATE SET analyzed_at=CURRENT_TIMESTAMP",
                (call_id, str(Path(base_path).resolve())),
            )

    def start_job(self, paths, options) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO base_jobs(input_paths,status,options_json) VALUES (?,'PROCESANDO',?)",
                (json.dumps([str(Path(p).resolve()) for p in paths], ensure_ascii=False),
                 json.dumps(options, ensure_ascii=False)),
            )
            return cursor.lastrowid

    def finish_job(self, job_id, result, rows):
        with self.connect() as connection:
            connection.executemany(
                "INSERT INTO base_records(job_id,row_number,phone,call_state,agent,called_at,duration,"
                "name,identifier,identifier_type,state,base_type,base_number) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ((job_id, index, *("" if value is None else str(value) for value in row))
                 for index, row in enumerate(rows, 1)),
            )
            connection.execute(
                "UPDATE base_jobs SET status='COMPLETADO',output_path=?,read_count=?,filtered_count=?,"
                "unique_count=?,finished_at=CURRENT_TIMESTAMP WHERE id=?",
                (str(result.output), sum(source[1] for source in result.sources),
                 result.filtered, result.unique, job_id),
            )

    def fail_job(self, job_id, error, output=None):
        with self.connect() as connection:
            connection.execute("UPDATE base_jobs SET status='ERROR',error=?,output_path=?,"
                               "finished_at=CURRENT_TIMESTAMP WHERE id=?", (str(error), str(output) if output else None, job_id))

    def jobs(self, limit=100):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM base_jobs ORDER BY id DESC LIMIT ?", (limit,))]


if __name__ == "__main__":
    print(f"Base de datos preparada: {Database().path}")
