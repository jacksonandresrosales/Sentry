"""Mini aplicación local para preparar bases de Issabel y Lucid."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

if __package__:
    from .transformar_base import BaseError, ConversionResult, convert_files, default_output, normalize_base_number
else:
    from transformar_base import BaseError, ConversionResult, convert_files, default_output, normalize_base_number


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeleteBaseApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Preparar bases — Sentry")
        self.geometry("760x640")
        self.minsize(650, 620)
        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self.input_path = tk.StringVar()
        self.output_folder = tk.StringVar(value=str(PROJECT_ROOT / "outputs"))
        self.source_system = tk.StringVar(value="Issabel")
        self.source_hint = tk.StringVar()
        self.base_type = tk.StringVar(value="Usar origen / V si falta")
        self.base_number = tk.StringVar()
        self.output_name = tk.StringVar(value="Selecciona una base CSV o Excel.")
        self.status = tk.StringVar(value="Listo para cargar una base.")
        self.saved_path = tk.StringVar()
        self.result: ConversionResult | None = None
        self.busy = False
        self.events = queue.Queue()
        self.controls = []
        self.build_ui()
        self.base_number.trace_add("write", lambda *_: self.refresh_output_name())
        self.source_system.trace_add("write", lambda *_: self.source_system_changed())
        self.source_system_changed()
        self.poll_after_id = self.after(100, self.poll_events)

    def build_ui(self):
        frame = ttk.Frame(self, padding=22)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="Preparar bases", font=("Segoe UI", 20, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="Selecciona el sistema de origen y carga una base CSV o Excel.").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 16))
        source_row = ttk.Frame(frame)
        source_row.grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(source_row, text="Sistema de origen").pack(side="left", padx=(0, 12))
        self.source_system_box = ttk.Combobox(source_row, textvariable=self.source_system,
                                              state="readonly", values=("Issabel", "Lucid"), width=18)
        self.source_system_box.pack(side="left")
        ttk.Label(frame, textvariable=self.source_hint, wraplength=680).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(6, 12))
        ttk.Label(frame, text="Base de entrada (.csv o .xlsx)").grid(row=4, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.input_path, state="readonly").grid(
            row=5, column=0, sticky="ew", pady=(4, 12))
        select = ttk.Button(frame, text="Cargar base…", command=self.choose_input)
        select.grid(row=5, column=1, padx=(10, 0), pady=(4, 12))
        ttk.Label(frame, text="Carpeta de resultados").grid(row=6, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.output_folder, state="readonly").grid(
            row=7, column=0, sticky="ew", pady=(4, 10))
        folder = ttk.Button(frame, text="Cambiar carpeta…", command=self.choose_folder)
        folder.grid(row=7, column=1, padx=(10, 0), pady=(4, 10))
        self.issabel_options = options = ttk.Frame(frame)
        options.grid(row=8, column=0, columnspan=2, sticky="ew")
        ttk.Label(options, text="T BASE").grid(row=0, column=0, sticky="w")
        type_box = ttk.Combobox(options, textvariable=self.base_type, state="readonly", width=26,
                               values=("Usar origen / V si falta", "V — Verificar", "R — Remover"))
        type_box.grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(options, text="N° BASE (vacío = detectar o asignar)").grid(
            row=0, column=1, padx=(18, 0), sticky="w")
        number_entry = ttk.Entry(options, textvariable=self.base_number, width=15)
        number_entry.grid(row=1, column=1, padx=(18, 0), pady=(4, 0), sticky="w")
        ttk.Label(frame, textvariable=self.output_name, wraplength=680).grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(12, 8))
        self.process_button = ttk.Button(frame, text="Procesar y guardar Excel", command=self.start_conversion)
        self.process_button.grid(row=10, column=0, columnspan=2, sticky="ew")
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(12, 6))
        self.progress.grid_remove()
        ttk.Label(frame, textvariable=self.status, wraplength=680).grid(
            row=12, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, textvariable=self.saved_path, wraplength=680).grid(
            row=13, column=0, columnspan=2, sticky="w", pady=(5, 8))
        actions = ttk.Frame(frame)
        actions.grid(row=14, column=0, columnspan=2, sticky="w")
        self.open_file_button = ttk.Button(actions, text="Abrir resultado", state="disabled",
                                           command=self.open_result)
        self.open_file_button.pack(side="left")
        self.open_folder_button = ttk.Button(actions, text="Abrir carpeta de resultados", state="disabled",
                                             command=self.open_folder)
        self.open_folder_button.pack(side="left", padx=(10, 0))
        self.controls = [self.source_system_box, select, folder, type_box, number_entry, self.process_button]

    def source_system_changed(self):
        lucid = self.source_system.get().lower() == "lucid"
        if lucid:
            self.issabel_options.grid_remove()
        else:
            self.issabel_options.grid()
        self.source_hint.set(
            "Lucid: Teléfono, Nombre, ID y Estado (también acepta GESTION). Conserva ID y estados de personas; "
            "excluye empresas por su razón social (S.A., Ltda., etc.), no por tener RUC. "
            "Añade el cero inicial al teléfono si falta. "
            "En Sentry, busca solo grabaciones out- por teléfono desde el 01/09/2026, "
            "sin usar Fecha Rellamada ni la fecha del CSV."
            if lucid else "Issabel: formato NO con los filtros y la numeración de base habituales."
        )
        self.refresh_output_name()

    def choose_input(self):
        selected = filedialog.askopenfilename(parent=self, title="Cargar base de llamadas",
                                              filetypes=[("CSV o Excel", "*.csv *.xlsx")])
        if selected:
            self.input_path.set(selected)
            self.reset_result()
            self.refresh_output_name()

    def choose_folder(self):
        selected = filedialog.askdirectory(parent=self, title="Carpeta para guardar resultados")
        if selected:
            self.output_folder.set(selected)
            self.reset_result()
            self.refresh_output_name()

    def refresh_output_name(self):
        if not self.input_path.get():
            return
        try:
            source_system = self.source_system.get().lower()
            number = (self.base_number.get().strip() or None) if source_system == "issabel" else None
            path = default_output(Path(self.input_path.get()), Path(self.output_folder.get()), number,
                                  source_system=source_system)
            self.output_name.set(f"Salida prevista: {path.name}")
        except BaseError as exc:
            self.output_name.set(str(exc))

    def reset_result(self):
        self.result = None
        self.saved_path.set("")
        self.status.set("Base cargada. Pulsa Procesar y guardar Excel.")
        self.open_file_button.configure(state="disabled")
        self.open_folder_button.configure(state="disabled")

    def set_busy(self, value):
        self.busy = value
        for control in self.controls:
            control.configure(state="disabled" if value else
                              "readonly" if isinstance(control, ttk.Combobox) else "normal")
        if value:
            self.progress.grid()
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(value=0)
            self.progress.grid_remove()

    def start_conversion(self):
        if self.busy:
            return
        if not self.input_path.get():
            messagebox.showinfo("Carga una base", "Selecciona primero un archivo CSV o Excel.", parent=self)
            return
        source = Path(self.input_path.get())
        folder = Path(self.output_folder.get())
        source_system = self.source_system.get().lower()
        try:
            value = self.base_number.get().strip() if source_system == "issabel" else ""
            base_number = normalize_base_number(value) if value else None
        except BaseError as exc:
            messagebox.showerror("Número de base inválido", str(exc), parent=self)
            return
        choice = self.base_type.get()
        base_type = None
        if source_system == "issabel":
            base_type = "V" if choice.startswith("V —") else "R" if choice.startswith("R —") else None
        self.reset_result()
        self.set_busy(True)
        self.status.set("Procesando la base… Puedes esperar aquí; la ventana seguirá respondiendo.")
        threading.Thread(target=self.worker,
                         args=(source, folder, base_type, base_number, source_system), daemon=True).start()

    def worker(self, source, folder, base_type, base_number, source_system="issabel"):
        try:
            result = convert_files([source], base_type=base_type,
                                   base_number=base_number, output_folder=folder, source_system=source_system,
                                   entity_filter="people" if source_system == "lucid" else "all")
            self.events.put(("success", result))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def poll_events(self):
        try:
            event, value = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.set_busy(False)
            if event == "success":
                self.result = value
                read_count = sum(count for _, count, _ in value.sources)
                if value.source_system == "lucid":
                    self.status.set(f"Terminado · Registros leídos: {read_count:,} · "
                                    f"Registros de empresas excluidos: {value.excluded:,} · "
                                    f"Teléfonos únicos en la hoja final: {value.unique:,}.")
                else:
                    self.status.set(f"Terminado: {read_count:,} registros leídos; "
                                    f"{value.filtered:,} filtrados; {value.unique:,} en la hoja final.")
                self.saved_path.set(f"Guardado en: {value.output}")
                self.output_name.set(f"Archivo generado: {value.output.name}")
                self.open_file_button.configure(state="normal")
                self.open_folder_button.configure(state="normal")
                if value.missing_base_number:
                    messagebox.showinfo("Número de base no detectado",
                                        "El resultado se guardó, pero faltó N° BASE en algunos registros. "
                                        "Puedes indicar B1, B2, etc. y volver a procesar.", parent=self)
            else:
                self.status.set("No se pudo generar el resultado. Revisa el archivo y vuelve a intentar.")
                messagebox.showerror("No se pudo procesar", value, parent=self)
        self.poll_after_id = self.after(100, self.poll_events)

    def destroy(self):
        if getattr(self, "poll_after_id", None):
            self.after_cancel(self.poll_after_id)
            self.poll_after_id = None
        super().destroy()

    def open_path(self, path):
        try:
            os.startfile(str(path))
        except (OSError, AttributeError) as exc:
            messagebox.showerror("No se pudo abrir", f"{path}\n\n{exc}", parent=self)

    def open_result(self):
        if self.result:
            self.open_path(self.result.output)

    def open_folder(self):
        if self.result:
            self.open_path(self.result.output.parent)

    def close_app(self):
        if self.busy:
            messagebox.showinfo("Procesando", "Espera a que termine el procesamiento antes de cerrar.", parent=self)
        else:
            self.destroy()


if __name__ == "__main__":
    DeleteBaseApp().mainloop()
