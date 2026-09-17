"""Reportes globales de los datos operativos guardados en SQLite."""
from datetime import timedelta
import html
from pathlib import Path
import sqlite3

from PySide6.QtCore import QDate, Qt, QThread, Signal, QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QComboBox, QDateEdit, QFrame, QScrollArea, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QTabWidget, QFileDialog,
    QToolTip)
import xlsxwriter

from app.services.analytics import period_bounds, load_report
from app.ui.theme import theme_colors


class ReportWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, database, start, end, parent):
        super().__init__(parent)
        self.database, self.start_date, self.end_date = database, start, end

    def run(self):
        try:
            result = load_report(self.database, self.start_date, self.end_date)
            self.ready.emit(result)
        except (sqlite3.Error, OSError, ValueError) as exc:
            self.failed.emit(str(exc))


class ActivityChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.hovered_index = None
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self.setAccessibleName('Actividad diaria de llamadas')

    def paintEvent(self, event):
        painter = QPainter(self)
        colors = theme_colors(self.window().theme)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        maximum = max((max(total, incidents) for _, total, incidents in self.rows), default=0) or 1
        width = (self.width() - 30) / max(1,len(self.rows))
        height = self.height() - 40
        for i,(day,total,incidents) in enumerate(self.rows):
            x = 15 + i * width
            if i == self.hovered_index:
                painter.fillRect(QRectF(x, 0, width, height + 8), QColor(colors['surface_hover']))
            bar_width = min(36, width * .6)
            for count, color in ((total, colors['border_strong']), (incidents, colors['green_accessible'])):
                bar_height = height * count / maximum
                painter.fillRect(QRectF(x+(width-bar_width)/2, height-bar_height+5, bar_width,bar_height),QColor(color))
            painter.setPen(QColor(colors['text']))
            if len(self.rows) <= 7:
                painter.drawText(QRectF(x, 0, width, 20), Qt.AlignmentFlag.AlignCenter, str(total))
            painter.setPen(QColor(colors['muted']))
            if len(self.rows) <= 7 or i % 5 == 0 or i == len(self.rows)-1:
                painter.drawText(QRectF(x-8,height+10,width+16,22),Qt.AlignmentFlag.AlignCenter,day)
        painter.end()

    def mouseMoveEvent(self, event):
        width = (self.width() - 30) / max(1, len(self.rows))
        index = int((event.position().x() - 15) / width) if width else -1
        if 0 <= index < len(self.rows):
            self.hovered_index = index
            day, total, incidents = self.rows[index]
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"<b>{html.escape(day)}</b><br>{total} llamadas analizadas<br>"
                f"{incidents} incidentes, incluidas las denuncias verificadas manualmente",
                self,
            )
        else:
            self.hovered_index = None
            QToolTip.hideText()
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hovered_index = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)


