"""Quick Entry Plus: wires the pop-up watcher, the docked toolbar and the actions together."""

from __future__ import annotations

import ctypes
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication, QIcon, QImage
from PySide6.QtWidgets import QApplication, QMenu

from . import __version__, sessions, uia, voice
from . import toolbar as toolbar_module
from .askbar import AskWindow, Glyph
from .capture import frame_rect, screen_image, window_image
from .claude_cli import ATTACHMENTS, ClaudeSession
from .deliver import NEW_CHAT, Deliverer, put_on_clipboard
from .hotkeys import Hotkeys, parse
from .keyhook import TAP_KEYS, KeyHook, SessionWatch, ptt_vk
from .settings import Settings
from .tray import Tray, already_running
from .snip import Snipper
from .toolbar import MicIndicator, Toast, Toolbar
from .watcher import PopupWatcher, is_app_window
from .win import (VK_BACK, VK_CONTROL, VK_ESCAPE, VK_LWIN, clipboard_is_private, exe_name,
                  exe_path, find_windows, input_desktop_ok, is_elevated, is_main_window,
                  monitor_right, normal_area, press, rect, restore_clipboard,
                  snapshot_clipboard, text_of, type_text, user32)

log = logging.getLogger("qeplus")


class App:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.watcher = PopupWatcher()
        self.toolbar = Toolbar()
        self.toast = Toast()
        self.snipper = Snipper()
        self.deliverer = Deliverer()
        self.recorder = voice.Recorder()
        self.transcriber = voice.Transcriber()
        self.hotkeys = Hotkeys(int(self.toolbar.winId()), self.settings["hotkeys"])
        self.warned_conflicts: set[str] = set()
        # the answer card: our own input bar, opened by a hotkey that is always registered
        self.ask = AskWindow(self.settings["hotkeys"])
        self.claude = ClaudeSession()
        # global hotkeys that are always on (except while paused)
        self.ask_key = Hotkeys(int(self.ask.winId()), self.global_bindings())
        self.target = "popup"  # where the next capture or transcript goes: popup | ask
        self.before_ask = 0    # the window you were in when you opened the bar
        clear_attachment_files()
        self.keyhook = KeyHook()  # push-to-talk and double-tap; installed only if one is on
        self.mic = MicIndicator()
        self.paused = False
        self.busy = False
        self.draft = ""        # pop-up text taken along with the current capture
        self.moved_draft = ""  # text that went to the main window: cleared from the pop-up later

        self.watcher.shown.connect(self.on_shown)
        self.watcher.moved.connect(self.dock)
        self.watcher.hidden.connect(self.on_hidden)
        self.toolbar.triggered.connect(self.on_button)
        self.snipper.captured.connect(self.on_captured)
        self.snipper.cancelled.connect(self.on_cancelled)
        self.deliverer.done.connect(self.on_delivered)
        self.recorder.limit_reached.connect(self.on_recording_limit)
        self.transcriber.finished.connect(self.on_transcript)
        self.transcriber.failed.connect(self.on_transcribe_failed)
        self.transcriber.notice.connect(self.toast.show_message)
        self.hotkeys.triggered.connect(self.on_hotkey)
        self.hotkeys.conflict.connect(self.on_hotkey_conflict)
        self.ask_key.triggered.connect(self.on_global_hotkey)
        self.ask_key.conflict.connect(self.on_hotkey_conflict)
        self.ask.submitted.connect(self.ask_submit)
        self.ask.attach.connect(self.ask_attach)
        self.ask.new_requested.connect(self.ask_new)
        self.ask.open_in_claude.connect(self.ask_open_in_claude)
        self.ask.closed.connect(self.close_ask)
        self.ask.copied.connect(self.copy_code)
        self.claude.text.connect(self.ask.add_text)
        self.claude.finished.connect(self.ask.finish_turn)
        self.claude.failed.connect(self.ask.show_error)
        # after the card has the full answer; a failed turn never overwrites a saved session
        self.claude.finished.connect(self.save_session)
        self.claude.notice.connect(self.toast.show_message)
        self.ask.recent_requested.connect(self.show_recent)
        self.ask.clipboard_accepted.connect(self.attach_clipboard)
        self.clip_changed = 0.0      # when someone else last copied something
        self.clip_offered = 0        # the clipboard sequence number the chip was offered for
        self.copying_selection = False
        self.transcribing = False    # one transcription at a time
        QGuiApplication.clipboard().dataChanged.connect(self.on_clipboard_changed)
        self.keyhook.ptt_down.connect(self.ptt_start)
        self.keyhook.ptt_up.connect(self.ptt_stop)
        self.keyhook.ptt_cancel.connect(self.ptt_cancel)
        # while push-to-talk records: watch for a secure desktop or an admin window in front
        self.ptt_watchdog = QTimer(interval=250)
        self.ptt_watchdog.timeout.connect(self.check_ptt)
        self.session_watch = SessionWatch(int(self.toolbar.winId()))
        self.session_watch.changed.connect(self.on_session_change)
        self.keyhook.double_tap.connect(lambda: None if self.paused else self.open_ask())
        self.ask_key.enable()
        self.settings.changed.connect(self.apply_settings)
        self.settings.problem.connect(self.toast.show_message)

    def apply_settings(self) -> None:
        toolbar_module.user_animations = self.settings["animations"]
        if self.settings["voice_engine"] == "whisper" or self.settings["push_to_talk"]:
            self.transcriber.preload(self.settings["whisper_model"])  # first dictation is quick
        self.hotkeys.set_bindings(self.settings["hotkeys"])
        self.ask_key.set_bindings(self.global_bindings())
        self.configure_keyhook()
        self.ask.set_shortcuts(self.settings["hotkeys"])
        self.ask.recent.setVisible(self.settings["recent_sessions"])
        self.warned_conflicts.clear()
        for name, combo in self.settings["hotkeys"].items():
            label = self.toolbar.buttons[name]
            label.shortcut = combo
            label.set_label(label.label)  # refresh the accessible name
        if self.toolbar.isVisible():
            self.dock()

    def set_paused(self, paused: bool) -> None:
        """Paused: no toolbar and no hotkeys; the pop-up itself is untouched."""
        self.paused = paused
        log.info("paused" if paused else "resumed")
        if paused:  # release everything: nothing recorded, typed, pasted or copied later
            self.cancel_voice()
            self.snipper.cancel()
            self.deliverer.cancel()
            self.busy, self.moved_draft = False, ""
            self.on_hidden()
            self.close_ask()
            self.ask_key.disable()
        else:
            self.ask_key.enable()
            if self.watcher.visible():
                self.on_shown()
        self.configure_keyhook()

    def dock(self, animate: bool = False) -> None:
        left, top, right, _ = rect(self.watcher.popup)
        self.toolbar.dock(left, top, right, user32.GetDpiForWindow(self.watcher.popup),
                          monitor_right(self.watcher.popup), animate,
                          self.settings["toolbar_position"])

    def on_shown(self) -> None:
        if self.busy or self.paused:
            return
        self.dock(animate=True)
        self.hotkeys.enable()
        log.info("pop-up shown over %s; toolbar at %s size %dx%d",
                 exe_name(self.watcher.before_popup) or "?", self.toolbar.target,
                 self.toolbar.width(), self.toolbar.height())
        if self.moved_draft:
            QTimer.singleShot(200, self.clear_moved_draft)
        target = window_name(self.watcher.before_popup)
        self.toolbar.buttons["window"].set_label(f"Attach {target}")

    def on_hidden(self) -> None:
        self.hotkeys.disable()
        self.toolbar.hide()
        if self.recorder.recording and self.target == "popup":
            self.stop_recording()  # the user clicked away: keep what they said

    def clear_moved_draft(self) -> None:
        """The draft went to the main window with a screenshot: don't leave a copy in the pop-up."""
        moved, self.moved_draft = self.moved_draft, ""
        if self.paused or not moved:
            return
        try:
            current = " ".join(uia.focused_text().split())
        except Exception:
            return
        if self.watcher.focused() and current == moved:
            press(VK_CONTROL, ord("A"))
            press(VK_BACK)

    def on_button(self, name: str) -> None:
        voice_idle = self.voice_state() == "idle"
        if self.busy or (name != "voice" and not voice_idle):
            return
        if name == "screenshot":
            self.start_capture()
        elif name == "window":
            self.attach_window()
        elif name == "voice":
            self.toggle_voice()

    def on_hotkey(self, name: str) -> None:
        # it may have closed, or been paused, while Alt was still held
        if self.watcher.visible() and not self.paused:
            log.info("hotkey: %s", name)
            self.on_button(name)

    def on_hotkey_conflict(self, combo: str) -> None:
        if combo not in self.warned_conflicts:  # once per run, not on every pop-up
            self.warned_conflicts.add(combo)
            self.toast.show_message(f"{combo} is taken by another app, so it won't work here.")

    # --- voice ---------------------------------------------------------------------------

    def voice_state(self) -> str:
        return self.toolbar.buttons["voice"].state

    def set_voice_state(self, state: str) -> None:
        self.toolbar.set_state("voice", state)
        self.ask.buttons["voice"].set_state(state)

    def toggle_voice(self) -> None:
        state = self.voice_state()
        if state == "active":
            self.stop_recording()
        elif state == "idle":
            self.start_recording()

    def start_recording(self) -> None:
        if self.recorder.recording or self.transcribing:
            return
        if self.settings["voice_engine"] == "windows":
            self.with_popup(lambda: press(VK_LWIN, ord("H")), lambda: None)
            return
        try:
            self.recorder.start()
        except Exception as e:
            log.warning("microphone failed: %s", e)
            self.toast.show_message("Couldn't open the microphone. Check Windows privacy settings.")
            return
        self.set_voice_state("active")

    def on_recording_limit(self) -> None:
        if self.target == "ptt":  # a key-up we never saw: don't upload minutes of audio
            self.keyhook.reset()
            self.cancel_voice()
            self.toast.show_message("Push-to-talk stopped after 5 minutes; that recording "
                                    "was discarded.")
        else:
            self.stop_recording()

    def cancel_voice(self) -> None:
        """Drop a recording without transcribing it (Esc while holding, pause, lock, cap)."""
        self.ptt_watchdog.stop()
        if self.recorder.recording:
            self.recorder.stop()
            log.info("recording discarded")
            if self.target in ("ptt", "ask"):
                self.target = "popup"
            self.set_voice_state("idle")
        self.mic.hide()

    def stop_recording(self) -> None:
        if not self.recorder.recording:
            return
        wav = self.recorder.stop()
        seconds = (len(wav) - 44) / (voice.RATE * 2)
        log.info("recorded %.1f s", seconds)
        if seconds < 0.3:
            self.set_voice_state("idle")
            return
        self.set_voice_state("busy")
        self.transcribing = True
        self.transcriber.start(wav, self.settings["whisper_model"],
                               self.settings["transcript_cleanup"])

    def on_transcript(self, text: str) -> None:
        self.transcribing = False
        self.set_voice_state("idle")
        text = " ".join(text.split())  # the pop-up is single-line
        if self.paused:  # paused while Whisper worked: nothing is typed or sent anywhere
            self.target = "popup"
            self.mic.hide()
            if text:
                self.transcript_to_clipboard(text)  # but the words aren't lost
            return
        log.info("transcript: %d chars", len(text))
        ptt = self.target == "ptt"
        if ptt:
            self.target = "popup"
            self.mic.hide()  # also when nothing was heard: never leave "Transcribing…" up
        if not text:
            self.toast.show_message("Didn't catch any speech.")
            if self.target == "ask":
                self.target = "popup"
            return
        if ptt:  # push-to-talk: ask the answer card straight away
            pending = self.ask_has_draft()
            self.open_ask()
            self.ask.insert_text(text)
            self.send_dictated(pending)
            return
        if self.target == "ask":
            self.target = "popup"
            if self.ask.isVisible():
                pending = self.ask_has_draft()
                self.ask.insert_text(text)
                if self.settings["voice_autosend"]:
                    self.send_dictated(pending)
            else:  # the bar was closed meanwhile
                self.transcript_to_clipboard(text)
            return

        if self.paused or not self.watcher.focused():
            # you moved on while Whisper worked: don't reopen the pop-up and steal focus
            self.transcript_to_clipboard(text)
            return
        try:
            current = uia.focused_text()
        except Exception:
            current = ""
        if not self.watcher.focused():  # focus can move during that cross-process call
            self.transcript_to_clipboard(text)
            return
        type_text((" " if current and not current.endswith(" ") else "") + text)

    def ask_has_draft(self) -> bool:
        """Something the user started on: typing, attachments, a chip, or an answer coming."""
        return bool(self.ask.input.text().strip() or self.ask.attachments or self.ask.context
                    or self.ask.busy)

    def send_dictated(self, pending: bool) -> None:
        """Never send someone's draft along with dictation: add to it and let them send it."""
        if pending:
            self.toast.show_message("Added to your question: press Enter to send.")
        else:
            self.ask.input.returnPressed.emit()

    def transcript_to_clipboard(self, text: str) -> None:
        put_on_clipboard(text=text)
        self.toast.show_message("Transcript copied: paste it with Ctrl+V.")

    def on_transcribe_failed(self, error: str) -> None:
        self.transcribing = False
        self.set_voice_state("idle")
        if self.paused:
            self.target = "popup"
            self.mic.hide()
            return
        if self.target in ("ask", "ptt"):
            self.target = "popup"
            self.mic.hide()
            self.toast.show_message("Whisper couldn't transcribe that recording.")
            return
        if self.paused or not self.watcher.focused():
            # never switch the microphone on for someone who has moved on
            self.toast.show_message("Whisper couldn't transcribe that recording.")
            return
        self.voice_typing("Whisper couldn't transcribe that, so here's Windows voice typing: "
                          "please say it again.")

    def voice_typing(self, message: str) -> None:
        self.toast.show_message(message)
        self.with_popup(lambda: press(VK_LWIN, ord("H")), lambda: None)

    def with_popup(self, action, give_up, tries: int = 0) -> None:
        """Run action once the pop-up has focus, reopening it if needed; else give_up()."""
        if self.paused:  # paused while waiting: press nothing
            return
        if self.watcher.focused():
            action()
        elif tries >= 20:
            give_up()
        else:
            if tries == 0 and not self.watcher.visible():
                self.open_popup()
            QTimer.singleShot(100, lambda: self.with_popup(action, give_up, tries + 1))

    def open_popup(self) -> None:
        mods, vk = parse(self.settings["popup_shortcut"])
        keys = [k for flag, k in ((0x2, 0x11), (0x1, 0x12), (0x4, 0x10), (0x8, VK_LWIN))
                if mods & flag]  # Ctrl, Alt, Shift, Win
        press(*keys, vk)

    # --- the answer card ------------------------------------------------------------

    def global_bindings(self) -> dict[str, str]:
        s = self.settings
        return {"ask": s["ask_hotkey"], "open_claude": s["open_claude_hotkey"],
                "ask_selection": s["ask_selection_hotkey"]}  # "" leaves one unregistered

    def on_global_hotkey(self, name: str) -> None:
        log.info("global hotkey: %s", name)
        {"ask": self.toggle_ask, "open_claude": self.focus_claude,
         "ask_selection": self.ask_about_selection}[name]()

    def focus_claude(self) -> None:
        """Bring Claude Desktop's main window to the front (not quick entry), restoring it if
        minimised; start Claude if it isn't running or its window is closed to the tray."""
        # top-level windows only (an owned dialog isn't the main window), the largest by its
        # normal size, so a minimised window still counts
        wins = [h for h in find_windows(is_main_window)
                if user32.IsWindowVisible(h) and not user32.GetWindow(h, 4)]  # GW_OWNER
        if wins:
            hwnd = max(wins, key=normal_area)
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return
        try:  # launching it again makes the running app show its window, or starts it
            os.startfile(CLAUDE_APP)
            return
        except OSError as e:
            log.warning("couldn't start Claude from its Start menu entry: %s", e)
        # closed to the tray: run the same claude.exe again, which shows its window
        running = [exe_path(h) for h in find_windows(
            lambda h: exe_name(h) == "claude.exe"
            and text_of(h, user32.GetClassNameW) == "Chrome_WidgetWin_1")]
        path = next((p for p in running if p), "")
        try:
            if not path:
                raise OSError("Claude Desktop isn't running")
            subprocess.Popen([path], creationflags=subprocess.DETACHED_PROCESS)
        except OSError as e:
            log.warning("couldn't start Claude: %s", e)
            self.toast.show_message("Couldn't find Claude Desktop.")

    def configure_keyhook(self) -> None:
        """Install the keyboard hook only while push-to-talk or double-tap is on."""
        on = not self.paused
        ptt = (ptt_vk(self.settings["push_to_talk_key"])
               if on and self.settings["push_to_talk"] else None)
        tap = (TAP_KEYS[self.settings["double_tap_key"].lower()]
               if on and self.settings["double_tap"] else set())
        if self.target == "ptt" and self.recorder.recording and ptt != self.keyhook.logic.ptt:
            self.cancel_voice()  # its key-up would never be recognised now
        self.keyhook.configure(ptt, tap)

    def ptt_start(self) -> None:
        if self.paused or self.recorder.recording or self.transcribing:
            return
        try:
            self.recorder.start()
        except Exception as e:
            log.warning("microphone failed: %s", e)
            self.toast.show_message("Couldn't open the microphone. Check Windows privacy settings.")
            return
        self.target = "ptt"
        self.mic.show_state("listening")
        self.ptt_watchdog.start()

    def ptt_stop(self) -> None:
        if self.target != "ptt" or not self.recorder.recording:
            return
        self.ptt_watchdog.stop()
        wav = self.recorder.stop()
        if (len(wav) - 44) / (voice.RATE * 2) < 0.3:  # a tap, not speech
            self.target = "popup"
            self.mic.hide()
            return
        self.mic.show_state("transcribing")
        self.transcribing = True
        self.transcriber.start(wav, self.settings["whisper_model"],
                               self.settings["transcript_cleanup"])

    def ptt_cancel(self) -> None:
        """Esc while holding the push-to-talk key."""
        if self.target == "ptt" and self.recorder.recording:
            self.cancel_voice()
            self.toast.show_message("Recording cancelled.")

    def check_ptt(self) -> None:
        """The lock screen, a UAC prompt or an admin window in front means the hook won't see
        the key go up (keys aren't updated for keys it swallowed, so polling the key itself
        can't tell): cancel rather than record on."""
        if self.target != "ptt" or not self.recorder.recording:
            self.ptt_watchdog.stop()
        elif not input_desktop_ok() or is_elevated(user32.GetForegroundWindow()):
            self.keyhook.reset()
            self.cancel_voice()
            self.toast.show_message("Push-to-talk cancelled: Windows switched to a secure "
                                    "screen or an admin window.")

    def on_session_change(self) -> None:
        """Lock, unlock, sleep or resume: start the hook afresh and drop any recording."""
        self.keyhook.reset()
        if self.recorder.recording:
            self.cancel_voice()

    def toggle_ask(self) -> None:
        if self.ask.isVisible() and self.ask.isActiveWindow():
            self.close_ask()
            return
        self.open_ask()

    def open_ask(self) -> None:
        if self.ask.isVisible() and self.ask.isActiveWindow():
            return
        fg = user32.GetForegroundWindow()
        if fg and is_app_window(fg) and not is_own_window(fg):
            self.before_ask = fg
        self.ask.buttons["window"].set_label(f"Attach {window_name(self.before_ask)}")
        self.ask.open()
        self.offer_clipboard()
        self.claude.start()  # warm: the first answer starts ~1 s after Enter
        self.claude.check_account()  # picks up a sign-in fixed (or changed) since start-up

    def close_ask(self) -> None:
        """Esc: hide everything and end the process; the conversation resumes next time."""
        self.claude.stop()
        self.ask.tip.hide()
        self.ask.dismiss()
        if self.ask.busy:
            self.ask.show_error("Stopped.")

    def ask_submit(self, text: str) -> None:
        images = list(self.ask.attachments)
        context, label = self.ask.context, self.ask.context_label
        self.ask.clear_attachments(delete=False)  # the session deletes them once read
        self.claude.fallback = self.ask.transcript()  # if --resume finds nothing
        self.ask.clear_context()
        self.ask.hide_clipboard_offer()
        self.ask.begin_turn(text, len(images), label.lower() if context else "")
        message = f"{text}\n\n{label}:\n\"\"\"\n{context}\n\"\"\"" if context else text
        self.claude.ask(message, images)

    def save_session(self, *_) -> None:
        if self.settings["recent_sessions"] and self.claude.session_id:
            try:
                sessions.save(self.claude.session_id, self.ask.turns)
            except OSError as e:
                log.warning("couldn't save the session: %s", e)

    def show_recent(self) -> None:
        menu = QMenu(self.ask)
        menu.setStyleSheet(RECENT_MENU_STYLE)
        entries = sessions.load()
        if not entries:
            menu.addAction("No recent conversations yet").setEnabled(False)
        icon = QIcon(Glyph().grab())
        for entry in entries:
            # one line, and & shown as & rather than turned into a keyboard accelerator
            title = " ".join(entry["title"].split()).replace("&", "&&")
            action = menu.addAction(icon, f"{title}\t{sessions.when(entry['updated'])}")
            action.triggered.connect(lambda _=False, e=entry: self.resume_session(e))
        menu.exec(self.ask.recent.mapToGlobal(self.ask.recent.rect().bottomLeft()))

    def resume_session(self, entry: dict) -> None:
        """Reopen a saved conversation: its history in the card, continued with --resume."""
        if entry.get("backend", "claude") != "claude":
            self.toast.show_message("Only Claude conversations can be resumed.")
            return
        self.claude.stop()
        self.claude.session_id = entry["id"]
        self.ask.clear_attachments()
        self.ask.load_conversation(entry["turns"])
        self.claude.start()
        log.info("resumed a recent conversation")

    # --- selection and clipboard ------------------------------------------------------

    def on_clipboard_changed(self) -> None:
        if (QGuiApplication.clipboard().ownsClipboard() or self.copying_selection
                or clipboard_is_private()):
            self.clip_changed = 0.0  # ours, or a password manager's: never offered
        else:
            self.clip_changed = time.monotonic()  # copied by you, in another app

    def offer_clipboard(self) -> None:
        """Something copied in the last 30 s (text or image)? Offer to attach it."""
        if (not self.settings["clipboard_chip"] or not self.clip_changed
                or time.monotonic() - self.clip_changed > 30 or self.ask.context
                or clipboard_is_private()):
            return
        self.clip_offered = user32.GetClipboardSequenceNumber()
        data = QGuiApplication.clipboard().mimeData()
        if data is not None and data.hasImage():
            self.ask.offer_clipboard("image")
        elif data is not None and data.text().strip():
            self.ask.offer_clipboard("text")

    def attach_clipboard(self) -> None:
        self.clip_changed = 0.0  # offered once
        if (user32.GetClipboardSequenceNumber() != self.clip_offered
                or clipboard_is_private()):
            self.toast.show_message("The clipboard changed, so nothing was attached.")
            return
        data = QGuiApplication.clipboard().mimeData()
        if data is not None and data.hasImage():
            self.attach_image(QGuiApplication.clipboard().image())
        elif data is not None and data.text().strip():
            self.ask.set_context(data.text(), "Clipboard text")

    def ask_about_selection(self) -> None:
        """Copy the selection in the app you're in (your clipboard is saved and put back),
        then open the bar with it as a removable "Selected text" chip."""
        if (self.paused or self.copying_selection
                or (self.ask.isVisible() and self.ask.isActiveWindow())):
            return
        fg = user32.GetForegroundWindow()
        if is_elevated(fg):  # before anything crosses into it
            self.toast.show_message("Can't copy from an admin window.")
            return
        try:  # read it straight from the app if it exposes it: no keys, clipboard untouched
            text, kind = uia.focused_selection()
        except Exception as e:  # UIA crosses into the other app and can time out
            log.debug("UIA selection failed: %s", e)
            text, kind = None, ""
        if text and text.strip():
            self.show_selection(text)
            return
        if kind == "password":
            self.toast.show_message("That's a password field, so nothing was copied.")
            return
        key = copy_key(fg, kind)
        if key is None:
            self.toast.show_message("Here a copy shortcut could do something else (a "
                                    "terminal, remote desktop or a VM): copy the text "
                                    "yourself, then use Attach clipboard.")
            return
        saved = snapshot_clipboard()
        if saved is None:
            self.toast.show_message("Couldn't read your clipboard, so nothing was copied.")
            return
        if user32.GetForegroundWindow() != fg:  # focus moved during the checks above
            return
        self.copying_selection = True
        before = user32.GetClipboardSequenceNumber()
        try:
            press(VK_CONTROL, key)
        except Exception:
            self.copying_selection = False
            raise

        def finish(tries: int = 0, last: int = before) -> None:
            seq = user32.GetClipboardSequenceNumber()
            # wait for the copy, then one more tick so a multi-format copy has settled
            if (seq == before or seq != last) and tries < 15 and not self.paused:
                QTimer.singleShot(40, lambda: finish(tries + 1, seq))  # up to ~600 ms
                return
            try:
                self.finish_selection(before, saved)
            finally:
                QTimer.singleShot(100, lambda: setattr(self, "copying_selection", False))

        QTimer.singleShot(40, finish)

    def finish_selection(self, before: int, saved) -> None:
        seq = user32.GetClipboardSequenceNumber()
        text = QGuiApplication.clipboard().text() if seq != before else ""
        if seq != before:
            if user32.GetClipboardSequenceNumber() == seq:  # put your clipboard back
                if not restore_clipboard(saved, hwnd=int(self.ask.winId())):
                    log.warning("couldn't restore the clipboard after copying a selection")
            else:  # copied again since: that's newer than what we saved, so leave it
                log.info("clipboard changed after the selection copy; not restoring")
        if self.paused:
            return
        if text.strip():
            self.show_selection(text)
        else:
            self.open_ask()
            self.toast.show_message("No text was selected.")

    def show_selection(self, text: str) -> None:
        self.open_ask()
        self.ask.set_context(text, "Selected text")
        self.ask.hide_clipboard_offer()

    def ask_new(self) -> None:
        self.ask.clear_conversation()
        self.ask.clear_attachments()
        clear_attachment_files()
        self.claude.reset()

    def ask_open_in_claude(self) -> None:
        text = self.ask.transcript()
        if text:
            put_on_clipboard(text=text)
        self.close_ask()
        try:
            os.startfile(NEW_CHAT)
        except OSError as e:
            log.warning("couldn't open a new chat: %s", e)
            self.toast.show_message("Couldn't open Claude." + (
                " The conversation is on your clipboard." if text else ""))
            return
        self.toast.show_message("Conversation copied: paste it into the new chat with "
                                "Ctrl+V." if text else "Opened Claude.")

    def copy_code(self, code: str) -> None:
        put_on_clipboard(text=code)
        self.toast.show_message("Code copied.")

    def ask_attach(self, name: str) -> None:
        if name == "screenshot":
            self.target = "ask"
            self.ask.hide()  # out of the way of the selection, and of the capture
            self.snipper.start()
        elif name == "window":
            self.ask_attach_window()
        elif self.settings["voice_engine"] == "windows":
            self.ask.open()
            QTimer.singleShot(150, lambda: None if self.paused else press(VK_LWIN, ord("H")))
        else:
            self.target = "ask"
            self.toggle_voice()

    def ask_attach_window(self) -> None:
        target = self.before_ask
        if not target or not user32.IsWindow(target) or user32.IsIconic(target):
            self.toast.show_message("No window to attach: open the bar from the window "
                                    "you want.")
            return
        image = window_image(target)
        if image is not None:
            self.attach_image(image)
            return
        self.ask.hide()  # the screen-grab fallback must not capture the bar

        def grab():
            if self.paused:
                return
            if on_top(target):
                self.attach_image(screen_image(*frame_rect(target)))
            else:
                self.toast.show_message(f"{window_name(target)} is partly covered, so it "
                                        "can't be captured.")
                self.ask.open()

        QTimer.singleShot(250, grab)

    def attach_image(self, image: QImage | None) -> None:
        """Save a capture as a PNG in the scratch folder and attach it to the next question."""
        if image is None or image.isNull():
            self.toast.show_message("The capture came out empty.")
        else:
            os.makedirs(ATTACHMENTS, exist_ok=True)
            path = os.path.join(ATTACHMENTS, f"capture-{time.strftime('%Y%m%d-%H%M%S')}-"
                                        f"{secrets.token_hex(4)}.png")  # never two alike
            image.save(path, "PNG")
            self.ask.add_attachment(path)
        self.ask.open()

    def take_draft(self) -> None:
        try:
            self.draft = uia.focused_text() if self.watcher.focused() else ""
        except Exception as e:
            log.debug("couldn't read the pop-up draft: %s", e)
            self.draft = ""

    def start_capture(self) -> None:
        self.busy = True
        self.take_draft()
        self.toolbar.hide()
        self.snipper.start()

    def attach_window(self) -> None:
        target = self.watcher.before_popup
        if not target or not user32.IsWindow(target):
            self.toast.show_message(
                "No window to attach: open the pop-up from the window you want.")
            return
        if user32.IsIconic(target):
            self.toast.show_message(
                f"{window_name(target)} is minimised, so there's nothing to see.")
            return
        self.busy = True
        self.take_draft()
        self.toolbar.hide()
        image = window_image(target)
        if image is not None:
            self.on_captured(image)
            return
        # the window drew nothing for PrintWindow: grab it from the screen instead, once the
        # pop-up (which sits on top of it) is closed; Esc keeps the pop-up's draft
        log.info("PrintWindow gave nothing for %s; grabbing the screen", exe_name(target))
        if self.watcher.focused():
            press(VK_ESCAPE)
        QTimer.singleShot(250, lambda: self.grab_screen(target))

    def grab_screen(self, target) -> None:
        if self.paused:
            self.busy = False
            return
        if not user32.IsWindow(target) or user32.IsIconic(target):
            self.busy = False
            self.toast.show_message("That window closed or was minimised.")
            return
        if not on_top(target):
            # a screen grab would capture whatever covers it instead
            self.busy = False
            self.toast.show_message(
                f"{window_name(target)} is partly covered, so it can't be captured. "
                "Bring it to the front and try again.")
            return
        image = screen_image(*frame_rect(target))
        if image is None:
            self.busy = False
            self.toast.show_message(f"Couldn't capture {window_name(target)}.")
        else:
            self.on_captured(image)

    def on_cancelled(self) -> None:
        if self.paused:  # cancelled by Pause: open nothing
            self.target, self.busy = "popup", False
            return
        if self.target == "ask":
            self.target = "popup"
            self.ask.open()
            return
        self.busy = False
        if not self.watcher.visible():
            self.open_popup()  # back to the pop-up the user came from

    def on_captured(self, image: QImage) -> None:
        log.info("captured %dx%d", image.width(), image.height())
        if self.paused:  # a capture that finished after Pause goes nowhere
            self.target, self.busy = "popup", False
            return
        if self.target == "ask":
            self.target = "popup"
            self.attach_image(image)
            return
        self.deliverer.start(image, self.draft)

    def on_delivered(self, ok: bool, message: str) -> None:
        self.busy = False
        if ok:
            self.moved_draft = " ".join(self.draft.split())
        else:
            self.toast.show_message(message)


