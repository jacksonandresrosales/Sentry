"""Persistencia SQLite; las credenciales se guardan únicamente como blobs cifrados por DPAPI."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "data" / "db" / "sentry_audit.db"

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
"""


class Database:
    def __init__(self, path: Path = DEFAULT_DATABASE):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 3:
                raise RuntimeError("La base de datos pertenece a una versión más nueva de Sentry.")
            connection.executescript(SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(calls)")}
            additions = {
                "category": "TEXT NOT NULL DEFAULT 'PENDIENTE'",
                "transcript_json": "TEXT",
                "analysis_error": "TEXT",
                "cache_key": "TEXT",
                "reviewed": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE calls ADD COLUMN {name} {definition}")
            connection.execute("UPDATE calls SET status='ERROR', analysis_error="
                               "'El análisis fue interrumpido al cerrar la aplicación.' "
                               "WHERE status IN ('TRANSFIRIENDO','ANALIZANDO')")
            connection.execute("PRAGMA user_version=3")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def settings(self) -> dict[str, str]:
        with self.connect() as connection:
            return dict(connection.execute("SELECT key,value FROM app_settings").fetchall())

    def save_settings(self, values: dict[str, str]):
        allowed = {
            "audio_directory", "nas_directory", "audio_source", "keywords",
            "base_output_folder", "audio_filter_base",
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
                placeholders = ",".join("?" for _ in normalized)
                found = {row["file_path"]: dict(row) for row in connection.execute(
                    f"SELECT * FROM calls WHERE file_path IN ({placeholders})", normalized)}
                result = [found[path] for path in normalized if path in found]
            for item in result:
                item["hits"] = [dict(hit) for hit in connection.execute(
                    "SELECT keyword,speaker,timestamp_seconds,context_snippet,is_risk_validated "
                    "FROM keyword_hits WHERE call_id=? ORDER BY timestamp_seconds", (item["id"],))]
            return result

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

    def save_call_result(self, file_path, cache_key: str, transcript, result):
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
                "processed_at=CURRENT_TIMESTAMP WHERE id=?",
                (transcript.get("text", ""), json.dumps(transcript, ensure_ascii=False),
                 result.get("summary", ""), result.get("sentiment", "NEUTRAL"), result.get("risk", "BAJO"),
                 int(category == "ALERTA"), category, cache_key, call_id),
            )

    def set_reviewed(self, file_path, reviewed=True):
        with self.connect() as connection:
            connection.execute("UPDATE calls SET reviewed=? WHERE file_path=?",
                               (int(reviewed), str(Path(file_path).resolve())))

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
