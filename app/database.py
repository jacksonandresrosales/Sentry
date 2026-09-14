"""Persistencia local SQLite. Las credenciales nunca se almacenan aquí."""
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
            if version > 1:
                raise RuntimeError("La base de datos pertenece a una versión más nueva de Sentry.")
            connection.executescript(SCHEMA)
            connection.execute("PRAGMA user_version=1")

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
        allowed = {"audio_directory", "keywords", "base_output_folder"}
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
