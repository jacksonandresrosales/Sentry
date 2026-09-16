"""Flujo nativo Lucid con conversiones reales y SQLite temporal."""

import csv
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import openpyxl

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.database import Database
from app.services.base_conversion import BaseAudioIndex
from app.ui.views.bases_page import BasesPage


class LucidBasesUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for_conversion(self, page):
        deadline = time.monotonic() + 15
        while page.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertFalse(page.busy, page.status.text())

    def test_index_loading_keeps_ui_responsive_and_rejects_a_stale_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp).resolve()
            database = Database(folder / "audit.db")
            page = BasesPage(database)
            source = folder / "base_Lucid.xlsx"
            source.touch()
            page.result_path = source
            started = threading.Event()
            release = threading.Event()
            loader_threads = []
            received = []
            page.base_selected.connect(lambda *args: received.append(args))

            def delayed_load(path):
                self.assertEqual(path, source)
                loader_threads.append(threading.get_ident())
                started.set()
                if not release.wait(5):
                    raise TimeoutError("La prueba no liberó la lectura.")
                return BaseAudioIndex({"0990000001": frozenset()}, source_system="lucid")

            try:
                with patch("app.ui.views.bases_page.load_hoja1_audio_index", side_effect=delayed_load):
                    page.use_result_for_audio()
                    self.assertTrue(started.wait(1))
                    self.assertNotEqual(loader_threads, [threading.get_ident()])
                    self.assertTrue(page.busy)
                    self.assertFalse(page.source_system.isEnabled())
                    self.assertFalse(page.use_base_button.isEnabled())
                    self.assertFalse(page.close())
                    timer_events = []
                    QTimer.singleShot(0, lambda: timer_events.append("responsive"))
                    self.app.processEvents()
                    self.assertEqual(timer_events, ["responsive"])
                    page.result_path = folder / "otra_base.xlsx"
                    release.set()
                    self.wait_for_conversion(page)
                self.assertIsNone(page.worker)
                self.assertTrue(page.source_system.isEnabled())
                self.assertEqual(received, [])
                self.assertEqual(page.active_phones, set())
                self.assertNotIn("audio_filter_base", database.settings())
                self.assertIn("selección cambió", page.status.text())
            finally:
                release.set()
                if page.busy:
                    self.wait_for_conversion(page)
                page.close()

    def test_failed_index_load_restores_controls_and_preserves_the_active_base(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp).resolve()
            database = Database(folder / "audit.db")
            page = BasesPage(database)
            previous = folder / "anterior.xlsx"
            index = BaseAudioIndex({"0990000001": frozenset()}, source_system="lucid")
            page._set_active_base(previous, index)
            database.save_settings({"audio_filter_base": str(previous)})
            try:
                page.result_path = folder / "eliminado.xlsx"
                page.use_result_for_audio()
                self.wait_for_conversion(page)
                self.assertIsNone(page.worker)
                self.assertTrue(page.process_button.isEnabled())
                self.assertTrue(page.progress.isHidden())
                self.assertEqual(page.active_phones, {"0990000001"})
                self.assertEqual(database.settings()["audio_filter_base"], str(previous))
                self.assertIn("No se pudo usar", page.status.text())
            finally:
                if page.busy:
                    self.wait_for_conversion(page)
                page.close()

    def test_lucid_ignores_issabel_filters_and_remembers_choice(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp).resolve()
            source = folder / "consolidado.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["Celular", "Nombre", "GESTION", "ID", "NOTAS"])
                writer.writerow(["990000001", "CLIENTE UNO", "No contesta", "0123456789", "dato privado"])
                writer.writerow(["0980000002", "CLIENTE DOS", "Venta", "1791234567001", "otra nota"])
                writer.writerow(["990000001", "CLIENTE UNO", "No contesta", "0123456789", "repetido"])
                writer.writerow(["0970000003", "COMERCIAL DE PRUEBA S.A.", "Venta", "1234567890001", "empresa"])
                writer.writerow(["0960000004", "SUMINISTROS LTDA.", "Venta", "1234567890002", "empresa"])
            database = Database(folder / "audit.db")
            page = BasesPage(database)
            try:
                self.assertEqual(page.source_system.currentData(), "issabel")
                page.base_number.setText("B0")
                page.base_type.setCurrentIndex(2)
                page.state.setText("NO COINCIDE")
                page.call_state.setText("NO COINCIDE")
                page.include_ruc.setChecked(True)
                page.source_system.setCurrentIndex(page.source_system.findData("lucid"))
                self.assertTrue(page.base_number.isHidden())
                self.assertFalse(page.state.isEnabled())
                self.assertTrue(page.sheet.isEnabled())
                self.assertIn("Teléfono, Nombre, ID y Estado", page.source_hint.text())
                self.assertIn("no por tener RUC", page.source_hint.text())
                self.assertEqual(database.settings()["base_source_system"], "lucid")
                page.output_folder.setText(str(folder))
                page.set_files([source])
                self.assertIn("consolidado_Lucid.xlsx", page.output_name.text())
                page.start_conversion()
                self.wait_for_conversion(page)
                self.assertIsNotNone(page.result_path, page.status.text())
                self.assertFalse(page.call_state.isEnabled())
                self.assertIn("Registros de empresas excluidos: 2", page.status.text())
                self.assertIn("Teléfonos repetidos retirados: 1", page.status.text())
                book = openpyxl.load_workbook(page.result_path, read_only=True, data_only=True)
                try:
                    self.assertEqual(list(book["Hoja1"].values), [
                        ("Teléfono", "Nombre", "ID", "Estado"),
                        ("0990000001", "CLIENTE UNO", "0123456789", "No contesta"),
                        ("0980000002", "CLIENTE DOS", "1791234567001", "Venta"),
                    ])
                finally:
                    book.close()
                page.use_result_for_audio()
                self.wait_for_conversion(page)
                self.assertEqual(page.active_index.source_system, "lucid")
                self.assertEqual(page.active_index.dates, set())
                self.assertEqual(page.active_phones, {"0990000001", "0980000002"})
                self.assertIn("grabaciones out- por teléfono, desde el 01/09/2026", page.status.text())
            finally:
                if page.busy:
                    self.wait_for_conversion(page)
                page.close()

            restored = BasesPage(Database(database.path))
            try:
                self.assertEqual(restored.source_system.currentData(), "lucid")
                self.assertEqual(restored.active_index.source_system, "lucid")
                restored.source_system.setCurrentIndex(restored.source_system.findData("issabel"))
                self.assertTrue(restored.base_number.isEnabled())
                restored.history.selectRow(0)
                restored.use_result_for_audio()
                self.wait_for_conversion(restored)
                self.assertEqual(restored.source_system.currentData(), "issabel")
                self.assertEqual(restored.active_index.source_system, "lucid")
                self.assertIn("Lucid", restored.active_base.text())
            finally:
                if restored.busy:
                    self.wait_for_conversion(restored)
                restored.close()


if __name__ == "__main__":
    unittest.main()
