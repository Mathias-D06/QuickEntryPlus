"""Tray icon and menu, the start-with-Windows shortcut, and the single-instance guard."""

from __future__ import annotations

import ctypes
import os
import sys

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import __version__
from .toolbar import ACCENT

STARTUP_DIR = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs",
                           "Startup")
SHORTCUT = "Quick Entry Plus.lnk"


def icon(paused: bool = False) -> QIcon:
    """Drawn at several sizes so the tray picks a sharp one at any scaling."""
    result = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64):
        result.addPixmap(draw(size, paused))
    return result


def draw(size: int, paused: bool = False) -> QPixmap:
    """A rounded square with a '+', clay when active, grey when paused."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), size * 0.26, size * 0.26)
    p.fillPath(path, QColor("#8a8780") if paused else ACCENT)
    pen = QPen(QColor("white"), max(1.5, size / 9))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    c, r = size / 2, size * 0.24
    p.drawLine(QPointF(c - r, c), QPointF(c + r, c))
    p.drawLine(QPointF(c, c - r), QPointF(c, c + r))
    p.end()
    return pm


def launch_command() -> tuple[str, str, str]:
    """(target, arguments, working dir) that start this app without a console window."""
    if getattr(sys, "frozen", False):  # the PyInstaller exe
        return sys.executable, "", os.path.dirname(sys.executable)
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return pythonw, "-m qeplus", root


def starts_with_windows(folder: str = STARTUP_DIR) -> bool:
    return os.path.exists(os.path.join(folder, SHORTCUT))


def set_starts_with_windows(enabled: bool, folder: str = STARTUP_DIR) -> None:
    path = os.path.join(folder, SHORTCUT)
    if not enabled:
        if os.path.exists(path):
            os.remove(path)
        return
    import win32com.client

    target, args, workdir = launch_command()
    link = win32com.client.Dispatch("WScript.Shell").CreateShortcut(path)
    link.TargetPath, link.Arguments, link.WorkingDirectory = target, args, workdir
    link.Description = "Quick Entry Plus: toolbar for Claude's quick entry pop-up"
    link.Save()


def already_running(name: str = "QuickEntryPlus.SingleInstance") -> bool:
    """True if another copy holds the named mutex. The handle is kept for the process lifetime."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    already_running.handle = kernel32.CreateMutexW(None, False, name)
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


class Tray(QSystemTrayIcon):
    pause_toggled = Signal(bool)
    open_settings = Signal()
    open_log = Signal()
    quit_requested = Signal()

    def __init__(self):
        super().__init__(icon())
        self.setToolTip(f"Quick Entry Plus {__version__}")
        menu = QMenu()
        self.pause_action = QAction("Pause", menu, checkable=True)
        self.pause_action.toggled.connect(self._paused)
        self.startup_action = QAction("Start with Windows", menu, checkable=True,
                                      checked=starts_with_windows())
        self.startup_action.toggled.connect(self._startup)
        menu.addAction(self.pause_action)
        menu.addAction(self.startup_action)
        menu.addSeparator()
        menu.addAction("Open settings file", self.open_settings.emit)
        menu.addAction("Open log", self.open_log.emit)
        menu.addSeparator()
        menu.addAction("Quit", self.quit_requested.emit)
        self.menu = menu  # keep a reference: the tray doesn't own it
        self.setContextMenu(menu)
        # a left click toggles pause, like the menu item
        self.activated.connect(lambda reason: self.pause_action.toggle()
                               if reason == QSystemTrayIcon.ActivationReason.Trigger else None)

    def _paused(self, paused: bool) -> None:
        self.setIcon(icon(paused))
        self.setToolTip(f"Quick Entry Plus {__version__}" + (" (paused)" if paused else ""))
        self.pause_toggled.emit(paused)

    def _startup(self, enabled: bool) -> None:
        try:
            set_starts_with_windows(enabled)
        except Exception:
            self.startup_action.blockSignals(True)
            self.startup_action.setChecked(starts_with_windows())
            self.startup_action.blockSignals(False)
            raise  # reaches the app's error handler: logged and shown as a toast
