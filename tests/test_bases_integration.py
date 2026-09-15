"""Pruebas integradas con CSV ficticio, Qt sin pantalla y SQLite temporal."""
import csv
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import openpyxl

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from app.database import Database
from app.services.base_conversion import load_hoja1_audio_index, load_hoja1_phones, normalize_phone_number, process_bases
from app.ui.views.main_window import SentryWindow
from scripts.transformar_base import DEFAULT_STATE


def make_csv(path):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Teléfono", "Estado Llamada", "Agente", "Fecha y hora",
                         "Duración(Seg)", "Nombre", "ID", "ESTADO"])
        row = ["0990000001", "Success", "00123", "2026-07-01 12:00:00", "37",
               "PERSONA DE PRUEBA", "0123456789", DEFAULT_STATE]
        writer.writerow(row)
        writer.writerow(row)


class DatabaseTests(unittest.TestCase):
    def test_schema_jobs_records_and_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            database = Database(folder / "audit.db")
            source = folder / "base.csv"
            make_csv(source)
            result = process_bases(database, [source], folder)
            self.assertEqual((result.filtered, result.unique), (2, 1))
            self.assertEqual(load_hoja1_phones(result.output), {"0990000001"})
            audio_index = load_hoja1_audio_index(result.output)
            self.assertEqual(audio_index.phone_dates, {"0990000001": frozenset({"20260701"})})
            self.assertEqual(audio_index.dates, {"20260701"})
            self.assertEqual(normalize_phone_number("+593 990 000 001"), "0990000001")
            database = Database(database.path)
            self.assertEqual(database.jobs()[0]["status"], "COMPLETADO")
            self.assertEqual(database.jobs()[0]["read_count"], 2)
            with database.connect() as connection:
                row = connection.execute("SELECT * FROM base_records").fetchone()
                self.assertEqual(row["identifier"], "0123456789")
                self.assertEqual(row["phone"], "0990000001")
                self.assertEqual(row["base_number"], "B1")
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_failed_input_is_logged_without_records(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            database = Database(folder / "audit.db")
            with self.assertRaises(FileNotFoundError):
                process_bases(database, [folder / "missing.csv"], folder)
            self.assertEqual(database.jobs()[0]["status"], "ERROR")
            with database.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM base_records").fetchone()[0], 0)

    def test_credentials_rejected_and_transaction_rolls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Database(Path(temp) / "audit.db")
            with self.assertRaises(ValueError):
                database.save_settings({"api_key": "secret"})
            with self.assertRaises(sqlite3.IntegrityError), database.connect() as connection:
                connection.execute("INSERT INTO app_settings VALUES ('keywords','prueba')")
                connection.execute("INSERT INTO keyword_hits(call_id,keyword,timestamp_seconds) VALUES (99,'prueba',1)")
            self.assertEqual(database.settings(), {})

    def test_empty_results_are_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "base.csv"
            make_csv(source)
            database = Database(folder / "audit.db")
            result = process_bases(database, [source], folder, state="NO COINCIDE")
            self.assertEqual(result.unique, 0)
            self.assertEqual(database.jobs()[0]["status"], "COMPLETADO")

    def test_audio_base_requires_hoja1(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sin-hoja1.xlsx"
            book = openpyxl.Workbook()
            book.active.title = "Datos"
            book.save(path)
            book.close()
            with self.assertRaisesRegex(ValueError, "Hoja1"):
                load_hoja1_phones(path)

    def test_history_failure_keeps_generated_excel(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "base.csv"
            make_csv(source)
            database = Database(folder / "audit.db")
            with patch.object(database, "finish_job", side_effect=sqlite3.OperationalError("sin espacio")):
                with self.assertRaisesRegex(RuntimeError, "Excel está guardado"):
                    process_bases(database, [source], folder)
            job = database.jobs()[0]
            self.assertEqual(job["status"], "ERROR")
            self.assertTrue(Path(job["output_path"]).is_file())

    def test_multiple_sources_consolidate_in_one_job(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            first, second = folder / "base B1.csv", folder / "base B2.csv"
            make_csv(first)
            make_csv(second)
            database = Database(folder / "audit.db")
            result = process_bases(database, [first, second], folder)
            self.assertEqual((result.filtered, result.unique), (4, 1))
            self.assertEqual(database.jobs()[0]["read_count"], 4)

    def test_call_rows_supports_large_batches_and_groups_keyword_hits(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            database = Database(folder / "audit.db")
            paths = [folder / f"audio-{index}.wav" for index in range(1_205)]
            with database.connect() as connection:
                connection.executemany(
                    "INSERT INTO calls(filename,file_path,status,category) VALUES (?,?,'COMPLETADO','ALERTA')",
                    ((path.name, str(path.resolve())) for path in paths),
                )
                call_ids = [row[0] for row in connection.execute("SELECT id FROM calls ORDER BY id")]
                connection.executemany(
                    "INSERT INTO keyword_hits(call_id,keyword,timestamp_seconds) VALUES (?,'demanda',5)",
                    ((call_id,) for call_id in call_ids),
                )
            rows = database.call_rows(paths)
            self.assertEqual(len(rows), len(paths))
            self.assertTrue(all(row["hits"][0]["keyword"] == "demanda" for row in rows))


class BasesUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for_conversion(self, page):
        deadline = time.monotonic() + 15
        while page.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(page.busy, page.status.text())
        self.assertTrue(page.progress.isHidden())

    def test_header_native_conversion_repeats_and_persistent_history(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp).resolve()
            source = folder / "base.csv"
            make_csv(source)
            window = SentryWindow(folder / "audit.db")
            try:
                window.bases_button.click()
                self.assertIs(window.pages.currentWidget(), window.bases_page)
                self.assertTrue(window.bases_button.isChecked())
                page = window.bases_page
                self.assertTrue(page.progress.isHidden())
                page.set_files([source])
                page.output_folder.setText(str(folder))
                page.start_conversion()
                self.assertTrue(page.busy)
                self.assertFalse(page.process_button.isEnabled())
                window._switch_page("audit")
                self.assertEqual(window.pages.currentIndex(), 0)
                self.wait_for_conversion(page)
                first = page.result_path
                before = first.read_bytes()
                self.assertEqual(first.name, "base_DB_delete_B1.xlsx")
                self.assertIn("2 filtrados", page.status.text())
                self.assertEqual(page.history.rowCount(), 1)
                self.assertTrue(page.use_base_button.isEnabled())
                page.use_result_for_audio()
                self.assertEqual(page.active_phones, {"0990000001"})
                self.assertEqual(window.active_base_phones, {"0990000001"})
                self.assertEqual(window.database.settings()["audio_filter_base"], str(first))
                page.start_conversion()
                self.wait_for_conversion(page)
                self.assertEqual(page.result_path.name, "base_DB_delete_B2.xlsx")
                self.assertEqual(first.read_bytes(), before)
                with patch.object(page, "open_path") as opener:
                    page.open_folder()
                    opener.assert_called_once_with(folder)
                page.base_number.setText("B0")
                page.start_conversion()
                self.assertFalse(page.busy)
                self.assertEqual(page.history.rowCount(), 2)
                self.assertTrue(page.progress.isHidden())
            finally:
                if window.bases_page.busy:
                    self.wait_for_conversion(window.bases_page)
                window.close()
            reopened = SentryWindow(folder / "audit.db")
            try:
                self.assertEqual(reopened.bases_page.history.rowCount(), 2)
                self.assertEqual(reopened.bases_page.output_folder.text(), str(folder))
                self.assertEqual(reopened.active_base_phones, {"0990000001"})
                reopened.bases_page.history.selectRow(1)
                self.assertEqual(reopened.bases_page.result_path, first)
            finally:
                reopened.close()

    def test_invalid_file_restores_controls_and_can_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            window = SentryWindow(folder / "audit.db")
            try:
                page = window.bases_page
                page.set_files([folder / "missing.csv"])
                page.output_folder.setText(str(folder))
                page.start_conversion()
                self.wait_for_conversion(page)
                self.assertIn("No se completó", page.status.text())
                self.assertTrue(page.process_button.isEnabled())
                self.assertFalse(page.open_button.isEnabled())
                self.assertEqual(page.jobs[0]["status"], "ERROR")
                source = folder / "base.csv"
                make_csv(source)
                page.set_files([source])
                page.start_conversion()
                self.wait_for_conversion(page)
                self.assertTrue(page.result_path.exists())
            finally:
                if window.bases_page.busy:
                    self.wait_for_conversion(window.bases_page)
                window.close()


if __name__ == "__main__":
    unittest.main()
