"""Pruebas de la mini aplicación con ventana oculta y archivos ficticios."""

import csv
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import openpyxl

from scripts.transformar_base_app import DeleteBaseApp
from scripts.transformar_base import DEFAULT_STATE


class AppTests(unittest.TestCase):
    def test_lucid_selection_processes_all_states_with_no_base_number(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp).resolve()
            source = folder / "consolidado.csv"
            with source.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["Celular", "Nombre", "GESTION", "ID"])
                writer.writerow(["990000001", "CLIENTE DE PRUEBA", "No contesta", "0123456789"])
                writer.writerow(["0980000002", "OTRO CLIENTE", "Venta", "1791234567001"])
                writer.writerow(["0970000003", "COMERCIAL S.A.", "Venta", "1234567890001"])
            app = DeleteBaseApp()
            app.withdraw()
            try:
                self.assertEqual(app.source_system.get(), "Issabel")
                app.base_number.set("B0")
                app.source_system.set("Lucid")
                self.assertEqual(app.issabel_options.winfo_manager(), "")
                self.assertIn("Teléfono, Nombre, ID y Estado", app.source_hint.get())
                self.assertIn("no por tener RUC", app.source_hint.get())
                app.input_path.set(str(source))
                app.output_folder.set(str(folder))
                app.refresh_output_name()
                self.assertIn("consolidado_Lucid.xlsx", app.output_name.get())
                with patch("scripts.transformar_base_app.messagebox.showerror") as errors:
                    app.start_conversion()
                    deadline = time.monotonic() + 10
                    while app.busy and time.monotonic() < deadline:
                        app.update()
                        time.sleep(0.01)
                    errors.assert_not_called()
                self.assertFalse(app.busy)
                self.assertIsNotNone(app.result, app.status.get())
                self.assertEqual(app.result.source_system, "lucid")
                self.assertEqual(app.result.entity_filter, "people")
                self.assertEqual(app.result.excluded, 1)
                self.assertIn("Registros de empresas excluidos: 1", app.status.get())
                book = openpyxl.load_workbook(app.result.output, read_only=True, data_only=True)
                try:
                    self.assertEqual(list(book["Hoja1"].values), [
                        ("Teléfono", "Nombre", "ID", "Estado"),
                        ("0990000001", "CLIENTE DE PRUEBA", "0123456789", "No contesta"),
                        ("0980000002", "OTRO CLIENTE", "1791234567001", "Venta"),
                    ])
                finally:
                    book.close()
                app.source_system.set("Issabel")
                self.assertEqual(app.issabel_options.winfo_manager(), "grid")
                self.assertEqual(app.base_number.get(), "B0")
            finally:
                app.destroy()

    def test_select_process_and_open_result_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp).resolve()
            source = folder / "PRUEBA B1.csv"
            with source.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["Teléfono", "Estado Llamada", "Agente", "Fecha y hora",
                                 "Duración(Seg)", "Nombre", "ID", "ESTADO"])
                writer.writerow(["0990000001", "Success", "00123", "2026-07-01 12:00:00",
                                 "37", "PERSONA DE PRUEBA", "0123456789", DEFAULT_STATE])
            app = DeleteBaseApp()
            app.withdraw()
            try:
                self.assertEqual(app.progress.winfo_manager(), "")
                with patch("scripts.transformar_base_app.filedialog.askopenfilename", return_value=str(source)):
                    app.choose_input()
                app.output_folder.set(str(folder))
                self.assertIn("PRUEBA B1_DB_delete_B1.xlsx", app.output_name.get())
                app.start_conversion()
                self.assertTrue(app.busy)
                self.assertEqual(app.progress.winfo_manager(), "grid")
                deadline = time.monotonic() + 10
                while app.busy and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertFalse(app.busy)
                self.assertEqual(app.progress.winfo_manager(), "")
                self.assertEqual(float(app.progress["value"]), 0)
                self.assertEqual(app.result.output, folder / "PRUEBA B1_DB_delete_B1.xlsx")
                self.assertTrue(app.result.output.exists())
                self.assertIn("Terminado", app.status.get())
                self.assertEqual(str(app.open_folder_button["state"]), "normal")
                with patch.object(app, "open_path") as opener:
                    app.open_folder()
                    opener.assert_called_once_with(folder)
                before = app.result.output.read_bytes()
                with patch("scripts.transformar_base_app.messagebox.askyesno") as confirmation:
                    app.start_conversion()
                    deadline = time.monotonic() + 10
                    while app.busy and time.monotonic() < deadline:
                        app.update()
                        time.sleep(0.01)
                    confirmation.assert_not_called()
                self.assertFalse(app.busy)
                self.assertEqual(app.result.output.name, "PRUEBA B1_DB_delete_B1_2.xlsx")
                self.assertEqual((folder / "PRUEBA B1_DB_delete_B1.xlsx").read_bytes(), before)
                self.assertEqual(len(list(folder.glob("*DB_delete*.xlsx"))), 2)
                app.base_number.set("B0")
                with patch("scripts.transformar_base_app.messagebox.showerror") as error:
                    app.start_conversion()
                    error.assert_called_once()
                self.assertFalse(app.busy)
                self.assertEqual(app.progress.winfo_manager(), "")
            finally:
                app.destroy()


if __name__ == "__main__":
    unittest.main()
