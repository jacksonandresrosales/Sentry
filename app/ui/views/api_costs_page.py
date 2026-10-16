"""Pantalla de consumo y costo estimado de las APIs."""
from __future__ import annotations

from datetime import date, timedelta
import html
import math
from pathlib import Path
import sqlite3

from PySide6.QtCore import QDate, QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QComboBox, QDateEdit, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy,
    QMessageBox, QPushButton, QScrollArea, QTabWidget, QTableWidgetItem, QToolTip,
    QVBoxLayout, QWidget)

from app.services.analytics import period_bounds
from app.services.api_cost_export import export_api_costs_excel
from app.services.api_costs import (PROVIDER_LABELS, RECOMMENDED_API_MODELS, SERVICE_LABELS,
                                    bucket_daily, load_cost_report, model_label)
from app.ui.theme import theme_colors
from app.ui.views.reports_page import ReportsPage


# Solo se listan en pantalla las solicitudes más recientes; los totales cubren todo el filtro.
EVENT_ROWS = 500

# Un color por modelo, estable entre períodos para que la leyenda no cambie de significado.
MODEL_COLORS = {
    ('deepgram', 'nova-3'): '#27953c',
    ('assemblyai', 'universal-2'): '#1b9aaa',
    ('assemblyai', 'universal-3-5-pro'): '#7a5ccc',
    ('gemini', 'gemini-3.5-flash'): '#4f79cf',
    ('deepseek', 'deepseek-flash'): '#d4882a',
    ('groq', 'openai/gpt-oss-120b'): '#d65a6f',
}
FALLBACK_COLORS = ('#8a8f98', '#a07850', '#5c8f7a', '#9a6aa8', '#6f86a6')


def model_color(provider: str, model: str) -> str:
    color = MODEL_COLORS.get((provider, model))
    if color:
        return color
    return FALLBACK_COLORS[sum(map(ord, f'{provider}/{model}')) % len(FALLBACK_COLORS)]


def money(value: float | None) -> str:
    """Importe legible: centavos para cifras normales y más decimales solo si hacen falta."""
    if value is None:
        return '—'
    if value == 0:
        return '$0.00'
    if abs(value) >= 1:
        return f'${value:,.2f}'
    if abs(value) >= 0.01:
        return f'${value:,.3f}'
    return f'${value:,.4f}' if abs(value) >= 0.0001 else '<$0.0001'


def money_table(value: float | None) -> str:
    """Cuatro decimales fijos para que las columnas de importes queden alineadas."""
    return '—' if value is None else f'${value:,.4f}'


def money_exact(value: float) -> str:
    return f'${value:,.6f}'


def duration_text(seconds: float | None) -> str:
    if not seconds:
        return '0 min'
    minutes = seconds / 60
    if minutes < 1:
        return f'{seconds:.0f} s'
    if minutes < 60:
        return f'{minutes:.1f} min'.replace('.0 min', ' min')
    hours, rest = divmod(round(minutes), 60)
    return f'{hours} h {rest:02d} min'


def tokens_text(value: float | None) -> str:
    value = value or 0
    if value >= 1_000_000:
        return f'{value / 1_000_000:.2f} M'
    if value >= 10_000:
        return f'{value / 1000:.0f} k'
    if value >= 1000:
        return f'{value / 1000:.1f} k'
    return f'{value:.0f}'


def nice_ceiling(value: float) -> float:
    """Máximo del eje en 1, 2, 2.5 o 5 × 10ⁿ para que las líneas guía caigan en cifras redondas."""
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        candidate = step * 10 ** exponent
        if candidate >= value * 1.0001:
            return candidate
    return 10 ** (exponent + 1)


class SortItem(QTableWidgetItem):
    """Celda que ordena por su valor numérico y no por el texto formateado."""

    def __init__(self, text: str, sort_value=None):
        super().__init__(text)
        self.sort_value = sort_value

    def __lt__(self, other):
        # Sin super(): llamar al __lt__ de C++ desde Python cierra la app en PySide6 6.12.
        theirs = getattr(other, 'sort_value', None)
        if self.sort_value is not None and theirs is not None:
            return self.sort_value < theirs
        return self.text().casefold() < other.text().casefold()


class CostWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, database, start, end, service, provider, parent):
        super().__init__(parent)
        self.database = database
        self.start_date, self.end_date = start, end
        self.service, self.provider = service, provider

    def run(self):
        try:
            self.ready.emit(load_cost_report(self.database, self.start_date, self.end_date,
                                             self.service, self.provider, event_limit=EVENT_ROWS))
        except (sqlite3.Error, OSError, ValueError) as exc:
            self.failed.emit(str(exc))


class CostExportWorker(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, database, output, start, end, service, provider, parent):
        super().__init__(parent)
        self.database, self.output = database, Path(output)
        self.start_date, self.end_date = start, end
        self.service, self.provider = service, provider

    def run(self):
        try:
            path = export_api_costs_excel(self.database, self.output, self.start_date,
                                          self.end_date, self.service, self.provider)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(str(path))


def _theme(widget):
    return theme_colors(getattr(widget.window(), 'theme', None))


class ShareBar(QWidget):
    """Barra horizontal de participación en el gasto, con pista de fondo."""

    def __init__(self, share: float, color: str, parent=None):
        super().__init__(parent)
        self.share, self.color = max(0.0, min(1.0, share)), color
        self.setFixedHeight(8)
        self.setMinimumWidth(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_theme(self)['border']))
        track = QRectF(0, 0, self.width(), self.height())
        painter.drawRoundedRect(track, 4, 4)
        if self.share > 0:
            painter.setBrush(QColor(self.color))
            painter.drawRoundedRect(QRectF(0, 0, max(self.height(), self.width() * self.share),
                                           self.height()), 4, 4)
        painter.end()


