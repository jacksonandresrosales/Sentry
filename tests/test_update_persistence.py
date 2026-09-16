"""La actualización respalda WAL y nunca sustituye la información del cliente."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.database import Database


class UpdatePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = Database(self.root / "client.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_backup_contains_uncheckpointed_wal_and_client_settings(self):
        with self.database.connect() as source:
            source.execute("PRAGMA wal_autocheckpoint=0")
            source.execute("INSERT INTO app_settings VALUES ('keywords','demanda, denuncia')")
            source.execute("INSERT INTO api_credentials(service,provider,model,encrypted_key) "
                           "VALUES ('analysis','gemini','model','protected-secret')")
            source.commit()
            self.assertTrue(Path(str(self.database.path) + "-wal").is_file())
            target = self.database.backup_to(self.root / "backups" / "snapshot.db")
        saved = sqlite3.connect(target)
        try:
            self.assertEqual(saved.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(saved.execute("SELECT value FROM app_settings").fetchone()[0], "demanda, denuncia")
            self.assertEqual(saved.execute("SELECT encrypted_key FROM api_credentials").fetchone()[0], "protected-secret")
        finally:
            saved.close()
        # Abrir una versión nueva con esquema compatible conserva la información.
        self.assertEqual(Database(self.database.path).settings()["keywords"], "demanda, denuncia")

    def test_cancel_and_collision_do_not_damage_live_database_or_existing_backup(self):
        self.database.save_settings({"keywords": "demanda"})
        with self.assertRaises(InterruptedError):
            self.database.backup_to(self.root / "cancel.db", cancel=lambda: True)
        self.assertFalse((self.root / "cancel.db").exists())
        self.assertFalse(list(self.root.glob(".sentry-backup-*")))
        with self.assertRaises(ValueError):
            self.database.backup_to(self.database.path)
        target = self.database.backup_to(self.root / "saved.db")
        before = target.read_bytes()
        with self.assertRaises(ValueError):
            self.database.backup_to(target)
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(self.database.settings()["keywords"], "demanda")

    def test_failed_migration_is_rolled_back_with_recoverable_backup(self):
        old_path = self.root / "old.db"
        connection = sqlite3.connect(old_path)
        connection.execute("CREATE TABLE client_notes(value TEXT)")
        connection.execute("INSERT INTO client_notes VALUES ('conservar')")
        connection.execute("PRAGMA user_version=5")
        connection.commit()
        connection.close()
        with patch("app.database.SCHEMA", "CREATE TABLE added(value TEXT); INVALID SQL;"):
            with self.assertRaises(sqlite3.Error):
                Database(old_path)
        connection = sqlite3.connect(old_path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='added'").fetchone())
            self.assertEqual(connection.execute("SELECT value FROM client_notes").fetchone()[0], "conservar")
        finally:
            connection.close()
        self.assertEqual(len(list((self.root / "backups").glob("*.db"))), 1)

    def test_newer_schema_is_not_overwritten_or_downgraded(self):
        with self.database.connect() as connection:
            connection.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(RuntimeError, "versión más nueva"):
            Database(self.database.path)
