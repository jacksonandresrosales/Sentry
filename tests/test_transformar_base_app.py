"""Pruebas de la mini aplicación con ventana oculta y archivos ficticios."""

import csv
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts.transformar_base_app import DeleteBaseApp
from scripts.transformar_base import DEFAULT_STATE


class AppTests(unittest.TestCase):
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
