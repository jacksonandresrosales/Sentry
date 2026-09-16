"""Actualizaciones verificadas, con respaldo local y consentimiento para reiniciar."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time
import uuid

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from app.about import APP_VERSION
from app.database import APP_STORAGE_ROOT
from app.services.app_updates import check_for_update, download_update, launch_installer


class UpdateWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(object, object)

    def __init__(self, mode, database, release=None, parent=None):
        super().__init__(parent)
        self.mode, self.database, self.release = mode, database, release

    def run(self):
        try:
            if self.mode == "check":
                result = check_for_update(APP_VERSION, cancel=self.isInterruptionRequested)
            elif self.mode == "download":
                result = download_update(self.release, APP_STORAGE_ROOT / "updates",
                                         progress=self.progress.emit, cancel=self.isInterruptionRequested)
            else:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                target = APP_STORAGE_ROOT / "backups" / f"before-update-{stamp}-{uuid.uuid4().hex[:8]}.db"
                result = self.database.backup_to(target, cancel=self.isInterruptionRequested)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            if not self.isInterruptionRequested():
                self.succeeded.emit(result)


class UpdatePanel(QFrame):
    CHECK_INTERVAL = 6 * 60 * 60

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.worker: UpdateWorker | None = None
        self.release = None
        self.downloaded: Path | None = None
        self.closing = False
        self.manual_check = False
        self.install_started = False
        self.setObjectName("contentPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        title = QLabel("Actualizaciones de Sentry")
        title.setObjectName("formTitle")
        layout.addWidget(title)
        self.status = QLabel(f"Versión instalada: {APP_VERSION}")
        self.status.setWordWrap(True)
        self.status.setObjectName("pageSubtitle")
        layout.addWidget(self.status)
        self.automatic = QCheckBox("Buscar nuevas versiones automáticamente")
        self.automatic.setChecked(window.database.settings().get("updates_enabled", "1") != "0")
        self.automatic.toggled.connect(self._automatic_changed)
        layout.addWidget(self.automatic)
        controls = QHBoxLayout()
        self.check_button = QPushButton("Buscar actualizaciones")
        self.check_button.setObjectName("secondaryButton")
        self.check_button.clicked.connect(lambda: self.check(manual=True))
        self.install_button = QPushButton("Descargar actualización")
        self.install_button.setObjectName("primaryButton")
        self.install_button.clicked.connect(self._action)
        self.install_button.setEnabled(False)
        controls.addWidget(self.check_button)
        controls.addWidget(self.install_button)
        controls.addStretch()
        layout.addLayout(controls)
        note = QLabel("Los datos de este equipo se conservan. Antes de instalar se verifica la descarga "
                      "y se crea un respaldo de la base. El reinicio requiere tu confirmación.")
        note.setWordWrap(True)
        note.setObjectName("pageSubtitle")
        layout.addWidget(note)
        self.timer = QTimer(self)
        self.timer.setInterval(self.CHECK_INTERVAL * 1000)
        self.timer.timeout.connect(self._automatic_check)
        if getattr(sys, "frozen", False) and not os.environ.get("SENTRY_DISABLE_UPDATE_CHECK"):
            self.timer.start()
            QTimer.singleShot(10_000, self._automatic_check)

    def _automatic_changed(self, checked):
        try:
            self.window.database.save_settings({"updates_enabled": "1" if checked else "0"})
        except Exception as exc:
            self.status.setText(f"No se pudo guardar la preferencia: {exc}")

    def _automatic_check(self):
        if not self.automatic.isChecked() or self.worker is not None:
            return
        try:
            previous = float(self.window.database.settings().get("updates_last_checked", "0"))
        except (TypeError, ValueError):
            previous = 0
        if time.time() - previous >= self.CHECK_INTERVAL:
            self.check(manual=False)

    def check(self, manual=True):
        if self.worker is not None:
            return
        self.manual_check = manual
        self.status.setText("Consultando las versiones publicadas en GitHub…")
        self._start("check")

    def _start(self, mode):
        self.worker = UpdateWorker(mode, self.window.database, self.release, self)
        self.worker.succeeded.connect(self._succeeded)
        self.worker.failed.connect(self._failed)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._finished)
        self.check_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.worker.start()

    def _progress(self, done, total):
        self.status.setText(f"Descargando actualización: {done / 1048576:.1f} de {total / 1048576:.1f} MB")

    def _succeeded(self, result):
        if self.closing or self.worker is None:
            return
        mode = self.worker.mode
        if mode == "check":
            try:
                self.window.database.save_settings({"updates_last_checked": str(time.time())})
            except Exception as exc:
                self._failed(f"No se pudo guardar la comprobación: {exc}")
                return
            if result is None:
                self.status.setText(f"Sentry {APP_VERSION} está actualizado en su canal de versiones.")
                self.release, self.downloaded = None, None
                return
            if self.release is None or self.release.version != result.version:
                self.downloaded = None
            self.release = result
            self.status.setText(f"Disponible Sentry {result.version}. Puedes descargarlo sin perder tus datos.")
            self.status.setToolTip(result.notes[:4000])
            self.window._show_toast(f"Hay una actualización: Sentry {result.version}. Ábrela en Configuración.")
        elif mode == "download":
            self.downloaded = Path(result)
            self.status.setText("Descarga verificada. Pulsa Instalar y reiniciar cuando termines el trabajo actual.")
        else:
            try:
                launch_installer(self.downloaded, self.release, Path(sys.executable).resolve().parent,
                                 APP_STORAGE_ROOT / "updates" / "installation.log", parent_pid=os.getpid())
            except Exception as exc:
                self._failed(f"No se pudo iniciar el instalador. Respaldo conservado en {result}. {exc}")
                return
            self.install_started = True
            self.status.setText("Respaldo guardado. Cerrando Sentry para instalar la actualización…")
            # La señal finished libera primero el hilo; el instalador espera este PID.

    def _failed(self, error):
        self.status.setText(f"No se completó la actualización: {error}")
        if self.worker is not None and self.worker.mode == "check":
            # Evita ciclos agresivos cuando no hay red o GitHub limita las consultas.
            try:
                self.window.database.save_settings({"updates_last_checked": str(time.time())})
            except Exception:
                pass
        self.window.setEnabled(True)

    def _finished(self):
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.deleteLater()
        self.check_button.setEnabled(True)
        self.install_button.setEnabled(self.release is not None and getattr(sys, "frozen", False))
        self.install_button.setText("Instalar y reiniciar" if self.downloaded else "Descargar actualización")
        if self.release and not getattr(sys, "frozen", False):
            self.status.setText(f"Disponible {self.release.version}. La instalación automática se usa desde el .exe instalado.")
        if self.closing or self.install_started:
            QTimer.singleShot(0, self.window.close)

    def _work_is_active(self):
        return any(getattr(self.window, name, None) is not None for name in (
            "analysis_worker", "local_scan_worker", "issabel_match_worker", "winscp_worker",
            "remote_search_worker", "report_export_worker",
        )) or self.window.bases_page.busy or self.window.reports_page.worker is not None

    def _action(self):
        if self.worker is not None or self.release is None or not getattr(sys, "frozen", False):
            return
        if self.downloaded is None:
            self.status.setText("Descargando y verificando el instalador…")
            self._start("download")
            return
        if self._work_is_active():
            self.status.setText("Termina o detén el análisis, la búsqueda y las exportaciones antes de instalar.")
            return
        install_dir = Path(sys.executable).resolve().parent
        if self.window.database.path.is_relative_to(install_dir) or APP_STORAGE_ROOT.is_relative_to(install_dir):
            self.status.setText("Los datos están dentro de la carpeta del programa. Se requiere migrarlos antes de actualizar.")
            return
        answer = QMessageBox.question(
            self.window, "Actualizar Sentry",
            f"¿Instalar Sentry {self.release.version} y reiniciar?\n\n"
            "Se conservarán las bases, grabaciones, configuraciones e historial de este equipo. "
            "Primero se creará una copia de seguridad de la base de datos.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self._work_is_active():
            self.status.setText("Comenzó otra tarea. Termínala antes de instalar la actualización.")
            return
        try:
            self.window.database.save_settings({
                "audio_directory": self.window.config_directory.text().strip(),
                "nas_directory": self.window.nas_directory.text().strip(),
                "audio_source": self.window._source_key(),
                "keywords": self.window.keywords_input.text().strip(),
                "theme": self.window.theme,
                "analysis_parallelism": str(self.window.analysis_parallelism.currentData()),
            })
            self.window._persist_credentials()
            self.window._persist_remote_connection()
        except Exception as exc:
            self.status.setText(f"No se pudieron guardar los ajustes. No se instalará: {exc}")
            return
        self.window.setEnabled(False)
        self.status.setText("Creando y verificando el respaldo de la base local…")
        self._start("backup")

    def request_close(self):
        if self.worker is None:
            self.closing = False
            return False
        self.closing = True
        self.worker.requestInterruption()
        self.status.setText("Cancelando la operación de actualización antes de cerrar…")
        return True