class CostChart(QWidget):
    TRANSCRIPTION_COLOR = '#27953c'
    ANALYSIS_COLOR = '#4f79cf'
    LEFT, RIGHT, TOP, BOTTOM = 64, 12, 14, 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.granularity = 'day'
        self.today = date.today().isoformat()
        self.hovered_index = None
        self.setMinimumHeight(240)
        self.setMouseTracking(True)
        self.setAccessibleName('Gasto estimado por día y modelo, en dólares')

    def set_rows(self, rows, granularity='day'):
        self.rows, self.granularity = rows, granularity
        self.today = date.today().isoformat()
        self.hovered_index = None
        self.update()

    def _slot_width(self):
        return (self.width() - self.LEFT - self.RIGHT) / max(1, len(self.rows))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = _theme(self)
        baseline = self.height() - self.BOTTOM
        plot_height = max(1, baseline - self.TOP)
        maximum = nice_ceiling(max((row['total_usd'] for row in self.rows), default=0))
        font = painter.font()
        font.setPixelSize(10)
        painter.setFont(font)
        # Líneas guía y etiquetas del eje vertical.
        for step in range(5):
            value = maximum * step / 4
            y = baseline - plot_height * step / 4
            painter.setPen(QPen(QColor(colors['border_strong'] if step == 0 else colors['border']), 1))
            painter.drawLine(QPointF(self.LEFT, y), QPointF(self.width() - self.RIGHT, y))
            painter.setPen(QColor(colors['muted']))
            painter.drawText(QRectF(0, y - 8, self.LEFT - 8, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             money(value) if step else '$0')
        width = self._slot_width()
        label_every = max(1, math.ceil(len(self.rows) / max(1, (self.width() - self.LEFT) // 46)))
        for index, row in enumerate(self.rows):
            x = self.LEFT + index * width
            end = row.get('end', row['date'])
            is_future = row['date'] > self.today
            is_today = row['date'] <= self.today <= end
            if index == self.hovered_index:
                painter.fillRect(QRectF(x, self.TOP, width, plot_height), QColor(colors['surface_hover']))
            bar_width = max(3, min(30, width * .66))
            bar_x = x + (width - bar_width) / 2
            top = baseline
            # Apila los modelos del más barato (abajo) al más caro para que la cima destaque.
            segments = [model for model in reversed(row['models']) if model['cost_usd'] > 0]
            heights = [plot_height * model['cost_usd'] / maximum for model in segments]
            visible = sum(heights)
            if 0 < visible < 2:
                heights = [h * 2 / visible for h in heights]
            for model, height in zip(segments, heights):
                painter.fillRect(QRectF(bar_x, top - height, bar_width, height),
                                 QColor(model_color(model['provider'], model['model'])))
                top -= height
            if visible:
                # Remata la barra con esquinas suaves sin alterar el área proporcional.
                cap = QPainterPath()
                cap.addRoundedRect(QRectF(bar_x, top, bar_width, min(4, baseline - top)), 2, 2)
                painter.fillPath(cap, QColor(model_color(segments[-1]['provider'], segments[-1]['model'])))
            if is_today and self.granularity == 'day':
                painter.setPen(QPen(QColor(colors['green_accessible']), 1, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(x + width / 2, self.TOP), QPointF(x + width / 2, baseline))
            if index % label_every == 0 or index == len(self.rows) - 1 or is_today:
                painter.setPen(QColor(colors['green_accessible'] if is_today else
                                      colors['placeholder'] if is_future else colors['muted']))
                painter.drawText(QRectF(x - 20, baseline + 6, width + 40, 18), Qt.AlignmentFlag.AlignCenter,
                                 'Hoy' if is_today and self.granularity == 'day' else row['label'])
        if not any(row['total_usd'] for row in self.rows):
            painter.setPen(QColor(colors['muted']))
            font.setPixelSize(12)
            painter.setFont(font)
            painter.drawText(QRectF(self.LEFT, self.TOP, self.width() - self.LEFT - self.RIGHT, plot_height),
                             Qt.AlignmentFlag.AlignCenter, 'Sin gasto registrado en este período')
        painter.end()

    @staticmethod
    def tooltip_for_day(row):
        requests = row['transcription_requests'] + row['analysis_requests']
        title = row['date'] if row.get('end', row['date']) == row['date'] else f"{row['date']} — {row['end']}"
        lines = [f"<b>{html.escape(title)}</b>",
                 f"Total estimado: <b>{money(row['total_usd'])}</b> · {requests} solicitudes",
                 f"Transcripción: {money(row['transcription_usd'])} · {row['transcription_requests']} solicitudes",
                 f"Análisis: {money(row['analysis_usd'])} · {row['analysis_requests']} solicitudes"]
        if row['unpriced']:
            lines.append(f"Sin estimación: {row['unpriced']} solicitudes")
        if row['models']:
            lines.append('<hr><b>Por modelo</b>')
            for model in row['models']:
                color = model_color(model['provider'], model['model'])
                name = html.escape(model_label(model['provider'], model['model']))
                detail = f"{money_exact(model['cost_usd'])} · {model['requests']} solicitudes"
                if model['unpriced']:
                    detail += f" · {model['unpriced']} sin estimación"
                lines.append(f"<span style='color:{color}'>■</span> {name}: {detail}")
        return '<br>'.join(lines)

    def mouseMoveEvent(self, event):
        width = self._slot_width()
        x = event.position().x()
        index = int((x - self.LEFT) / width) if width and x >= self.LEFT else -1
        if index >= len(self.rows):
            index = -1
        if index != (self.hovered_index if self.hovered_index is not None else -1):
            self.hovered_index = index if index >= 0 else None
            if self.hovered_index is None:
                QToolTip.hideText()
            else:
                QToolTip.showText(event.globalPosition().toPoint(),
                                  self.tooltip_for_day(self.rows[index]), self)
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hovered_index = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)


class ApiCostsPage(QScrollArea):
    PROVIDER_LABELS = PROVIDER_LABELS

    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.setObjectName('apiCostsPage')
        self.database, self.worker, self.export_worker, self.result = database, None, None, None
        self._pending_refresh = False
        with database.connect() as connection:
            self.observed_providers = {(row['service'], row['provider']) for row in connection.execute(
                'SELECT DISTINCT service,provider FROM api_usage')}
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.setWidget(content)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(28, 24, 28, 28)
        outer.setSpacing(16)
        title = QLabel('Costes de API')
        title.setObjectName('pageTitle')
        outer.addWidget(title)
        subtitle = QLabel('Cuánto gasta cada API de transcripción y análisis, calculado con el consumo real '
                          'de cada solicitud y las tarifas de referencia de Sentry.')
        subtitle.setObjectName('pageSubtitle')
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        toolbar = QFrame()
        toolbar.setObjectName('reportToolbar')
        controls = QHBoxLayout(toolbar)
        controls.setContentsMargins(16, 12, 16, 12)
        controls.setSpacing(12)
        self.period = QComboBox()
        for label, key in (('1 día', 'day'), ('1 semana', 'week'), ('1 mes', 'month'),
                           ('Todo el historial', 'all')):
            self.period.addItem(label, key)
        self.period.setCurrentIndex(2)
        self.date = QDateEdit(QDate.currentDate())
        self.date.setObjectName('costDate')
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat('dd/MM/yyyy')
        self.service = QComboBox()
        for label, key in (('Todos', ''), ('Transcripción', 'transcription'), ('Análisis', 'analysis')):
            self.service.addItem(label, key)
        self.provider = QComboBox()
        self._update_provider_options()
        self.refresh_button = QPushButton('Actualizar')
        self.refresh_button.setObjectName('secondaryButton')
        for label, control in (('Período', self.period), ('Fecha de referencia', self.date),
                               ('Servicio', self.service), ('Proveedor', self.provider)):
            group = QVBoxLayout()
            group.setSpacing(5)
            caption = QLabel(label)
            caption.setObjectName('fieldLabel')
            caption.setBuddy(control)
            control.setAccessibleName(label)
            group.addWidget(caption)
            group.addWidget(control)
            controls.addLayout(group)
        controls.addStretch()
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(self.refresh_button)
        self.export_button = QPushButton('Exportar Excel')
        self.export_button.setObjectName('validateButton')
        self.export_button.setToolTip('Exportar solicitudes, totales y tarifas del período y filtros elegidos')
        actions.addWidget(self.export_button)
        controls.addLayout(actions)
        controls.setAlignment(actions, Qt.AlignmentFlag.AlignBottom)
        outer.addWidget(toolbar)

        status_row = QHBoxLayout()
        self.range_label = QLabel()
        self.range_label.setObjectName('pageSubtitle')
        status_row.addWidget(self.range_label)
        status_row.addStretch()
        self.summary = QLabel('')
        self.summary.setObjectName('costSummary')
        status_row.addWidget(self.summary)
        outer.addLayout(status_row)
        self.export_status = QLabel('')
        self.export_status.setObjectName('apiStatus')
        self.export_status.setWordWrap(True)
        self.export_status.hide()
        outer.addWidget(self.export_status)

        self.metrics, self.metric_details = {}, {}
        cards = QGridLayout()
        cards.setHorizontalSpacing(12)
        for index, (key, caption) in enumerate((
                ('total_usd', 'Total estimado'), ('transcription_usd', 'Transcripción'),
                ('analysis_usd', 'Análisis'), ('per_call', 'Promedio por llamada'),
                ('monthly', 'Ritmo mensual'))):
            card = QFrame()
            card.setObjectName('reportMetric')
            box = QVBoxLayout(card)
            box.setContentsMargins(18, 14, 18, 14)
            box.setSpacing(2)
            label = QLabel(caption)
            label.setObjectName('fieldLabel')
            box.addWidget(label)
            value = QLabel('—')
            value.setObjectName('reportValueAccent' if key == 'total_usd' else 'reportValue')
            box.addWidget(value)
            detail = QLabel('')
            detail.setObjectName('costMetricDetail')
            detail.setWordWrap(True)
            box.addWidget(detail)
            self.metrics[key], self.metric_details[key] = value, detail
            cards.addWidget(card, 0, index)
            cards.setColumnStretch(index, 1)
        outer.addLayout(cards)

        # Costo por API: una fila por modelo con su participación, consumo y costo efectivo.
        api_panel = QFrame()
        api_panel.setObjectName('reportSection')
        api_layout = QVBoxLayout(api_panel)
        api_layout.setContentsMargins(18, 16, 18, 14)
        api_layout.setSpacing(10)
        api_title = QLabel('Costo por API')
        api_title.setObjectName('formTitle')
        api_layout.addWidget(api_title)
        api_hint = QLabel('Para comparar transcripción mira el costo por hora de audio; para análisis, '
                          'el costo por solicitud.')
        api_hint.setObjectName('pageSubtitle')
        api_hint.setWordWrap(True)
        api_layout.addWidget(api_hint)
        self.api_grid_host = QWidget()
        self.api_grid = QGridLayout(self.api_grid_host)
        self.api_grid.setContentsMargins(0, 4, 0, 0)
        self.api_grid.setHorizontalSpacing(18)
        self.api_grid.setVerticalSpacing(0)
        api_layout.addWidget(self.api_grid_host)
        outer.addWidget(api_panel)

        panel = QFrame()
        panel.setObjectName('reportSection')
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 16, 18, 12)
        self.chart_title = QLabel('Gasto diario por modelo')
        self.chart_title.setObjectName('formTitle')
        panel_layout.addWidget(self.chart_title)
        self.legend_host = QWidget()
        self.legend = QHBoxLayout(self.legend_host)
        self.legend.setContentsMargins(0, 2, 0, 2)
        self.legend.setSpacing(14)
        panel_layout.addWidget(self.legend_host)
        self.chart = CostChart()
        panel_layout.addWidget(self.chart)
        chart_hint = QLabel('Pasa el cursor sobre una barra para ver el detalle por modelo.')
        chart_hint.setObjectName('costMetricDetail')
        panel_layout.addWidget(chart_hint)
        outer.addWidget(panel)

        section = QFrame()
        section.setObjectName('reportSection')
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(18, 16, 18, 16)
        breakdown_title = QLabel('Detalle del período')
        breakdown_title.setObjectName('formTitle')
        section_layout.addWidget(breakdown_title)
        self.tabs = QTabWidget()
        self.tabs.setObjectName('reportTabs')
        self.calls = ReportsPage.table(['Grabación', 'Solicitudes', 'Audio', 'Transcripción', 'Análisis',
                                        'Total'], 300, 0)
        self.events = ReportsPage.table(['Fecha', 'Servicio', 'Modelo', 'Grabación', 'Consumo',
                                         'Costo'], 300, 3)
        self.tabs.addTab(self.calls, 'Por llamada')
        self.tabs.addTab(self.events, 'Solicitudes')
        section_layout.addWidget(self.tabs)
        outer.addWidget(section)
        note = QLabel('Los importes son estimados con tarifas públicas de pago por uso. Deepgram y AssemblyAI '
                      'Universal-3.5 Pro suman Keyterms cuando se envían términos; Universal-2 lo incluye. '
                      'DeepSeek aplica sus franjas pico y fuera de pico en UTC. '
                      'Las respuestas reutilizadas desde caché no cuentan como solicitud. La factura puede '
                      'diferir por planes, créditos, impuestos o cambios de tarifa; en el plan gratis de Groq '
                      'puede no haber cargo. “Sin estimación” indica un dato de uso o tarifa faltante.')
        note.setObjectName('costMetricDetail')
        note.setWordWrap(True)
        outer.addWidget(note)
        outer.addStretch()
        for control in (self.period, self.date, self.provider):
            signal = control.dateChanged if control is self.date else control.currentIndexChanged
            signal.connect(self.refresh)
        self.service.currentIndexChanged.connect(self._service_changed)
        self.refresh_button.clicked.connect(self.refresh)
        self.export_button.clicked.connect(self.export_excel)

    def _update_provider_options(self):
        service_filter = self.service.currentData()
        selected = self.provider.currentData()
        current = [(service, provider) for service, providers in RECOMMENDED_API_MODELS.items()
                   for provider in providers if not service_filter or service == service_filter]
        legacy = sorted((service, provider) for service, provider in self.observed_providers
                        if (not service_filter or service == service_filter) and
                        (service, provider) not in current)
        self.provider.blockSignals(True)
        self.provider.clear()
        self.provider.addItem('Todos', '')
        added = set()
        for _service, provider in current + legacy:
            if provider in added:
                continue
            label = self.PROVIDER_LABELS.get(provider, provider.title())
            self.provider.addItem(label + (' (histórico)' if (_service, provider) in legacy else ''), provider)
            added.add(provider)
        self.provider.setCurrentIndex(max(0, self.provider.findData(selected)))
        self.provider.blockSignals(False)

    def _service_changed(self):
        self._update_provider_options()
        self.refresh()

    def _selected_bounds(self):
        reference = self.date.date().toPython()
        if self.period.currentData() != 'all':
            return period_bounds(reference, self.period.currentData())
        with self.database.connect() as connection:
            bounds = connection.execute(
                "SELECT MIN(date(created_at,'localtime')), MAX(date(created_at,'localtime')) FROM api_usage"
            ).fetchone()
        first = date.fromisoformat(bounds[0]) if bounds[0] else reference
        last = date.fromisoformat(bounds[1]) if bounds[1] else reference
        return first, max(last, reference) + timedelta(days=1)

    def _set_controls_enabled(self, enabled):
        for control in (self.period, self.service, self.provider, self.refresh_button):
            control.setEnabled(enabled)
        self.date.setEnabled(enabled and self.period.currentData() != 'all')

    def refresh(self):
        if self.worker is not None or self.export_worker is not None:
            # Repite la consulta al terminar para no perder un cambio de filtro.
            self._pending_refresh = self.worker is not None
            return
        self._pending_refresh = False
        start, end = self._selected_bounds()
        self.range_label.setText(f'{start:%d/%m/%Y} — {end-timedelta(days=1):%d/%m/%Y} · hora local')
        self.summary.setText('Cargando consumo…')
        self._set_controls_enabled(False)
        self.export_button.setEnabled(False)
        self.worker = CostWorker(self.database, start, end, self.service.currentData(),
                                 self.provider.currentData(), self)
        self.worker.ready.connect(self.render)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def finished(self):
        self.worker.deleteLater()
        self.worker = None
        self._set_controls_enabled(True)
        self.export_button.setEnabled(self.export_worker is None)
        if self._pending_refresh:
            self.refresh()

    def export_excel(self):
        if self.export_worker is not None or self.worker is not None:
            return
        start, end = self._selected_bounds()
        suggested = f'Sentry_Costes_API_{start:%Y%m%d}_{end-timedelta(days=1):%Y%m%d}.xlsx'
        path, _ = QFileDialog.getSaveFileName(self, 'Exportar costes de API', suggested,
                                               'Excel (*.xlsx)')
        if not path:
            return
        self._set_controls_enabled(False)
        self.export_button.setEnabled(False)
        self.export_button.setText('Exportando…')
        self._show_export_status('Preparando el Excel con todas las solicitudes del período…')
        self.export_worker = CostExportWorker(self.database, path, start, end,
                                              self.service.currentData(), self.provider.currentData(), self)
        self.export_worker.succeeded.connect(self._export_succeeded)
        self.export_worker.failed.connect(self._export_failed)
        self.export_worker.finished.connect(self._export_finished)
        self.export_worker.start()

    def _show_export_status(self, text):
        self.export_status.setText(text)
        self.export_status.setVisible(bool(text))

    def _export_succeeded(self, path):
        self._show_export_status(f'Excel guardado: {path}')
        self.export_status.setToolTip(path)

    def _export_failed(self, message):
        self._show_export_status(f'No se pudo exportar el Excel: {message}')
        QMessageBox.warning(self, 'Exportación fallida', message)

    def _export_finished(self):
        self.export_worker.deleteLater()
        self.export_worker = None
        self._set_controls_enabled(True)
        self.export_button.setText('Exportar Excel')
        self.export_button.setEnabled(self.worker is None)

    def failed(self, message):
        self.result = None
        for key, value in self.metrics.items():
            value.setText('—')
            self.metric_details[key].setText('')
        self._clear_layout(self.api_grid)
        self._clear_layout(self.legend)
        self.calls.setRowCount(0)
        self.events.setRowCount(0)
        self.chart.set_rows([])
        QToolTip.hideText()
        self.summary.setText(f'No se pudo cargar el consumo: {message}')

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                ApiCostsPage._clear_layout(item.layout())

    def _elapsed_days(self, result):
        """Días transcurridos del período (sin contar días futuros) para proyectar el ritmo."""
        today = date.today().isoformat()
        days = [row for row in result['daily'] if row['date'] <= today]
        if self.period.currentData() == 'all':
            first = next((index for index, row in enumerate(days) if row['total_usd'] or
                          row['transcription_requests'] or row['analysis_requests']), None)
            days = days[first:] if first is not None else []
        return max(1, len(days))

    def render(self, result):
        self.result = result
        calls = result['calls']
        self.metrics['total_usd'].setText(money(result['total_usd']))
        self.metric_details['total_usd'].setText(
            f"{result['requests']} solicitudes · {calls} llamadas")
        self.metrics['transcription_usd'].setText(money(result['transcription_usd']))
        self.metric_details['transcription_usd'].setText(f"{duration_text(result['audio_seconds'])} de audio")
        self.metrics['analysis_usd'].setText(money(result['analysis_usd']))
        self.metric_details['analysis_usd'].setText(
            f"{tokens_text(result['input_tokens'])} tokens entrada · {tokens_text(result['output_tokens'])} salida")
        self.metrics['per_call'].setText(money(result['total_usd'] / calls) if calls else '—')
        hours = result['audio_seconds'] / 3600
        self.metric_details['per_call'].setText(
            f"Transcripción {money(result['transcription_usd'] / hours)}/h de audio" if hours else
            'Transcripción + análisis')
        daily_average = result['total_usd'] / self._elapsed_days(result)
        self.metrics['monthly'].setText(money(daily_average * 30) if result['requests'] else '—')
        self.metric_details['monthly'].setText(
            f"{money(daily_average)} al día en promedio" if result['requests'] else 'Sin consumo aún')
        for key, value in self.metrics.items():
            for widget in (value, self.metric_details[key]):
                widget.setToolTip(money_exact(result[key]) if key in result and
                                  isinstance(result[key], float) else '')
        self.summary.setText(
            f"{result['unpriced']} solicitudes sin estimación (falta tarifa o dato de uso)"
            if result['unpriced'] else '' if result['requests'] else
            'Aún no hay solicitudes registradas en este período.')
        self._render_api_rows(result)
        granularity, rows = bucket_daily(result['daily'])
        self.chart_title.setText({'day': 'Gasto diario por modelo', 'week': 'Gasto semanal por modelo',
                                  'month': 'Gasto mensual por modelo'}[granularity])
        self.chart.set_rows(rows, granularity)
        QToolTip.hideText()
        self.chart.setAccessibleDescription('; '.join(
            f"{row['date']}: {money(row['total_usd'])}" for row in rows if row['total_usd']))
        self._render_legend(result)
        self._render_tables(result)

    def _render_legend(self, result):
        self._clear_layout(self.legend)
        for item in result['by_model']:
            if not item['cost_usd']:
                continue
            label = QLabel(f"<span style='color:{model_color(item['provider'], item['model'])}'>●</span> "
                           f"{html.escape(item['label'])}")
            label.setObjectName('costLegend')
            self.legend.addWidget(label)
        self.legend.addStretch()

    def _render_api_rows(self, result):
        grid = self.api_grid
        self._clear_layout(grid)
        headers = ('API y modelo', 'Participación', 'Gasto', 'Solicitudes', 'Consumo', 'Costo efectivo',
                   'Tarifa base')
        for column, text in enumerate(headers):
            label = QLabel(text)
            label.setObjectName('fieldLabel')
            if column >= 2:
                label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(label, 0, column)
        grid.setColumnStretch(1, 1)
        if not result['by_model']:
            empty = QLabel('Cuando Sentry transcriba o analice llamadas, aquí verás el gasto de cada API.')
            empty.setObjectName('pageSubtitle')
            grid.addWidget(empty, 1, 0, 1, len(headers))
            return
        row = 1
        current_service = None
        for item in result['by_model']:
            if item['service'] != current_service:
                current_service = item['service']
                service_total = sum(entry['cost_usd'] for entry in result['by_model']
                                    if entry['service'] == current_service)
                group = QLabel(f"{SERVICE_LABELS.get(current_service, current_service)} · {money(service_total)}")
                group.setObjectName('costGroup')
                grid.addWidget(group, row, 0, 1, len(headers))
                row += 1
            color = model_color(item['provider'], item['model'])
            name = QLabel(f"<span style='color:{color}'>●</span>&nbsp; {html.escape(item['label'])}")
            name.setObjectName('costModelName')
            name.setToolTip(f"{item['provider']} · {item['model']}")
            share_box = QWidget()
            share_layout = QHBoxLayout(share_box)
            share_layout.setContentsMargins(0, 0, 0, 0)
            share_layout.setSpacing(8)
            share_layout.addWidget(ShareBar(item['share'], color))
            percent = QLabel(f"{item['share'] * 100:.0f}%" if item['share'] >= .005 or not item['share']
                             else '<1%')
            percent.setObjectName('costCell')
            percent.setMinimumWidth(34)
            percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            share_layout.addWidget(percent)
            if item['service'] == 'transcription':
                consumption = duration_text(item['audio_seconds'])
                effective = f"{money(item['usd_per_hour'])}/h" if item['usd_per_hour'] is not None else '—'
            else:
                consumption = (f"{tokens_text(item['input_tokens'])} ent. · "
                               f"{tokens_text(item['output_tokens'])} sal.")
                effective = (f"{money(item['usd_per_request'])}/sol."
                             if item['usd_per_request'] is not None else '—')
            requests = str(item['requests'])
            if item['unpriced']:
                requests += f" ({item['unpriced']} sin est.)"
            cells = [name, share_box]
            for text, object_name, tip in (
                    (money(item['cost_usd']), 'costAmount', money_exact(item['cost_usd'])),
                    (requests, 'costCell', ''), (consumption, 'costCell', ''),
                    (effective, 'costCell', 'Gasto dividido entre horas de audio' if
                     item['service'] == 'transcription' else 'Gasto dividido entre solicitudes con estimación'),
                    (item['rate_label'] or 'Sin tarifa', 'costMuted', '')):
                label = QLabel(text)
                label.setObjectName(object_name)
                label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if tip:
                    label.setToolTip(tip)
                cells.append(label)
            for column, widget in enumerate(cells):
                widget.setMinimumHeight(34)
                grid.addWidget(widget, row, column)
            row += 1

    def _render_tables(self, result):
        calls = [((item['filename'], None), (str(item['requests']), item['requests']),
                  (duration_text(item['audio_seconds']) if item['audio_seconds'] else '—', item['audio_seconds']),
                  (money_table(item['transcription_usd']), item['transcription_usd']),
                  (money_table(item['analysis_usd']), item['analysis_usd']),
                  (money_table(item['cost_usd']) + (' *' if item['unpriced'] else ''), item['cost_usd']))
                 for item in result['by_call'][:EVENT_ROWS]]
        events = []
        for item in result['events']:
            if item['audio_seconds'] is not None:
                consumption = duration_text(item['audio_seconds'])
            elif item['input_tokens'] is not None or item['output_tokens'] is not None:
                consumption = (f"{tokens_text(item['input_tokens'])} ent. · "
                               f"{tokens_text(item['output_tokens'])} sal.")
                if item['cached_input_tokens']:
                    consumption += f" ({tokens_text(item['cached_input_tokens'])} caché)"
            else:
                consumption = 'Sin datos'
            cost = item['cost_usd']
            events.append(((item['local_at'][:16], item['local_at']),
                           (SERVICE_LABELS.get(item['service'], item['service']), None),
                           (model_label(item['provider'], item['model']), None),
                           (item['filename'] or 'Llamada eliminada', None), (consumption, None),
                           (money_table(cost) if cost is not None else 'Sin estimación',
                            cost if cost is not None else -1)))
        # Por defecto: llamadas más caras primero y solicitudes más recientes primero.
        for table, rows, sort_column in ((self.calls, calls, 5), (self.events, events, 0)):
            table.setUpdatesEnabled(False)
            table.setSortingEnabled(False)
            table.setRowCount(len(rows))
            for index, row in enumerate(rows):
                tip = ' · '.join(text for text, _ in row)
                for column, (text, sort_value) in enumerate(row):
                    cell = SortItem(text, sort_value)
                    cell.setToolTip(tip)
                    if sort_value is not None and column:
                        cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    table.setItem(index, column, cell)
            for column, (_text, sort_value) in enumerate(rows[0] if rows else ()):
                if sort_value is not None and column:
                    table.horizontalHeaderItem(column).setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setSortingEnabled(True)
            table.sortItems(sort_column, Qt.SortOrder.DescendingOrder)
            table.setUpdatesEnabled(True)
        total = result['requests']
        self.tabs.setTabText(1, f'Solicitudes ({min(total, EVENT_ROWS)} de {total})'
                             if total > EVENT_ROWS else f'Solicitudes ({total})')
        self.tabs.setTabText(0, f"Por llamada ({result['calls']})")
