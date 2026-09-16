"""Las filas filtradas visibles deben tener tarjeta sin depender de un clic."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication

from app.ui.views.main_window import CallCard, CallRecord, SentryWindow, TranscriptLine, install_ui_font


class CallCardScrollingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        install_ui_font(cls.app)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.window = SentryWindow(Path(self.temporary.name) / "synthetic-audit.db")
        self.window.show()
        self.flush_events()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.flush_events()
        self.temporary.cleanup()

    def flush_events(self):
        # Procesa layout, los singleShot(0) y la liberación de tarjetas retiradas.
        for _ in range(4):
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def load_calls(self, count, alert_ids):
        alerts = set(alert_ids)
        transcript = (TranscriptLine(0, "Cliente", "Texto sintético de prueba."),)
        self.window.call_records = [
            CallRecord(
                index, f"synthetic-recording-{index}.wav", "", "099***0000", "12:00:00", 30,
                "Alerta" if index in alerts else "Normal", index in alerts,
                "denuncia" if index in alerts else "Normal", 0 if index in alerts else None,
                "Alta" if index in alerts else "Baja", "Resumen sintético", "Texto sintético",
                transcript, category_code="ALERTA" if index in alerts else "NORMAL",
            )
            for index in range(1, count + 1)
        ]
        self.window.calls = {call.call_id: call for call in self.window.call_records}
        self.window._populate_call_list()
        self.window.call_list.setCurrentRow(0)
        self.flush_events()

    def set_list_height(self, height):
        self.window.call_list.setFixedHeight(height)
        self.window.resize(1280, max(800, height + 300))
        self.flush_events()

    def visible_items(self):
        listing = self.window.call_list
        bounds = listing.viewport().rect()
        return [
            item for row in range(listing.count())
            if not (item := listing.item(row)).isHidden()
            and not (rect := listing.visualItemRect(item)).isEmpty()
            and rect.intersects(bounds)
        ]

    def assert_visible_cards(self):
        items = self.visible_items()
        self.assertTrue(items, "La prueba debe mostrar al menos una llamada.")
        missing = [
            item.data(Qt.ItemDataRole.UserRole) for item in items
            if not isinstance(self.window.call_list.itemWidget(item), CallCard)
        ]
        self.assertEqual(missing, [], f"Filas visibles sin CallCard: {missing}; no debe requerirse seleccionarlas.")
        for item in items:
            call_id = item.data(Qt.ItemDataRole.UserRole)
            self.assertIs(self.window.call_cards[call_id], self.window.call_list.itemWidget(item))
        # El límite admite precarga alrededor de la ventana y una fila seleccionada
        # fuera de pantalla, pero no crear una tarjeta por cada resultado del lote.
        bound = max(64, len(items) + 32)
        self.assertLessEqual(len(self.window.call_cards), bound)
        self.assertLessEqual(len(self.window.call_list.findChildren(CallCard)), bound)

    def test_sparse_complaints_in_short_list_have_cards_even_when_bottom_is_blank(self):
        self.load_calls(1000, {500, 900})
        self.window.status_filter.setCurrentIndex(self.window.status_filter.findData("alert"))
        for theme in ("light", "dark"):
            self.window.theme_combo.setCurrentIndex(self.window.theme_combo.findData(theme))
            for height in (300, 478, 760):
                with self.subTest(theme=theme, height=height):
                    self.set_list_height(height)
                    self.assertEqual(
                        [item.data(Qt.ItemDataRole.UserRole) for item in self.visible_items()], [500, 900]
                    )
                    self.assertEqual(self.window.selected_call_id, 500)
                    self.assert_visible_cards()

    def test_sparse_complaints_scroll_both_directions_without_raw_rows_or_unbounded_cards(self):
        alerts = set(range(25, 5001, 37)) | {500, 900}
        self.load_calls(5000, alerts)
        self.window.status_filter.setCurrentIndex(self.window.status_filter.findData("alert"))
        for height in (240, 475, 708):
            self.set_list_height(height)
            scrollbar = self.window.call_list.verticalScrollBar()
            self.assertGreater(scrollbar.maximum(), 0)
            positions = [0, scrollbar.maximum() // 3, scrollbar.maximum() // 2, scrollbar.maximum()]
            for position in positions + list(reversed(positions)):
                with self.subTest(height=height, position=position):
                    scrollbar.setValue(position)
                    self.flush_events()
                    self.assert_visible_cards()

    def test_growing_viewport_materializes_newly_visible_rows_without_scroll_or_selection(self):
        self.set_list_height(240)
        self.load_calls(200, set())
        self.window.call_list.verticalScrollBar().setValue(0)
        self.flush_events()
        self.assert_visible_cards()
        previous_visible = len(self.visible_items())
        selected = self.window.selected_call_id

        # Un viewport de más de veinte filas supera la precarga antigua. La
        # notificación de resize debe cubrirlo sin cambiar el valor del scroll.
        self.set_list_height(3000)
        self.assertGreater(len(self.visible_items()), previous_visible + 20)
        self.assertEqual(self.window.call_list.verticalScrollBar().value(), 0)
        self.assertEqual(self.window.selected_call_id, selected)
        self.assert_visible_cards()

    def test_search_empty_results_category_changes_and_sort_refresh_visible_cards(self):
        self.set_list_height(475)
        self.load_calls(1000, {500, 900})
        self.window.status_filter.setCurrentIndex(self.window.status_filter.findData("alert"))
        self.window.search_input.setText("synthetic-recording-900.wav")
        self.flush_events()
        self.assertEqual(
            [item.data(Qt.ItemDataRole.UserRole) for item in self.visible_items()], [900]
        )
        self.assert_visible_cards()

        self.window.search_input.setText("no-existing-synthetic-call")
        self.flush_events()
        self.assertEqual(self.visible_items(), [])
        self.assertEqual(self.window.call_count.text(), "0 llamadas")
        self.assertLessEqual(len(self.window.call_cards), 1)

        self.window.status_filter.setCurrentIndex(self.window.status_filter.findData("normal"))
        self.window.search_input.clear()
        self.flush_events()
        self.assert_visible_cards()
        self.window._sort_calls("name", "nombre")
        self.flush_events()
        self.assert_visible_cards()
        scrollbar = self.window.call_list.verticalScrollBar()
        for position in (scrollbar.maximum(), 0):
            scrollbar.setValue(position)
            self.flush_events()
            self.assert_visible_cards()

        self.window.status_filter.setCurrentIndex(self.window.status_filter.findData("alert"))
        self.flush_events()
        self.assertEqual(
            [item.data(Qt.ItemDataRole.UserRole) for item in self.visible_items()], [500, 900]
        )
        self.assert_visible_cards()


if __name__ == "__main__":
    unittest.main()