class ReportsPage(QScrollArea):
    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.database, self.worker, self.result = database, None, None
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName('reportsPage')
        self.setWidget(content)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(28,24,28,28)
        outer.setSpacing(18)
        heading = QHBoxLayout()
        title = QLabel('Reportes y analítica')
        title.setObjectName('pageTitle')
        heading.addWidget(title)
        heading.addStretch()
        self.export = QPushButton('Exportar Excel')
        self.export.setObjectName('primaryButton')
        self.export.setEnabled(False)
        self.export.clicked.connect(self.export_excel)
        heading.addWidget(self.export)
        outer.addLayout(heading)
        subtitle = QLabel('Una visión global de todas las llamadas y bases guardadas en este equipo.')
        subtitle.setObjectName('pageSubtitle')
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)
        toolbar = QFrame()
        toolbar.setObjectName('reportToolbar')
        controls = QHBoxLayout(toolbar)
        controls.setContentsMargins(16, 12, 16, 12)
        controls.setSpacing(12)
        self.period = QComboBox()
        for label,key in [('1 día','day'),('1 semana','week'),('1 mes','month')]:
            self.period.addItem(label,key)
        self.period.setCurrentIndex(1)
        self.period.setAccessibleName('Período del reporte')
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setMinimumHeight(36)
        self.date.setMinimumWidth(140)
        self.date.setDisplayFormat('dd/MM/yyyy')
        self.date.setAccessibleName('Fecha de referencia')
        self.refresh_button = QPushButton('Actualizar')
        self.refresh_button.setObjectName('secondaryButton')
        for label, widget in (('Período', self.period), ('Fecha de referencia', self.date)):
            group = QVBoxLayout()
            group.setSpacing(5)
            caption = QLabel(label)
            caption.setObjectName('fieldLabel')
            group.addWidget(caption)
            group.addWidget(widget)
            controls.addLayout(group)
        controls.addStretch()
        controls.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignBottom)
        outer.addWidget(toolbar)
        self.range_label = QLabel()
        self.range_label.setObjectName('pageSubtitle')
        self.range_label.setWordWrap(True)
        outer.addWidget(self.range_label)
        self.metrics = {}
        self.metric_cards = {}
        metrics = QGridLayout()
        for i,(key,label) in enumerate((('completed','Llamadas analizadas'),('incidents','Incidentes'),
                                        ('verified','Denuncias verificadas'),('bases','Bases analizadas'))):
            panel = QFrame()
            panel.setObjectName('reportMetric')
            box = QVBoxLayout(panel)
            box.setContentsMargins(18,14,18,14)
            number = QLabel('—')
            number.setObjectName('reportValueAccent' if key=='incidents' else 'reportValue')
            self.metrics[key] = number
            self.metric_cards[key] = panel
            box.addWidget(number)
            caption=QLabel(label)
            caption.setObjectName('reportLabel')
            box.addWidget(caption)
            metrics.addWidget(panel,0,i)
        outer.addLayout(metrics)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)
        charts = QHBoxLayout()
        charts.setSpacing(14)
        activity_panel = QFrame()
        activity_panel.setObjectName('reportSection')
        activity = QVBoxLayout(activity_panel)
        activity.setContentsMargins(18, 16, 18, 14)
        activity_title = QLabel('Actividad diaria')
        activity_title.setObjectName('formTitle')
        activity.addWidget(activity_title)
        activity_hint = QLabel('Gris: analizadas · Verde: incidentes · Pasa el cursor sobre una barra')
        activity_hint.setObjectName('pageSubtitle')
        activity.addWidget(activity_hint)
        self.chart = ActivityChart()
        self.chart.setAccessibleName('Llamadas analizadas e incidentes por día')
        activity.addWidget(self.chart)
        charts.addWidget(activity_panel, 3)
        keywords_panel = QFrame()
        keywords_panel.setObjectName('reportSection')
        keywords = QVBoxLayout(keywords_panel)
        keywords.setContentsMargins(18, 16, 18, 14)
        keywords_title = QLabel('Palabras más encontradas')
        keywords_title.setObjectName('formTitle')
        keywords.addWidget(keywords_title)
        keywords_hint = QLabel('Menciones totales y llamadas distintas')
        keywords_hint.setObjectName('pageSubtitle')
        keywords.addWidget(keywords_hint)
        self.words = self.table(['Palabra o frase','Menciones','Llamadas'], 180, 0)
        keywords.addWidget(self.words)
        charts.addWidget(keywords_panel, 2)
        outer.addLayout(charts)
        records_panel = QFrame()
        records_panel.setObjectName('reportSection')
        records_layout = QVBoxLayout(records_panel)
        records_layout.setContentsMargins(18, 16, 18, 14)
        records_layout.setSpacing(10)
        records_title = QLabel('Registros del período')
        records_title.setObjectName('formTitle')
        records_layout.addWidget(records_title)
        records_hint = QLabel('Selecciona una categoría. Pasa el cursor por cualquier fila para ver su detalle completo.')
        records_hint.setObjectName('pageSubtitle')
        records_hint.setWordWrap(True)
        records_layout.addWidget(records_hint)
        self.tabs = QTabWidget()
        self.tabs.setObjectName('reportTabs')
        self.bases = self.table(['Base analizada','Último análisis','Llamadas','Incidentes'], 300, 0)
        self.calls = self.table(['Fecha','Grabación','Estado','Clasificación','Verificada'], 300, 1)
        self.incidents = self.table(['Fecha','Grabación','Palabras detectadas','Revisión'], 300, 1)
        self.jobs = self.table(['Base preparada','Fecha','Estado','Registros'], 300, 0)
        self.daily = self.table(['Día','Analizadas','Incidentes'], 300, 0)
        for table,label in ((self.bases,'Bases analizadas'),(self.calls,'Llamadas'),(self.incidents,'Incidentes'),
                            (self.jobs,'Bases preparadas'),(self.daily,'Detalle diario')):
            self.tabs.addTab(table,label)
        records_layout.addWidget(self.tabs)
        outer.addWidget(records_panel)
        note = QLabel('Se muestran hasta 500 filas por tabla; el Excel incluye todas. Cada llamada cuenta una vez según su último análisis. '
                      'Un incidente es una llamada clasificada como alerta. Las bases se vinculan desde esta actualización; los análisis anteriores pueden no tener una base asociada.')
        note.setObjectName('pageSubtitle')
        note.setWordWrap(True)
        outer.addWidget(note)
        outer.addStretch()
        self.period.currentIndexChanged.connect(self.refresh)
        self.date.dateChanged.connect(self.refresh)
        self.refresh_button.clicked.connect(self.refresh)

    @staticmethod
    def table(headers, height=260, stretch_column=0):
        table=QTableWidget(0,len(headers))
        table.setObjectName('reportTable')
        table.setProperty('stretchColumn', stretch_column)
        table.setHorizontalHeaderLabels(headers)
        header = table.horizontalHeader()
        header.setMinimumSectionSize(72)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for column in range(len(headers)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch if column == stretch_column else QHeaderView.ResizeMode.ResizeToContents)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(38)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setMouseTracking(True)
        table.setSortingEnabled(True)
        table.setAlternatingRowColors(True)
        table.setMinimumHeight(height)
        table.setMaximumHeight(height)
        table.setAccessibleName(' · '.join(headers))
        return table

    def refresh(self):
        if self.worker is not None:
            return
        self.start_date,self.end_date=period_bounds(self.date.date().toPython(),self.period.currentData())
        self.range_label.setText(f'{self.start_date:%d/%m/%Y} — {self.end_date-timedelta(days=1):%d/%m/%Y} · '
                                 'Semana de lunes a domingo; mes calendario. Fechas de análisis en hora local.')
        self.summary.setText('Cargando analítica…')
        for control in (self.period,self.date,self.refresh_button,self.export):
            control.setEnabled(False)
        self.worker=ReportWorker(self.database,self.start_date,self.end_date,self)
        self.worker.ready.connect(self.render)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def finished(self):
        self.worker.deleteLater()
        self.worker=None
        for control in (self.period,self.date,self.refresh_button):
            control.setEnabled(True)
        self.export.setEnabled(self.result is not None)

    def failed(self,message):
        self.result=None
        for value in self.metrics.values():
            value.setText('—')
        for table in (self.words,self.bases,self.calls,self.incidents,self.jobs,self.daily):
            table.setRowCount(0)
        self.chart.rows=[]
        self.chart.update()
        self.summary.setText(f'No se pudo cargar el reporte: {message}. Pulsa Actualizar para reintentar.')

    def render(self,result):
        self.result=result
        for key,label in self.metrics.items():
            label.setText(str(len(result['bases']) if key=='bases' else result[key]))
        metric_help = {
            'completed': f"{result['completed']} llamadas terminaron el análisis en el período seleccionado.",
            'incidents': f"{result['incidents']} llamadas fueron clasificadas como alerta sensible.",
            'verified': f"{result['verified']} incidentes fueron confirmados manualmente.",
            'bases': f"{len(result['bases'])} bases quedaron vinculadas a llamadas analizadas en este período.",
        }
        for key, panel in self.metric_cards.items():
            panel.setToolTip(metric_help[key])
        evaluated=result.get('evaluated',result['completed'])
        rate=result['incidents']/evaluated*100 if evaluated else 0
        self.summary.setText(f"{result['total']} llamadas registradas · {result['normal']} normales · {result['mailbox']} buzones · "
                             f"{result['pending']} pendientes · {result['errors']} con error · {result['seconds']/60:.0f} min analizados · "
                             f"{rate:.1f}% de incidentes entre llamadas analizadas o verificadas" if result['total'] else
                             'No hay llamadas registradas en este período. Cambia la fecha o el período para consultar otro intervalo.')
        self.chart.setAccessibleDescription('; '.join(f'{day}: {total} analizadas, {alerts} incidentes' for day,total,alerts in result['daily']))
        self.chart.rows=result['daily']
        self.chart.update()
        self.rows = {
            'Palabras': result['keywords'],
            'Bases analizadas': [(Path(b['base_path']).name,b['last_analysis'],b['calls'],b['incidents']) for b in result['bases']],
            'Llamadas': [(c['occurred_at'],c['filename'],c['status'],c['category'],'Sí' if c['reviewed'] else 'No') for c in result['calls']],
            'Incidentes': [(c['occurred_at'],c['filename'],c['keywords'],'Verificada' if c['reviewed'] else 'Pendiente')
                          for c in result['calls'] if (c['status']=='COMPLETADO' or c['reviewed']) and c['category']=='ALERTA'],
            'Bases preparadas': [(Path(j['output_path']).name if j['output_path'] else f"Base #{j['id']}",j['created_at'],j['status'],j['unique_count']) for j in result['jobs']],
            'Detalle diario': result['daily'],
        }
        self.tables = {'Palabras':self.words,'Bases analizadas':self.bases,'Llamadas':self.calls,
                       'Incidentes':self.incidents,'Bases preparadas':self.jobs,'Detalle diario':self.daily}
        for key,table in self.tables.items():
            rows=self.rows[key][:500]
            table.setSortingEnabled(False)
            table.setRowCount(len(rows))
            for i,row in enumerate(rows):
                tooltip = self._row_tooltip(key, row)
                for j,value in enumerate(row):
                    item=QTableWidgetItem()
                    item.setData(Qt.ItemDataRole.DisplayRole, value)
                    item.setToolTip(tooltip)
                    if j != table.property('stretchColumn'):
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    table.setItem(i,j,item)
            table.setSortingEnabled(True)
            sort_column, sort_order = {
                'Palabras': (1, Qt.SortOrder.DescendingOrder),
                'Bases analizadas': (1, Qt.SortOrder.DescendingOrder),
                'Llamadas': (0, Qt.SortOrder.DescendingOrder),
                'Incidentes': (0, Qt.SortOrder.DescendingOrder),
                'Bases preparadas': (1, Qt.SortOrder.DescendingOrder),
                'Detalle diario': (0, Qt.SortOrder.AscendingOrder),
            }[key]
            table.sortItems(sort_column, sort_order)

    @staticmethod
    def _row_tooltip(section, row):
        values = [html.escape(str(value)) for value in row]
        if section == 'Palabras':
            return f"<b>{values[0]}</b><br>{values[1]} menciones en {values[2]} llamadas distintas."
        if section == 'Bases analizadas':
            return f"<b>{values[0]}</b><br>Último análisis: {values[1]}<br>{values[2]} llamadas · {values[3]} incidentes"
        if section == 'Llamadas':
            return f"<b>{values[1]}</b><br>Fecha: {values[0]}<br>Estado: {values[2]} · Clasificación: {values[3]}<br>Verificada: {values[4]}"
        if section == 'Incidentes':
            terms = values[2] or 'Sin palabras asociadas'
            return f"<b>{values[1]}</b><br>Fecha: {values[0]}<br>Palabras: {terms}<br>Revisión: {values[3]}"
        if section == 'Bases preparadas':
            return f"<b>{values[0]}</b><br>Fecha: {values[1]}<br>Estado: {values[2]} · {values[3]} registros"
        return f"<b>{values[0]}</b><br>{values[1]} llamadas analizadas · {values[2]} incidentes"

    def export_excel(self):
        path,_=QFileDialog.getSaveFileName(self,'Exportar analítica',f'Sentry_{self.start_date}.xlsx','Excel (*.xlsx)')
        if not path:
            return
        try:
            with xlsxwriter.Workbook(path,{'strings_to_formulas':False,'strings_to_urls':False}) as book:
                header=book.add_format({'bold':True,'bg_color':'#EEF7EF'})
                summary=book.add_worksheet('Resumen')
                summary.write_row(0,0,['Reporte Sentry',str(self.start_date),str(self.end_date-timedelta(days=1))],header)
                for i,key in enumerate(('total','completed','incidents','verified','normal','mailbox','pending','errors','seconds'),2):
                    summary.write_row(i,0,[{'total':'Llamadas registradas','completed':'Llamadas analizadas','incidents':'Incidentes',
                        'verified':'Denuncias verificadas','normal':'Normales','mailbox':'Buzones','pending':'Pendientes',
                        'errors':'Con error','seconds':'Segundos analizados'}[key],self.result[key]])
                summary.set_column(0,2,24)
                for name,rows in self.rows.items():
                    sheet=book.add_worksheet(name)
                    table=self.tables[name]
                    sheet.write_row(0,0,[table.horizontalHeaderItem(i).text() for i in range(table.columnCount())],header)
                    for i,row in enumerate(rows,1):
                        sheet.write_row(i,0,row)
                    sheet.freeze_panes(1,0)
                    sheet.set_column(0,table.columnCount()-1,26)
                    if rows:
                        sheet.autofilter(0,0,len(rows),table.columnCount()-1)
            self.summary.setText(f'Informe guardado: {path}')
        except (OSError,xlsxwriter.exceptions.XlsxWriterException) as exc:
            self.summary.setText(f'No se pudo exportar el informe: {exc}')