CLAUDE_APP = r"shell:AppsFolder\Claude_pzs8sxrjxfjjc!Claude"  # its Start menu entry (MSIX)
# Windows Terminal, conhost, mintty (Git Bash): Ctrl+Insert copies and never interrupts
TERMINAL_CLASSES = {"CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass", "mintty"}
# terminals where Ctrl+C interrupts and Ctrl+Insert isn't reliably copy: send nothing
# terminals, remote desktops and VMs (Ctrl+C/Ctrl+Insert go to the remote machine)
NO_COPY_EXES = {"putty.exe", "kitty.exe", "alacritty.exe", "wezterm-gui.exe", "conemu.exe",
                "conemu64.exe", "hyper.exe", "tabby.exe", "termius.exe", "warp.exe", "rio.exe",
                "mstsc.exe", "msrdc.exe", "vmware.exe", "virtualboxvm.exe", "vmconnect.exe"}
NO_COPY_PREFIXES = ("mobaxterm",)  # versioned names like MobaXterm_Personal_24.2.exe
# JetBrains IDEs: the editor copies normally; their terminal tool window is refused when UIA
# reports it as a terminal (kind "terminal"), like any other app's embedded terminal
RECENT_MENU_STYLE = """
  QMenu { background: #2f2c2a; color: #e8e6e3; border: 1px solid #4a4745; padding: 6px;
          font-family: 'Segoe UI Variable Text'; font-size: 13px; }
  QMenu::item { padding: 7px 24px 7px 8px; border-radius: 6px; min-width: 340px; }
  QMenu::item:selected { background: rgba(255, 255, 255, 22); }
  QMenu::item:disabled { color: #8a8780; }
  QMenu::icon { padding-left: 6px; }
"""


