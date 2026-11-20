"""UI Automation checks on Claude's windows (read-only).

Chromium builds its accessibility tree only after a UIA client first asks, so the first query
after start-up can come back empty; callers poll. Every call crosses into Claude's process, so
the client gets short timeouts: a hung Claude must not freeze the toolbar.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import comtypes
import comtypes.client

from .win import is_main_window, user32

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402

CONNECTION_TIMEOUT_MS, TRANSACTION_TIMEOUT_MS = 1000, 2000
MAX_SELECTION = 65_536  # characters read from a selection: a huge Ctrl+A can't stall the UI
TERMINAL_HINTS = ("terminal", "xterm", "console")  # in an AutomationId or class name
_auto = None


def _automation():
    global _auto
    if _auto is None:
        try:  # IUIAutomation2 (Windows 8+) is the interface that has timeouts
            _auto = comtypes.client.CreateObject(UIA.CUIAutomation8,
                                                 interface=UIA.IUIAutomation2)
            _auto.ConnectionTimeout = CONNECTION_TIMEOUT_MS
            _auto.TransactionTimeout = TRANSACTION_TIMEOUT_MS
        except (AttributeError, OSError):
            _auto = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
    return _auto


def focused_textbox_url() -> str | None:
    """URL of the page whose text box has keyboard focus, or None if focus is not in a text box."""
    auto = _automation()
    e = auto.GetFocusedElement()
    if not e or e.CurrentAriaRole != "textbox":
        return None
    walker = auto.ControlViewWalker
    while e:
        if e.CurrentControlType == UIA.UIA_DocumentControlTypeId:
            return str(e.GetCurrentPropertyValue(UIA.UIA_ValueValuePropertyId) or "")
        e = walker.GetParentElement(e)
    return None


def focused_text() -> str:
    """Text in the focused text box (e.g. the pop-up's draft), or "" if focus is elsewhere."""
    e = _automation().GetFocusedElement()
    if not e or e.CurrentAriaRole != "textbox":
        return ""
    return str(e.GetCurrentPropertyValue(UIA.UIA_ValueValuePropertyId) or "")


def focused_selection() -> tuple[str | None, str]:
    """(selected text, kind) for the focused element. The text comes from its TextPattern, so
    no keys are sent and the clipboard isn't touched; None if it has none, at most
    MAX_SELECTION characters. kind is "password"; "terminal" when its AutomationId or class
    says so (VS Code's terminal is an xterm-helper-textarea; a tab called terminal.py isn't);
    "unknown" when those can't be read; or ""."""
    e = _automation().GetFocusedElement()
    if not e:
        return None, ""
    try:
        if e.CurrentIsPassword:
            return None, "password"
        ident = f"{e.CurrentAutomationId or ''} {e.CurrentClassName or ''}".lower()
    except (comtypes.COMError, AttributeError):
        return None, "unknown"  # can't tell what it is: don't guess that Ctrl+C is safe
    kind = "terminal" if any(h in ident for h in TERMINAL_HINTS) else ""
    try:
        pattern = e.GetCurrentPattern(UIA.UIA_TextPatternId)
        if not pattern:
            return None, kind
        ranges = pattern.QueryInterface(UIA.IUIAutomationTextPattern).GetSelection()
        if not ranges:
            return None, kind
        parts, budget = [], MAX_SELECTION
        for i in range(ranges.Length):
            if budget <= 0:
                break
            part = ranges.GetElement(i).GetText(budget) or ""
            parts.append(part)
            budget -= len(part)
    except (comtypes.COMError, AttributeError):
        return None, kind
    text = "".join(parts)[:MAX_SELECTION]
    return (text if text.strip() else None), kind


def is_new_chat_url(url: str | None) -> bool:
    """claude.ai/new exactly (any query string): a fresh chat, never an existing one."""
    if not url:
        return False
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname == "claude.ai" and parts.path == "/new"


def chat_box_focused() -> bool:
    """True only when Claude Desktop's main window is in front and keyboard focus is in the
    message box of a new chat (so a browser tab, or an existing chat that hasn't navigated
    yet, never qualifies)."""
    return (is_main_window(user32.GetForegroundWindow())
            and is_new_chat_url(focused_textbox_url()))
