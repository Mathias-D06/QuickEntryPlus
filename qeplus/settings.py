"""Settings in %APPDATA%\\QuickEntryPlus\\settings.json, reloaded when the file is saved.

A missing file is created with the defaults. A file that isn't valid JSON is never overwritten:
the defaults are used and the caller is told, so a typo can't wipe the user's edits. A value of
the wrong type or out of range falls back to its default, with a warning.
"""

from __future__ import annotations

import copy
import json
import logging
import os

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from .hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, parse
from .keyhook import ptt_vk

DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "QuickEntryPlus")
PATH = os.path.join(DIR, "settings.json")
DEFAULTS = {
    "toolbar_position": "auto",  # auto (right of the pop-up, below it if no room) | right | below
    "popup_shortcut": "Ctrl+Alt+Space",  # Claude's quick entry shortcut, to reopen the pop-up
    "ask_hotkey": "Ctrl+Alt+Shift+Space",  # opens the answer card's own input bar
    "voice_autosend": False,  # send a dictated question as soon as it is transcribed
    "hotkeys": {"screenshot": "Alt+S", "window": "Alt+W", "voice": "Alt+M"},
    "voice_engine": "windows",   # windows (Win+H voice typing) | gemini
    "gemini_model": "gemini-3.8-flash",
    "gemini_fallback_model": "gemini-3.1-flash-lite",
    "transcript_cleanup": True,  # remove filler words and false starts (Gemini only)
    "push_to_talk": False,       # hold the key to dictate a question to the answer card
    "push_to_talk_key": "CapsLock",  # Shift+Caps Lock still toggles Caps Lock
    "double_tap": True,          # double-tap the key below to open the answer card
    "double_tap_key": "Ctrl",    # Ctrl | Alt
    "recent_sessions": True,     # keep the last 10 answer-card conversations (sessions.json)
    "open_claude_hotkey": "Ctrl+Alt+Shift+C",  # focus Claude's main window; "" turns it off
    "ask_selection_hotkey": "Ctrl+Alt+Shift+Q",  # ask about the selection; "" turns it off
    "clipboard_chip": True,      # offer to attach something copied in the last 30 seconds
    "animations": True,          # also off when Windows' Animation effects setting is off
}
CHOICES = {"toolbar_position": ("auto", "right", "below"), "voice_engine": ("windows", "gemini"),
           "double_tap_key": ("Ctrl", "Alt")}
OPTIONAL_HOTKEYS = ("open_claude_hotkey", "ask_selection_hotkey")  # "" turns these off
log = logging.getLogger(__name__)


def altgr_clash(combo: str) -> bool:
    """Ctrl+Alt+<letter or digit> without Shift is AltGr+key on many non-English layouts
    (Ctrl+Alt+Q types @ on a German keyboard), so registering it takes that character away."""
    mods, vk = parse(combo)
    return (mods & (MOD_CONTROL | MOD_ALT | MOD_SHIFT) == MOD_CONTROL | MOD_ALT
            and (ord("0") <= vk <= ord("9") or ord("A") <= vk <= ord("Z")))


class Notice(str):
    """A problem whose value was kept anyway (a warning, not a fallback to the default)."""


def validate(raw: dict) -> tuple[dict, list[str]]:
    """Merge raw over the defaults; returns (settings, problems). Unknown keys are ignored."""
    out, problems = copy.deepcopy(DEFAULTS), []
    for key, default in DEFAULTS.items():
        if key not in raw:
            continue
        value = raw[key]
        if key == "hotkeys":
            if not isinstance(value, dict):
                problems.append("hotkeys must be an object")
                continue
            for action, combo in value.items():
                if action not in default:
                    problems.append(f"unknown hotkey action {action!r}")
                    continue
                if not isinstance(combo, str):
                    problems.append(f"hotkey {action} should be text like \"Alt+S\" or \"\"")
                    continue
                try:
                    if combo:  # "" turns that hotkey off
                        parse(combo)
                    out[key][action] = combo
                except ValueError as e:
                    problems.append(f"hotkey {action}: {e}")
        elif key in ("popup_shortcut", "ask_hotkey") + OPTIONAL_HOTKEYS:
            try:
                if value or key not in OPTIONAL_HOTKEYS:
                    parse(value)
                out[key] = value
                if value and key != "popup_shortcut" and altgr_clash(value):  # kept, but warned
                    problems.append(Notice(
                        f"{key}: {value.strip()} is AltGr+{value.strip()[-1]} on many "
                        "keyboard layouts and blocks typing that character; add Shift"))
            except (ValueError, AttributeError, TypeError) as e:
                problems.append(f"{key}: {e}")
        elif key == "push_to_talk_key":
            try:
                ptt_vk(value)
                out[key] = value
            except (ValueError, AttributeError) as e:
                problems.append(f"{key}: {e}")
        elif type(value) is not type(default) or (key in CHOICES and value not in CHOICES[key]):
            allowed = " | ".join(CHOICES[key]) if key in CHOICES else type(default).__name__
            problems.append(f"{key} should be {allowed}, not {value!r}")
        elif isinstance(value, str) and not value.strip():
            problems.append(f"{key} is empty")
        else:
            out[key] = value
    return out, problems


class Settings(QObject):
    """settings.values is the current dict; changed fires after a reload."""

    changed = Signal()
    problem = Signal(str)  # a message for the user

    def __init__(self, path: str = PATH):
        super().__init__()
        self.path = path
        self.values = copy.deepcopy(DEFAULTS)

    def start(self) -> None:
        """Load, then watch the file. Separate from __init__ so problem() has listeners."""
        self.load()
        self.watcher = QFileSystemWatcher([self.path], self)
        # editors save in several steps (and some replace the file): settle, then reload once
        self.debounce = QTimer(self, singleShot=True, interval=300)
        self.debounce.timeout.connect(self._reload)
        self.watcher.fileChanged.connect(lambda _: self.debounce.start())

    def __getitem__(self, key):
        return self.values[key]

    def load(self) -> None:
        if not os.path.exists(self.path):
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(DEFAULTS, f, indent=2)
            log.info("created %s", self.path)
        try:
            with open(self.path, encoding="utf-8-sig") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("the file must contain a JSON object")
        except (OSError, ValueError) as e:
            log.warning("settings unreadable: %s", e)
            self.problem.emit(f"settings.json has an error ({e}). Using the defaults.")
            self.values = copy.deepcopy(DEFAULTS)
            return
        self.values, problems = validate(raw)
        for p in problems:
            log.warning("settings: %s", p)
        errors = [p for p in problems if not isinstance(p, Notice)]
        notices = [p for p in problems if isinstance(p, Notice)]
        if errors:
            self.problem.emit("settings.json: " + "; ".join(errors) + ". Using the default "
                              "for those.")
        if notices:
            self.problem.emit("settings.json: " + "; ".join(notices) + ".")

    def _reload(self) -> None:
        if self.path not in self.watcher.files() and os.path.exists(self.path):
            self.watcher.addPath(self.path)  # re-watch a file the editor replaced
        old = self.values
        self.load()
        if self.values != old:
            log.info("settings reloaded")
            self.changed.emit()