def is_own_window(hwnd) -> bool:
    from ctypes import wintypes as wt

    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


def clear_attachment_files() -> None:
    """Captures are only needed until they are sent: don't keep screenshots around."""
    for name in os.listdir(ATTACHMENTS) if os.path.isdir(ATTACHMENTS) else []:
        try:
            os.remove(os.path.join(ATTACHMENTS, name))
        except OSError:
            pass  # still open somewhere; it goes next time


def copy_key(hwnd, kind: str = "") -> int | None:
    """The key to press with Ctrl to copy the selection in hwnd: C, or Insert in terminals
    (where Ctrl+C with nothing selected interrupts the running program); None to send nothing
    (other terminals, and terminals inside editors, which UIA reports as kind "terminal")."""
    exe = exe_name(hwnd)
    if exe in NO_COPY_EXES or exe.startswith(NO_COPY_PREFIXES):
        return None
    if text_of(hwnd, user32.GetClassNameW) in TERMINAL_CLASSES:
        return 0x2D  # VK_INSERT
    return None if kind in ("terminal", "unknown") else ord("C")


def on_top(hwnd, grid: int = 5) -> bool:
    """True if hwnd is the top window at every point of a grid across its visible frame."""
    from ctypes import wintypes as wt

    left, top, right, bottom = frame_rect(hwnd)
    for i in range(grid):
        for j in range(grid):
            x = left + 2 + (right - left - 4) * i // (grid - 1)
            y = top + 2 + (bottom - top - 4) * j // (grid - 1)
            hit = user32.WindowFromPoint(wt.POINT(x, y))
            if not hit or user32.GetAncestor(hit, 2) != hwnd:  # GA_ROOT
                return False
    return True


