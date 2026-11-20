"""Drag-to-select screen capture: a dimmed overlay on every screen; Esc or right-click cancels.

The overlay is live (not a frozen frame): it takes focus, which closes Claude's pop-up, and the
region is grabbed after the overlay is gone, so neither the pop-up nor the overlay is captured.
shortcut: a selection stays on one screen; upgrade to one virtual-desktop overlay if spanning
screens is wanted.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .toolbar import ACCENT

DIM = QColor(0, 0, 0, 96)


class _Overlay(QWidget):
    def __init__(self, snipper: "Snipper", screen):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.snipper, self.screen_ = snipper, screen
        self.origin: QPoint | None = None
        self.selection = QRect()
        self.closing = False  # set when the snipper closes it; anything else is a cancel
        self.setGeometry(screen.geometry())

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), DIM)
        if not self.selection.isNull():
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p.fillRect(self.selection, QColor(0, 0, 0, 1))  # alpha 1, not 0: keeps the mouse
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            pen = QPen(ACCENT, 1)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.drawRect(self.selection.adjusted(0, 0, -1, -1))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.snipper.cancel()
        elif e.button() == Qt.MouseButton.LeftButton:
            self.origin = e.position().toPoint()
            self.selection = QRect(self.origin, self.origin)

    def mouseMoveEvent(self, e):
        if self.origin is not None:
            self.selection = QRect(self.origin, e.position().toPoint()).normalized() \
                .intersected(self.rect())  # dragging onto another screen stops at the edge
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.origin is not None:
            self.origin = None
            if self.selection.width() < 4 or self.selection.height() < 4:
                self.selection = QRect()  # a click, not a drag: let the user try again
                self.update()
            else:
                self.snipper.finish(self.screen_, self.selection)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.snipper.cancel()

    def closeEvent(self, e):
        if not self.closing:  # Alt+F4, or Windows closing it: never leave the app waiting
            QTimer.singleShot(0, self.snipper.cancel)
        super().closeEvent(e)


class Snipper(QObject):
    captured = Signal(QImage)
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.overlays: list[_Overlay] = []

    def start(self) -> None:
        self.overlays = [_Overlay(self, s) for s in QGuiApplication.screens()]
        for o in self.overlays:
            o.show()
        under_cursor = QGuiApplication.screenAt(QCursor.pos())
        target = next((o for o in self.overlays if o.screen_ is under_cursor), self.overlays[0])
        target.raise_()
        target.activateWindow()  # focus: Esc works, and Claude's pop-up closes

    def _close(self) -> None:
        for o in self.overlays:
            o.closing = True
            o.close()
            o.deleteLater()
        self.overlays = []

    def cancel(self) -> None:
        if not self.overlays:  # already finished or cancelled
            return
        self._close()
        self.cancelled.emit()

    def finish(self, screen, rect: QRect) -> None:
        self._close()
        # wait for the overlay to leave the screen before grabbing what's under it
        QTimer.singleShot(150, lambda: self.captured.emit(
            screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height()).toImage()))
