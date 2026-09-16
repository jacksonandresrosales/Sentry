import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop,QPoint,QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QHeaderView
import openpyxl
from app.database import Database
from app.ui.views.main_window import SentryWindow


class ReportsUiTest(unittest.TestCase):
    def test_filters_real_empty_and_export(self):
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            database_path=Path(folder)/'test.db'
            database=Database(database_path)
            today=date.today().isoformat()
            with database.connect() as connection:
                connection.execute(
                    "INSERT INTO calls(id,filename,file_path,status,category,processed_at,reviewed,duration_seconds) "
                    "VALUES (1,'alerta.wav','alerta.wav','COMPLETADO','ALERTA',datetime(?,'utc'),1,60),"
                    "(2,'normal.wav','normal.wav','COMPLETADO','NORMAL',datetime(?,'utc'),0,45)",
                    (f'{today} 12:00:00',f'{today} 13:00:00'),
                )
                connection.execute(
                    "INSERT INTO keyword_hits(call_id,keyword,timestamp_seconds,is_risk_validated) "
                    "VALUES (1,'queja',10,1)"
                )
            database.record_analysis_base(1,'base.xlsx')
            database.record_analysis_base(2,'base.xlsx')
            window=SentryWindow(database_path)
            window.show()
            app.processEvents()
            page=window.reports_page
            def wait():
                loop=QEventLoop()
                page.worker.finished.connect(loop.quit)
                QTimer.singleShot(10000,loop.quit)
                loop.exec()
                app.processEvents()
                self.assertIsNone(page.worker)
            window._switch_page('reports')
            wait()
            self.assertFalse(hasattr(page,'source'))
            self.assertFalse(hasattr(page,'demo'))
            self.assertEqual(page.result['total'],2)
            self.assertEqual(page.calls.horizontalHeader().sectionResizeMode(1), QHeaderView.ResizeMode.Stretch)
            self.assertEqual(page.calls.horizontalHeader().sectionResizeMode(0), QHeaderView.ResizeMode.ResizeToContents)
            self.assertIn('Clasificación:', page.calls.item(0, 0).toolTip())
            self.assertIn(page.calls.item(0, 1).text(), page.calls.item(0, 0).toolTip())
            QTest.mouseMove(page.chart, QPoint(20, 50))
            app.processEvents()
            self.assertEqual(page.chart.hovered_index, 0)
            for period,days in ((0,1),(2,30)):
                page.period.setCurrentIndex(period)
                wait()
                if period==0:
                    self.assertEqual(len(page.result['daily']),days)
                else:
                    self.assertGreaterEqual(len(page.result['daily']),28)
            destination=Path(folder)/'report.xlsx'
            with patch('app.ui.views.reports_page.QFileDialog.getSaveFileName',return_value=(str(destination),'Excel')):
                page.export_excel()
            book=openpyxl.load_workbook(destination,read_only=True)
            self.assertEqual(book['Resumen']['A1'].value,'Reporte Sentry')
            self.assertEqual(book['Llamadas'].max_row,page.result['total']+1)
            book.close()
            page.date.setDate(page.date.date().addYears(-1))
            wait()
            self.assertEqual(page.result['total'],0)
            self.assertEqual(page.calls.rowCount(),0)
            self.assertEqual(page.metrics['incidents'].text(),'0')
            window.close()
