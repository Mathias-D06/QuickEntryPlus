"""Hand a captured image (plus the pop-up's draft text) to Claude.

The quick entry pop-up ignores pasted images (tested: Qt image, PNG + CF_DIB, CF_HDROP), so this
opens a new chat in the main window with Claude's own deep link and pastes there, but only after
UI Automation confirms keyboard focus is in a normal chat's message box. If that can't be
confirmed, nothing is typed or pasted: the image stays on the clipboard and the caller is told.
"""

from __future__ import annotations

import logging
import os
import time

from PySide6.QtCore import QMimeData, QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImage

from . import uia
from .win import VK_CONTROL, press, type_text, user32

NEW_CHAT = "claude://claude.ai/new?surface=chat&source=desktop_action"  # from Claude's app.asar
FIND_TIMEOUT = 6.0  # seconds; a new chat took 0.7 s in testing
log = logging.getLogger(__name__)
# Windows formats that keep clipboard content out of Win+V history and Cloud Clipboard sync
PRIVATE_FORMATS = {"ExcludeClipboardContentFromMonitorProcessing": b"\0\0\0\0",
                   "CanIncludeInClipboardHistory": b"\0\0\0\0",
                   "CanUploadToCloudClipboard": b"\0\0\0\0"}


def put_on_clipboard(image: QImage | None = None, text: str | None = None) -> int:
    """Put a capture or a transcript on the clipboard, kept out of clipboard history and cloud
    sync. Returns the clipboard sequence number, to tell later whether anything replaced it."""
    data = QMimeData()
    if image is not None:
        data.setImageData(image)
    if text is not None:
        data.setText(text)
    for name, value in PRIVATE_FORMATS.items():
        data.setData(f'application/x-qt-windows-mime;value="{name}"', value)
    QGuiApplication.clipboard().setMimeData(data)
    return user32.GetClipboardSequenceNumber()


class Deliverer(QObject):
    done = Signal(bool, str)  # ok, message for the user when not ok

    def __init__(self):
        super().__init__()
        self.timer = QTimer(self, interval=100)
        self.timer.timeout.connect(self._poll)
        self.draft, self.deadline, self.clip_seq = "", 0.0, 0
        self.active = False  # cleared by cancel(): a pending poll or paste then does nothing

    def start(self, image: QImage, draft: str) -> None:
        self.draft = " ".join(draft.split())  # the pop-up is single-line; never type a newline
        if image.isNull():
            self._fail("The capture came out empty.", on_clipboard=False)
            return
        self.clip_seq = put_on_clipboard(image)
        try:
            os.startfile(NEW_CHAT)
        except OSError as e:
            log.warning("opening a new chat failed: %s", e)
            self._fail("Couldn't open a new Claude chat.")
            return
        self.deadline = time.monotonic() + FIND_TIMEOUT
        self.active = True
        self.timer.start()

    def cancel(self) -> None:
        """Pause: stop waiting for the chat box; nothing is typed or pasted."""
        self.active = False
        self.timer.stop()

    def _guard(self) -> bool:
        try:
            return uia.chat_box_focused()
        except Exception as e:  # UIA calls cross into Claude's process and can fail mid-load
            log.debug("UIA check failed: %s", e)
            return False

    def _poll(self) -> None:
        if not self.active:
            self.timer.stop()
            return
        if not self._guard():
            if time.monotonic() > self.deadline:
                self.timer.stop()
                self._fail("Couldn't find Claude's chat box.")
            return
        self.timer.stop()
        if self.draft:
            type_text(self.draft + " ")
        QTimer.singleShot(200, self._paste)  # let the typed text land before the paste

    def _paste(self) -> None:
        if not self.active:
            return
        self.active = False
        if not self._guard():  # focus moved while typing: don't paste somewhere else
            self._fail("Focus left Claude's chat box.")
            return
        if user32.GetClipboardSequenceNumber() != self.clip_seq:
            # something else was copied meanwhile: pasting now would send that to Claude
            self._fail("The clipboard changed before pasting, so nothing was pasted.",
                       on_clipboard=False)
            return
        press(VK_CONTROL, ord("V"))
        log.info("pasted screenshot into a new chat (draft: %d chars)", len(self.draft))
        self.done.emit(True, "")

    def _fail(self, why: str, on_clipboard: bool = True) -> None:
        self.active = False
        log.warning("delivery failed: %s", why)
        where = " The screenshot is on your clipboard." if on_clipboard else ""
        extra = " Your text is still in the pop-up." if self.draft else ""
        self.done.emit(False, f"{why}{where}{extra}")
