"""Consulta y limpieza de la caché de procesamiento, sin borrar llamadas."""
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.ui.theme import theme_colors


def _size_text(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024


class CacheWorker(QThread):
    succeeded = Signal(object, str)
    failed = Signal(str)

    def __init__(self, database, clear, parent):
        super().__init__(parent)
        self.database, self.clear = database, clear

    def run(self):
        try:
            message = self.database.clear_processing_cache() if self.clear else ""
            usage = self.database.processing_cache_usage()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(usage, message)


class CacheDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.worker = None
        self.usage = None
        self.setWindowTitle("Limpiar caché")
        self.setObjectName("cacheDialog")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        colors = theme_colors(window.theme)
        self.setStyleSheet(f"QDialog#cacheDialog {{ background: {colors['panel']}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)
        title = QLabel("Caché de procesamiento")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        description = QLabel("Datos reutilizables que ayudan a evitar repetir trabajo. "
                             "El tamaño indicado es aproximado.")
        description.setObjectName("pageSubtitle")
        description.setWordWrap(True)
        layout.addWidget(description)

        panel = QFrame()
        panel.setObjectName("contentPanel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setVerticalSpacing(14)
        self.sizes = {}
        self.counts = {}
        for row, (key, caption) in enumerate((
            ("transcription_cache", "Transcripciones reutilizables"),
            ("analysis_cache", "Análisis reutilizables"),
            ("file_fingerprints", "Índice de archivos"),
        )):
            grid.addWidget(QLabel(caption), row, 0)
            count = QLabel("Calculando…")
            count.setObjectName("pageSubtitle")
            grid.addWidget(count, row, 1)
            size = QLabel("—")
            size.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(size, row, 2)
            self.sizes[key], self.counts[key] = size, count
        grid.setColumnStretch(0, 1)
        layout.addWidget(panel)
        self.total = QLabel("Calculando tamaño…")
        self.total.setObjectName("formTitle")
        layout.addWidget(self.total)

        note = QLabel("Se conservan tus grabaciones, resultados guardados, informes, historial de costes y claves. "
                      "Volver a procesar sin caché podría consumir API.")
        note.setWordWrap(True)
        note.setObjectName("pageSubtitle")
        layout.addWidget(note)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Estado de la limpieza de caché")
        layout.addWidget(self.status)
        self.busy_note = QLabel("Espera a que terminen las tareas de Sentry para eliminar la caché.")
        self.busy_note.setWordWrap(True)
        self.busy_note.setObjectName("pageSubtitle")
        layout.addWidget(self.busy_note)
        actions = QHBoxLayout()
        self.close_button = QPushButton("Cerrar")
        self.close_button.setObjectName("secondaryButton")
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.close_button)
        actions.addStretch()
        self.clear_button = QPushButton("Eliminar caché")
        self.clear_button.setObjectName("primaryButton")
        self.clear_button.setEnabled(False)
        self.clear_button.clicked.connect(self._clear)
        actions.addWidget(self.clear_button)
        layout.addLayout(actions)
        self.close_button.setDefault(True)
        self.resize(580, self.sizeHint().height())
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._update_actions)
        self.timer.start()
        QTimer.singleShot(0, lambda: self._start(False))

    def _busy(self):
        return self.window.updates_panel._work_is_active() or self.window.updates_panel.worker is not None

    def _update_actions(self):
        busy = self._busy()
        self.busy_note.setVisible(busy)
        entries = sum(item["entries"] for item in self.usage.values()) if self.usage is not None else 0
        self.clear_button.setEnabled(self.worker is None and not busy and (self.usage is None or entries > 0))

    def _clear(self):
        if self.worker is None and not self._busy():
            self._start(self.usage is not None)

    def _start(self, clear):
        if self.worker is not None:
            return
        self.status.setText("Eliminando caché y recuperando espacio…" if clear else "Calculando caché…")
        self.clear_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.worker = CacheWorker(self.window.database, clear, self)
        self.worker.succeeded.connect(self._ready)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _ready(self, usage, message):
        self.usage = usage
        for key, item in usage.items():
            self.sizes[key].setText(_size_text(item["bytes"]))
            self.counts[key].setText(f"{item['entries']:,} " + ("registro" if item['entries'] == 1 else "registros"))
        self.total.setText(f"Tamaño aproximado: {_size_text(sum(item['bytes'] for item in usage.values()))}")
        self.status.setText(message or ("Caché eliminada." if self.worker.clear else ""))
        self.clear_button.setText("Eliminar caché")

    def _failed(self, error):
        self.status.setText(f"No se pudo completar la operación: {error}")
        if self.usage is None:
            self.clear_button.setText("Reintentar")

    def _finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.close_button.setEnabled(True)
        self._update_actions()

    def reject(self):
        if self.worker is None:
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            event.ignore()
        else:
            super().closeEvent(event)
