"""La rueda desplaza la página; los menús solo cambian con un clic."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QDateEdit, QWidget


class IdleWheelGuard(QObject):
    """Ignora la rueda sobre selectores cerrados para no cambiar proveedor, modelo o fecha."""

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if event.type() != QEvent.Type.Wheel:
            return False
        control = _choice_control(watched)
        if control is None or _popup_open(control):
            return False
        return True


def _choice_control(watched) -> QWidget | None:
    current = watched if isinstance(watched, QWidget) else None
    while current is not None:
        if isinstance(current, (QComboBox, QAbstractSpinBox)):
            return current
        current = current.parentWidget()
    return None


def _popup_open(control: QWidget) -> bool:
    if isinstance(control, QComboBox):
        view = control.view()
        return view is not None and view.isVisible()
    if isinstance(control, QDateEdit):
        calendar = control.calendarWidget()
        return calendar is not None and calendar.isVisible()
    return False


def install_idle_wheel_guard(app) -> None:
    """Instala el filtro una sola vez sobre la aplicación."""
    if app is None or app.findChild(IdleWheelGuard, "sentryIdleWheelGuard") is not None:
        return
    guard = IdleWheelGuard(app)
    guard.setObjectName("sentryIdleWheelGuard")
    app.installEventFilter(guard)
