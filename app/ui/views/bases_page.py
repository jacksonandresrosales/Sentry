from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QProgressBar,
    QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.database import Database
from app.services.base_conversion import process_bases
from scripts.transformar_base import BaseError, DEFAULT_STATE, default_output, normalize_base_number


class ConversionWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, database, paths, folder, options, parent=None):
        super().__init__(parent)
        self.database, self.paths, self.folder, self.options = database, paths, folder, options

    def run(self):
        try:
            self.succeeded.emit(process_bases(self.database, self.paths, self.folder, **self.options))
        except Exception as exc:
            self.failed.emit(str(exc))


class BasesPage(QWidget):
    """Vista nativa: no lanza Tkinter ni duplica las reglas del transformador."""
    def __init__(self, database: Database, parent=None):
        super().__init__(parent)
        self.database = database
        self.paths: list[Path] = []
        self.worker = None
        self.busy = False
        self.result_path = None
        self.setObjectName("basesPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("configScroll")
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("basesContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(18)
        title = QLabel("Preparar bases")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Carga CSV o Excel y genera el formato NO, con filtros, fórmulas y numeración segura.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        panel = QFrame()
        panel.setObjectName("contentPanel")
        form = QVBoxLayout(panel)
        form.setContentsMargins(20, 18, 20, 18)
        form.setSpacing(12)
        files_row = QHBoxLayout()
        files_title = QLabel("Bases de origen")
        files_title.setObjectName("formTitle")
        self.choose_button = self.button("Cargar bases…", self.choose_files)
        files_row.addWidget(files_title)
        files_row.addStretch()
        files_row.addWidget(self.choose_button)
        form.addLayout(files_row)
        self.files_list = QListWidget()
        self.files_list.setAccessibleName("Archivos de origen seleccionados")
        self.files_list.setMaximumHeight(88)
        form.addWidget(self.files_list)
        fields = QGridLayout()
        fields.setHorizontalSpacing(12)
        fields.setVerticalSpacing(10)
        self.output_folder = QLineEdit(database.settings().get(
            "base_output_folder", str(Path(__file__).resolve().parents[3] / "outputs")))
        self.folder_button = self.button("Examinar…", self.choose_folder)
        folder_label = QLabel("Carpeta de resultados")
        folder_label.setBuddy(self.output_folder)
        fields.addWidget(folder_label, 0, 0)
        fields.addWidget(self.output_folder, 0, 1, 1, 3)
        fields.addWidget(self.folder_button, 0, 4)
        self.base_type = QComboBox()
        self.base_type.addItem("Conservar origen / V si falta", None)
        self.base_type.addItem("V · Verificar", "V")
        self.base_type.addItem("R · Remover", "R")
        type_label = QLabel("T BASE")
        type_label.setBuddy(self.base_type)
        fields.addWidget(type_label, 1, 0)
        fields.addWidget(self.base_type, 1, 1)
        self.base_number = QLineEdit()
        self.base_number.setPlaceholderText("Automático")
        self.base_number.setToolTip("Detecta el origen o asigna B1, B2… Manual solo para una base.")
        number_label = QLabel("N° BASE")
        number_label.setBuddy(self.base_number)
        fields.addWidget(number_label, 1, 2)
        fields.addWidget(self.base_number, 1, 3, 1, 2)
        form.addLayout(fields)

        self.advanced_toggle = self.button("Opciones avanzadas", self.toggle_advanced)
        self.advanced_toggle.setCheckable(True)
        form.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        advanced = QGridLayout(self.advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        self.state = QLineEdit(DEFAULT_STATE)
        self.call_state = QLineEdit("Success")
        self.sheet = QLineEdit()
        self.sheet.setPlaceholderText("Detectar hojas originales")
        self.encoding = QLineEdit()
        self.encoding.setPlaceholderText("Automática")
        self.delimiter = QLineEdit()
        self.delimiter.setPlaceholderText("Automático")
        self.include_ruc = QCheckBox("Incluir RUC con tercer dígito 9")
        for row, (name, field) in enumerate([
            ("ESTADO (* = todos)", self.state), ("Estado llamada (* = todos)", self.call_state),
            ("Hoja Excel", self.sheet), ("Codificación CSV", self.encoding), ("Separador CSV", self.delimiter),
        ]):
            label = QLabel(name)
            label.setBuddy(field)
            advanced.addWidget(label, row, 0)
            advanced.addWidget(field, row, 1)
        advanced.addWidget(self.include_ruc, 5, 1)
        self.advanced.hide()
        form.addWidget(self.advanced)
        self.output_name = QLabel("Selecciona una o varias bases. Los archivos originales no se modifican.")
        self.output_name.setObjectName("pageSubtitle")
        self.output_name.setWordWrap(True)
        self.output_name.setTextFormat(Qt.TextFormat.PlainText)
        form.addWidget(self.output_name)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        form.addWidget(self.progress)
        self.status = QLabel("Listo para cargar una base.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        form.addWidget(self.status)
        actions = QHBoxLayout()
        self.process_button = self.button("Procesar y guardar Excel", self.start_conversion, "primaryButton")
        self.open_button = self.button("Abrir resultado", self.open_result)
        self.open_folder_button = self.button("Abrir carpeta", self.open_folder)
        self.open_button.setEnabled(False)
        self.open_folder_button.setEnabled(False)
        actions.addWidget(self.process_button)
        actions.addStretch()
        actions.addWidget(self.open_button)
        actions.addWidget(self.open_folder_button)
        form.addLayout(actions)
        layout.addWidget(panel)

        history_panel = QFrame()
        history_panel.setObjectName("contentPanel")
        history_layout = QVBoxLayout(history_panel)
        history_layout.setContentsMargins(20, 18, 20, 18)
        history_title = QLabel("Historial de procesamiento")
        history_title.setObjectName("formTitle")
        history_layout.addWidget(history_title)
        hint = QLabel("Selecciona una ejecución para abrir su resultado. Se muestran las últimas 100.")
        hint.setObjectName("pageSubtitle")
        hint.setWordWrap(True)
        history_layout.addWidget(hint)
        self.history = QTableWidget(0, 6)
        self.history.setHorizontalHeaderLabels(["N°", "Fecha", "Archivo", "Filtrados", "Únicos", "Estado"])
        self.history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history.verticalHeader().hide()
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.history.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.history.setMinimumHeight(160)
        self.history.itemSelectionChanged.connect(self.select_history)
        history_layout.addWidget(self.history)
        layout.addWidget(history_panel)
        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self.controls = [self.choose_button, self.output_folder, self.folder_button,
                         self.base_type, self.base_number, self.state, self.call_state,
                         self.sheet, self.encoding, self.delimiter, self.include_ruc,
                         self.advanced_toggle, self.process_button, self.history]
        self.base_number.textChanged.connect(self.preview_name)
        self.output_folder.textChanged.connect(self.preview_name)
        self.refresh_history()

    @staticmethod
    def button(text, callback, style="secondaryButton"):
        button = QPushButton(text)
        button.setObjectName(style)
        button.clicked.connect(callback)
        return button

    def toggle_advanced(self):
        self.advanced.setVisible(self.advanced_toggle.isChecked())

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Seleccionar bases", "", "Bases CSV o Excel (*.csv *.xlsx)")
        if paths:
            self.set_files([Path(path) for path in paths])

    def set_files(self, paths):
        self.paths = list(paths)
        self.files_list.clear()
        for path in self.paths:
            self.files_list.addItem(path.name)
            self.files_list.item(self.files_list.count() - 1).setToolTip(str(path))
        self.result_path = None
        self.open_button.setEnabled(False)
        self.open_folder_button.setEnabled(False)
        self.preview_name()

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Carpeta de resultados", self.output_folder.text())
        if folder:
            self.output_folder.setText(folder)

    def preview_name(self):
        if not self.paths:
            return
        try:
            output = default_output(self.paths[0], Path(self.output_folder.text()), self.base_number.text().strip() or None)
            self.output_name.setText(f"Salida prevista: {output.name} · Se confirma al leer el origen.")
        except BaseError as exc:
            self.output_name.setText(str(exc))

    def start_conversion(self):
        if self.busy:
            return
        if not self.paths:
            self.status.setText("Carga al menos una base CSV o Excel.")
            return
        if not self.output_folder.text().strip():
            self.status.setText("Selecciona una carpeta de resultados.")
            return
        try:
            number = normalize_base_number(self.base_number.text().strip()) if self.base_number.text().strip() else None
            if number and len(self.paths) > 1:
                raise BaseError("El número manual se aplica a una sola base; para consolidar, usa automático.")
            delimiter = self.delimiter.text() or None
            if delimiter is not None and len(delimiter) != 1:
                raise BaseError("El separador debe tener un solo carácter.")
            folder = Path(self.output_folder.text().strip()).resolve()
            self.database.save_settings({"base_output_folder": str(folder)})
        except (BaseError, OSError, ValueError, sqlite3.Error) as exc:
            self.status.setText(str(exc))
            return
        options = dict(base_number=number, base_type=self.base_type.currentData(),
                       state=self.state.text().strip(), call_state=self.call_state.text().strip(),
                       sheet=self.sheet.text().strip() or None, encoding=self.encoding.text().strip() or None,
                       delimiter=delimiter, include_ruc_third_9=self.include_ruc.isChecked())
        self.busy = True
        self.result_path = None
        for control in self.controls:
            control.setEnabled(False)
        self.open_button.setEnabled(False)
        self.open_folder_button.setEnabled(False)
        self.progress.show()
        self.status.setText("Procesando bases… Puedes seguir trabajando en Auditoría.")
        self.worker = ConversionWorker(self.database, list(self.paths), folder, options, self)
        self.worker.succeeded.connect(self.conversion_done)
        self.worker.failed.connect(self.conversion_failed)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def conversion_done(self, result):
        self.result_path = result.output
        self.output_name.setText(f"Archivo generado: {result.output.name}")
        self.output_name.setToolTip(str(result.output))
        read = sum(source[1] for source in result.sources)
        self.status.setText(f"Terminado · {read:,} leídos · {result.filtered:,} filtrados · "
                            f"{result.unique:,} únicos · {result.filtered - result.unique:,} teléfonos repetidos retirados.")
        if not result.filtered:
            self.status.setText(self.status.text() + " No hubo coincidencias; se conservan originales y cabeceras.")

    def conversion_failed(self, message):
        self.status.setText(f"No se completó el procesamiento: {message}")

    def worker_finished(self):
        self.busy = False
        self.progress.hide()
        for control in self.controls:
            control.setEnabled(True)
        self.refresh_history()
        exists = self.result_path is not None and self.result_path.is_file()
        self.open_button.setEnabled(exists)
        self.open_folder_button.setEnabled(exists)
        self.worker.deleteLater()
        self.worker = None

    def refresh_history(self):
        try:
            self.jobs = self.database.jobs()
        except Exception as exc:
            self.status.setText(f"No se pudo leer el historial: {exc}")
            return
        self.history.blockSignals(True)
        self.history.setRowCount(len(self.jobs))
        for row, job in enumerate(self.jobs):
            name = Path(job["output_path"]).name if job["output_path"] else Path(json.loads(job["input_paths"])[0]).name
            values = [job["id"], job["created_at"], name, job["filtered_count"], job["unique_count"], job["status"]]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(job["error"] or job["output_path"] or job["input_paths"])
                self.history.setItem(row, column, item)
        self.history.clearSelection()
        self.history.blockSignals(False)

    def select_history(self):
        row = self.history.currentRow()
        if 0 <= row < len(self.jobs):
            path = self.jobs[row]["output_path"]
            self.result_path = Path(path) if path else None
            exists = self.result_path is not None and self.result_path.is_file()
            self.open_button.setEnabled(exists)
            self.open_folder_button.setEnabled(exists)

    def open_result(self):
        if self.result_path is not None:
            self.open_path(self.result_path)

    def open_folder(self):
        if self.result_path is not None:
            self.open_path(self.result_path.parent)

    def open_path(self, path):
        if not path.exists():
            self.status.setText("El resultado fue movido o eliminado. Revisa la carpeta de salida.")
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.status.setText(f"No se pudo abrir automáticamente: {path}")