def window_name(hwnd) -> str:
    """Short name for tooltips and toasts: the window title, else the program name."""
    if not hwnd or not user32.IsWindow(hwnd):
        return "window"
    name = text_of(hwnd, user32.GetWindowTextW) or exe_name(hwnd).removesuffix(".exe") or "window"
    return name if len(name) <= 32 else name[:31] + "…"


def setup_logging(folder: str) -> str:
    """Rotating log in the settings folder (1 MB x 3), plus the console when there is one."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "qeplus.log")
    handlers: list[logging.Handler] = [RotatingFileHandler(path, maxBytes=1_000_000,
                                                           backupCount=3, encoding="utf-8")]
    if sys.stderr is not None:  # pythonw and the windowed exe have no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.WARNING, handlers=handlers,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("qeplus").setLevel(logging.INFO)
    return path


def popup_found() -> str:
    """Self-test: is Claude's pop-up window still recognised? Fails loudly if a Claude
    update changed it; skipped when Claude isn't running."""
    from .win import find_windows, is_popup

    if not find_windows(lambda h: exe_name(h) == "claude.exe"):
        return "skipped (Claude Desktop isn't running)"
    if not find_windows(is_popup):
        raise LookupError("Claude is running but no window matches the quick entry "
                          "pop-up: a Claude update may have changed it")
    return "found"


