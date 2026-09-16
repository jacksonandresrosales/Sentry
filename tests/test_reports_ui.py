import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop,QPoint,QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QHeaderView
import openpyxl
from app.ui.views.main_window import SentryWindow


class ReportsUiTest(unittest.TestCase):
    def test_filters_real_empty_and_export(self):
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            window=SentryWindow(Path(folder)/'test.db')
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
            self.assertTrue(page.demo)
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
            destination=Path(folder)/'example.xlsx'
            with patch('app.ui.views.reports_page.QFileDialog.getSaveFileName',return_value=(str(destination),'Excel')):
                page.export_excel()
            book=openpyxl.load_workbook(destination,read_only=True)
            self.assertEqual(book['Resumen']['A1'].value,'DATOS DE EJEMPLO')
            self.assertEqual(book['Llamadas'].max_row,page.result['total']+1)
            book.close()
            page.source.setCurrentIndex(0)
            wait()
            self.assertEqual(page.result['total'],0)
            self.assertEqual(page.calls.rowCount(),0)
            self.assertEqual(page.metrics['incidents'].text(),'0')
            self.assertFalse(page.demo)
            window.close()
