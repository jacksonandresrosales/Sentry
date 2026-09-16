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

from app.database import APP_STORAGE_ROOT, Database
from app.services.base_conversion import BaseAudioIndex, load_hoja1_audio_index, process_bases
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


class AudioIndexWorker(QThread):
    succeeded = Signal(str, object)
    failed = Signal(str)

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            index = load_hoja1_audio_index(self.path)
            if not index.phones:
                raise ValueError("Hoja1 no contiene teléfonos para relacionar con grabaciones.")
            self.succeeded.emit(str(self.path), index)
        except Exception as exc:
            self.failed.emit(str(exc))


class BasesPage(QWidget):
    """Vista nativa: no lanza Tkinter ni duplica las reglas del transformador."""
    base_selected = Signal(str, object)

    def __init__(self, database: Database, parent=None):
        super().__init__(parent)
        self.database = database
        self.paths: list[Path] = []
        self.worker = None
        self.busy = False
        self.result_path = None
        self.active_base_path: Path | None = None
        self.active_phones: set[str] = set()
        self.active_index = BaseAudioIndex({})
        settings = database.settings()
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
        subtitle = QLabel("Prepara bases CSV o Excel de Issabel o Lucid para relacionarlas con tus grabaciones.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        panel = QFrame()
        panel.setObjectName("contentPanel")
        form = QVBoxLayout(panel)
        form.setContentsMargins(20, 18, 20, 18)
        form.setSpacing(12)
        source_row = QHBoxLayout()
        source_label = QLabel("Sistema de origen")
        self.source_system = QComboBox()
        self.source_system.setObjectName("baseSourceSystem")
        self.source_system.addItem("Issabel", "issabel")
        self.source_system.addItem("Lucid", "lucid")
        source_label.setBuddy(self.source_system)
        saved_source = self.source_system.findData(settings.get("base_source_system", "issabel"))
        self.source_system.setCurrentIndex(max(0, saved_source))
        source_row.addWidget(source_label)
        source_row.addWidget(self.source_system)
        source_row.addStretch()
        form.addLayout(source_row)
        self.source_hint = QLabel()
        self.source_hint.setObjectName("pageSubtitle")
        self.source_hint.setWordWrap(True)
        form.addWidget(self.source_hint)
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
        self.output_folder = QLineEdit(settings.get(
            "base_output_folder", str(APP_STORAGE_ROOT / "outputs")))
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
        self.issabel_controls = [type_label, self.base_type, number_label, self.base_number]
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
            if field in (self.state, self.call_state):
                self.issabel_controls.extend((label, field))
        advanced.addWidget(self.include_ruc, 5, 1)
        self.issabel_controls.append(self.include_ruc)
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
        self.active_base = QLabel("Base activa para grabaciones: ninguna")
        self.active_base.setObjectName("pageSubtitle")
        self.active_base.setWordWrap(True)
        self.active_base.setTextFormat(Qt.TextFormat.PlainText)
        form.addWidget(self.active_base)
        actions = QHBoxLayout()
        self.process_button = self.button("Procesar y guardar Excel", self.start_conversion, "primaryButton")
        self.use_base_button = self.button("Usar base para buscar audios", self.use_result_for_audio)
        self.open_button = self.button("Abrir resultado", self.open_result)
        self.open_folder_button = self.button("Abrir carpeta", self.open_folder)
        self.use_base_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.open_folder_button.setEnabled(False)
        actions.addWidget(self.process_button)
        actions.addWidget(self.use_base_button)
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
        self.controls = [self.source_system, self.choose_button, self.output_folder, self.folder_button,
                         self.base_type, self.base_number, self.state, self.call_state,
                         self.sheet, self.encoding, self.delimiter, self.include_ruc,
                         self.advanced_toggle, self.process_button, self.history]
        self.base_number.textChanged.connect(self.preview_name)
        self.output_folder.textChanged.connect(self.preview_name)
        self.source_system.currentIndexChanged.connect(self.source_system_changed)
        self.update_source_controls()
        self.refresh_history()
        self.restore_active_base()

    @staticmethod
    def button(text, callback, style="secondaryButton"):
        button = QPushButton(text)
        button.setObjectName(style)
        button.clicked.connect(callback)
        return button

    def toggle_advanced(self):
        self.advanced.setVisible(self.advanced_toggle.isChecked())

    def update_source_controls(self):
        lucid = self.source_system.currentData() == "lucid"
        for control in self.issabel_controls:
            control.setVisible(not lucid)
            control.setEnabled(not lucid and not self.busy)
        self.source_hint.setText(
            "Lucid: genera Teléfono, Nombre, ID y Estado; también acepta GESTION. Conserva los ID y todos los "
            "estados de personas. Excluye empresas identificadas por su razón social (S.A., Ltda., etc.), "
            "no por tener RUC. Añade el cero inicial al teléfono cuando falta. "
            "Busca solo grabaciones out- por teléfono desde el 01/09/2026 "
            "en la carpeta o servidor configurado; no usa Fecha Rellamada ni la fecha del CSV."
            if lucid else
            "Issabel: conserva el formato NO con filtros, fórmulas y numeración de base."
        )

    def source_system_changed(self):
        self.update_source_controls()
        self.preview_name()
        try:
            self.database.save_settings({"base_source_system": self.source_system.currentData()})
        except (ValueError, sqlite3.Error) as exc:
            self.status.setText(f"No se pudo guardar el sistema de origen: {exc}")

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
        self.use_base_button.setEnabled(False)
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
            source_system = self.source_system.currentData()
            number = (self.base_number.text().strip() or None) if source_system == "issabel" else None
            output = default_output(self.paths[0], Path(self.output_folder.text()), number,
                                    source_system=source_system)
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
            source_system = self.source_system.currentData()
            lucid = source_system == "lucid"
            number = (normalize_base_number(self.base_number.text().strip())
                      if not lucid and self.base_number.text().strip() else None)
            if number and len(self.paths) > 1:
                raise BaseError("El número manual se aplica a una sola base; para consolidar, usa automático.")
            delimiter = self.delimiter.text() or None
            if delimiter is not None and len(delimiter) != 1:
                raise BaseError("El separador debe tener un solo carácter.")
            folder = Path(self.output_folder.text().strip()).resolve()
            self.database.save_settings({"base_output_folder": str(folder), "base_source_system": source_system})
        except (BaseError, OSError, ValueError, sqlite3.Error) as exc:
            self.status.setText(str(exc))
            return
        options = dict(source_system=source_system, base_number=number,
                       entity_filter="people" if lucid else "all",
                       base_type=None if lucid else self.base_type.currentData(),
                       state="*" if lucid else self.state.text().strip(),
                       call_state="*" if lucid else self.call_state.text().strip(),
                       sheet=self.sheet.text().strip() or None, encoding=self.encoding.text().strip() or None,
                       delimiter=delimiter, include_ruc_third_9=not lucid and self.include_ruc.isChecked())
        self.result_path = None
        self.begin_work("Procesando bases… Puedes seguir trabajando en Auditoría.")
        self.worker = ConversionWorker(self.database, list(self.paths), folder, options, self)
        self.worker.succeeded.connect(self.conversion_done)
        self.worker.failed.connect(self.conversion_failed)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def begin_work(self, message):
        self.busy = True
        for control in self.controls:
            control.setEnabled(False)
        self.open_button.setEnabled(False)
        self.open_folder_button.setEnabled(False)
        self.use_base_button.setEnabled(False)
        self.progress.show()
        self.status.setText(message)

    def conversion_done(self, result):
        self.result_path = result.output
        self.output_name.setText(f"Archivo generado: {result.output.name}")
        self.output_name.setToolTip(str(result.output))
        read = sum(source[1] for source in result.sources)
        if result.source_system == "lucid":
            self.status.setText(
                f"Terminado · Registros leídos: {read:,} · Registros de empresas excluidos: {result.excluded:,} · "
                f"Teléfonos únicos: {result.unique:,} · "
                f"Teléfonos repetidos retirados: {result.filtered - result.unique:,}."
            )
        else:
            self.status.setText(f"Terminado · {read:,} leídos · {result.filtered:,} filtrados · "
                                f"{result.unique:,} únicos · {result.filtered - result.unique:,} teléfonos repetidos retirados.")
        if not result.filtered:
            message = (" No quedaron registros de personas; se conservan originales y cabeceras."
                       if result.source_system == "lucid" else
                       " No hubo coincidencias; se conservan originales y cabeceras.")
            self.status.setText(self.status.text() + message)

    def conversion_failed(self, message):
        self.status.setText(f"No se completó el procesamiento: {message}")

    def worker_finished(self):
        worker = self.worker
        self.worker = None
        self.busy = False
        self.progress.hide()
        for control in self.controls:
            control.setEnabled(True)
        self.update_source_controls()
        if isinstance(worker, ConversionWorker):
            self.refresh_history()
        exists = self.result_path is not None and self.result_path.is_file()
        self.use_base_button.setEnabled(exists)
        self.open_button.setEnabled(exists)
        self.open_folder_button.setEnabled(exists)
        if worker is not None:
            worker.deleteLater()

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
            self.use_base_button.setEnabled(exists)
            self.open_button.setEnabled(exists)
            self.open_folder_button.setEnabled(exists)

    def restore_active_base(self):
        selected = self.database.settings().get("audio_filter_base", "")
        if not selected:
            return
        path = Path(selected)
        try:
            index = load_hoja1_audio_index(path)
        except (OSError, ValueError, KeyError):
            self.active_base.setText("Base activa para grabaciones: no disponible")
            self.active_base.setToolTip(str(path))
            return
        self._set_active_base(path, index)

    def use_result_for_audio(self):
        if self.busy:
            return
        if self.result_path is None:
            self.status.setText("Selecciona primero un resultado generado o una fila del historial.")
            return
        path = self.result_path.resolve()
        self.begin_work("Leyendo los teléfonos de la base… Puedes seguir trabajando en Auditoría.")
        self.worker = AudioIndexWorker(path, self)
        self.worker.succeeded.connect(self.audio_index_ready)
        self.worker.failed.connect(self.audio_index_failed)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def audio_index_ready(self, selected_path: str, index: BaseAudioIndex):
        path = Path(selected_path)
        if self.result_path is None or self.result_path.resolve() != path:
            self.status.setText("La selección cambió. Vuelve a elegir la base para buscar audios.")
            return
        try:
            self.database.save_settings({"audio_filter_base": selected_path})
        except (ValueError, sqlite3.Error) as exc:
            self.audio_index_failed(str(exc))
            return
        self._set_active_base(path, index)
        lookup_hint = " de grabaciones out- por teléfono, desde el 01/09/2026" if index.source_system == "lucid" else ""
        self.status.setText(
            f"Base seleccionada · {len(index.phones):,} teléfonos únicos. "
            f"Buscando coincidencias{lookup_hint} en la carpeta o servidor de grabaciones…"
        )
        self.base_selected.emit(str(self.active_base_path), self.active_index)

    def audio_index_failed(self, message):
        self.status.setText(f"No se pudo usar la base para buscar audios: {message}")

    def closeEvent(self, event):
        if self.busy:
            self.status.setText("Espera a que termine la lectura o el procesamiento de la base antes de cerrar.")
            event.ignore()
            return
        super().closeEvent(event)

    def _set_active_base(self, path: Path, index: BaseAudioIndex):
        self.active_base_path = Path(path).resolve()
        self.active_index = index
        self.active_phones = index.phones
        self.active_base.setText(
            f"Base activa para grabaciones: {self.active_base_path.name} · "
            f"{index.source_system.title()} · {len(self.active_phones):,} teléfonos"
        )
        self.active_base.setToolTip(str(self.active_base_path))

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