def claude_signed_in() -> str:
    """Self-test: the answer card's CLI exists and is signed in to a Claude subscription."""
    from .claude_cli import account_problem, auth_status, find_cli

    cli = find_cli()
    if cli is None:
        raise FileNotFoundError("Claude Code not found (install Claude Desktop or Claude Code)")
    status = auth_status(cli)
    problem = account_problem(status, cli)
    if problem:
        raise PermissionError(problem)
    return f"signed in via {status.get('authMethod')} ({status.get('subscriptionType')})"


def selftest() -> int:
    """`--selftest`: check that everything the app loads lazily works in this build (the
    packaged exe has no console, so results go to the log). Exit code 0 = all passed."""
    import tempfile

    from .capture import screen_image
    from .tray import draw, set_starts_with_windows, starts_with_windows

    def mic():
        import sounddevice as sd
        return sd.query_devices(kind="input")["name"]

    def whisper():
        import io
        import wave

        import ctranslate2
        import faster_whisper
        versions = (f"faster-whisper {faster_whisper.__version__}, "
                    f"ctranslate2 {ctranslate2.__version__}")
        if not voice.model_ready(voice.MODEL):
            return f"{versions}; {voice.MODEL} model not downloaded yet (it is on first use)"
        silence = io.BytesIO()  # a real run on half a second of silence: engine and VAD bundled
        with wave.open(silence, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(voice.RATE)
            w.writeframes(bytes(voice.RATE))
        heard = voice.transcribe(silence.getvalue(), voice.MODEL)
        return f"{versions}; {voice.MODEL} transcribed silence as {heard!r}"

    def shortcut():
        folder = tempfile.mkdtemp(prefix="qeplus-selftest-")
        set_starts_with_windows(True, folder)
        made = starts_with_windows(folder)
        set_starts_with_windows(False, folder)
        return f"created={made}"

    checks = {
        "ui automation": lambda: f"focus in a web page: {uia.focused_textbox_url() is not None}",
        "microphone (PortAudio)": mic,
        "whisper (offline)": whisper,
        "screen capture": lambda: f"{screen_image(0, 0, 64, 64).size().toTuple()} px",
        "startup shortcut": shortcut,
        "icon drawing": lambda: f"{draw(32).width()} px",
        "settings": lambda: Settings().path,
        "pop-up detection": popup_found,
        "claude code": claude_signed_in,
    }
    failed = 0
    for name, check in checks.items():
        try:
            log.info("selftest %-22s ok   %s", name, check())
        except Exception as e:
            failed += 1
            log.error("selftest %-22s FAIL %s: %s", name, type(e).__name__, e)
    log.info("selftest: %s", "all passed" if not failed else f"{failed} failed")
    return 1 if failed else 0


def main() -> int:
    from . import settings as settings_module

    log_path = setup_logging(settings_module.DIR)
    if "--selftest" in sys.argv:
        qt = QApplication(sys.argv)  # noqa: F841  (pixmaps and Settings need it)
        return selftest()
    if already_running():
        log.info("another copy is already running; exiting")
        return 0
    qt = QApplication(sys.argv)
    qt.setQuitOnLastWindowClosed(False)
    app = App()
    tray = Tray()

    def on_error(kind, value, tb):
        # never crash: log it and say so in a toast; the signal is queued onto the Qt
        # thread, so this works from worker threads too
        log.error("unexpected error", exc_info=(kind, value, tb))
        app.toast.message.emit(f"Something went wrong ({kind.__name__}). "
                               "Details are in the log.")

    sys.excepthook = on_error
    threading.excepthook = lambda a: on_error(a.exc_type, a.exc_value, a.exc_traceback)
    # errors inside Win32 callbacks (the window-event hook) arrive here, not at excepthook
    sys.unraisablehook = lambda u: on_error(type(u.exc_value), u.exc_value,
                                            u.exc_traceback)

    def quit_app():
        app.keyhook.stop()
        app.session_watch.stop()
        app.claude.stop()
        app.ask_key.disable()
        app.hotkeys.disable()
        app.watcher.stop()
        tray.hide()
        qt.quit()

    tray.pause_toggled.connect(app.set_paused)
    tray.open_settings.connect(lambda: os.startfile(app.settings.path))
    tray.open_log.connect(lambda: os.startfile(log_path))
    tray.reset_position.connect(app.ask.reset_position)
    app.claude.account.connect(tray.set_warning)
    tray.quit_requested.connect(quit_app)
    app.settings.start()
    app.apply_settings()
    tray.show()
    app.claude.check_account()  # a sign-in that isn't your Claude account warns in the tray
    log.info("Quick Entry Plus %s running; waiting for Claude's quick entry pop-up",
             __version__)
    return qt.exec()
