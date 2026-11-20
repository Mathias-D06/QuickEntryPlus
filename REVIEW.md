# Security and feature-test review (2026-11-19)

Reviewed by Claude (gemini-4-argon wasn't available: the API key is on the free tier, where that
model's quota is 0). Every finding below was checked against the code; "verified" means
reproduced or confirmed by running something, "reasoned" means read from the code only.

Threat model: runs as the normal user, always on. Untrusted inputs are other apps' windows and
titles, UI Automation text, the clipboard, settings.json, Gemini responses and anyone near the
mic. It sends keystrokes and opens `claude://` links, so the main risks are typing or pasting
into the wrong place and leaking screenshots, audio or text.

## Fix status (2026-11-19)

Every finding below (S1–S7, G1–G10, plus the settings crash) is **fixed**. Each fix has a test
in `tests/check_fixes.py` (or an updated one in `check_window.py` / `check_hotkeys.py`). All seven
self-checks pass, the rebuilt exe passes its `--selftest`, and a live end-to-end screenshot
delivery passed with the stricter paste check.

| Ref | Fix |
|---|---|
| S1 | Everything the app puts on the clipboard carries `ExcludeClipboardContentFromMonitorProcessing`, `CanIncludeInClipboardHistory = 0` and `CanUploadToCloudClipboard = 0` (`deliver.put_on_clipboard`) |
| S2 | Paste only when Claude's main window is in front and the focused text box is on `https://claude.ai/new` exactly (`uia.is_new_chat_url`, `uia.chat_box_focused`) |
| S3 | The log records the program name, not window titles; the self-test logs only *whether* a page has focus; third-party INFO logs no longer reach `qeplus.log` |
| S4 | `is_blank` counts only a perfectly uniform image as blank; the screen-grab fallback runs only if the window is on top across a 5×5 grid of its frame (`app.on_top`), otherwise a message |
| S5 | `sys.unraisablehook` routes errors from Win32 callbacks to the same handler |
| S6 | New `popup_shortcut` setting (default `Ctrl+Alt+Space`) used to reopen the pop-up |
| S7 | UI Automation uses `CUIAutomation8` / `IUIAutomation2` with 1 s connection and 2 s transaction timeouts |
| Crash | `parse()` rejects non-ASCII keys and bare keys without a modifier; settings reject non-text hotkeys with a message |
| G1 | The clipboard sequence number is recorded when the capture is placed; if it has changed, nothing is pasted |
| G2 | An overlay closed from outside (Alt+F4) cancels the capture; the selection is clamped to its screen |
| G3 | A transcript that arrives after you've moved on, or while paused, goes to the clipboard with a message; a late failure never presses Win+H; focus is re-checked after the UIA call, right before typing |
| G4 | `EVENT_OBJECT_DESTROY` hides the toolbar and releases the hotkeys; every SHOW re-checks `is_popup()` |
| G5 | Error messages go through a `Toast.message` signal, so they arrive from any thread |
| G6 | The recorder sets its stream only after `start()` succeeds, and always clears it on stop |
| G7 | `argtypes` and `restype` set for every Win32 call that takes a handle; the toolbar shares that `user32` |
| G8 | Docking sizes the toolbar with the pop-up monitor's scale (`dpi / 96`) |
| G9 | At start-up, the pop-up itself is never recorded as the window to attach |
| G10 | `disable()` drops a pending hotkey; hotkeys are ignored while paused; `grab_screen` re-checks that the window still exists and isn't minimised; an empty user-level key value falls through to the system one |

## 1. Findings

| ID | Severity | Where | Finding | Status |
|---|---|---|---|---|
| S1 | Medium | `deliver.py:37`, `app.py:192` | **Screenshots and window captures go into Windows clipboard history (Win+V), and into Cloud Clipboard sync if it's on.** A capture of, say, an email window stays in Win+V after it has been pasted, and can sync to other devices. Qt's clipboard image has none of the formats that opt out (`ExcludeClipboardContentFromMonitorProcessing`, `CanIncludeInClipboardHistory = 0`, `CanUploadToCloudClipboard = 0`). Same for the transcript fallback. **Fix:** add those formats whenever the app writes to the clipboard. | Verified: clipboard formats listed earlier this session |
| S2 | Low–medium | `uia.py:47-50` | **The paste guard checks only the URL prefix of the focused text box, in any app.** A browser tab on `claude.ai/new`, or any claude.ai page whose path merely starts with `/new` (e.g. `/newsletter`), passes. Scenario: you switch to a browser on claude.ai during the ~1 s wait, and the draft plus screenshot land in the browser chat instead of Claude Desktop. **Fix:** also require the foreground window to be Claude's main window, and match the path exactly (`/new` or `/chat/…`). | Reasoned (the prefix part is plain string logic) |
| S3 | Low | `app.py:93`, `app.py:250` | **Window titles are written to the log.** "pop-up shown over 'Year 2: Class Notes'" and the fallback line store whatever window you were in: email subjects, document names, private-browsing page titles. They persist in `qeplus.log`. **Fix:** log the program name only. | Verified: titles found in the log |
| S4 | Low | `app.py:248-261` | **The screen-grab fallback captures whatever is on top of the target window.** If a window draws nothing for PrintWindow and another window overlaps it, the grab attaches the overlapping window's content instead. You see it before sending, but it's the wrong content. **Fix:** check the target is actually on top at its centre and corners (`WindowFromPoint`) before grabbing; otherwise show a message instead. | Reasoned |
| S5 | Low | `watcher.py:58-75` | **Errors inside the window-event callback are lost in the exe.** Python routes them to `sys.unraisablehook`, not `sys.excepthook`. With no console, they vanish: no log line and no message. Errors in the toolbar logic that the callback triggers are unaffected; those do reach the handler. **Fix:** set `sys.unraisablehook` to the same handler. | Verified: both cases run |
| S6 | Low | `app.py:215`, `app.py:266` | **Ctrl+Alt+Space is hard-coded.** If you change Claude's quick-entry shortcut, reopening the pop-up (after a cancelled screenshot or a finished transcript) sends Ctrl+Alt+Space to whatever app is in front, which may trigger that app's own shortcut. **Fix:** a `popup_shortcut` setting. | Reasoned |
| S7 | Low | `uia.py`, called from the UI thread | **UI Automation calls have no short timeout.** If Claude's window hangs, a UIA call can block the toolbar and hotkeys for the default timeout (seconds). **Fix:** set `IUIAutomation2.ConnectionTimeout` / `TransactionTimeout` to about 1–2 s. | Reasoned (not reproduced) |
| P1 | Privacy | `voice.py` | **The Gemini free tier lets Google use what you send to improve its products.** With `voice_engine: gemini` and a free-tier key, that includes your dictation audio. The default `windows` engine sends nothing. **Fix:** say so in the README; use a billing-enabled key for Gemini dictation. | Documented |

### Checked and fine
- **Gemini output can't trigger keys.** It's typed with `KEYEVENTF_UNICODE` after newlines and tabs are flattened. Verified live: Backspace (`\x08`) and Esc (`\x1b`) characters in a transcript neither deleted text nor closed the pop-up.
- **Nothing is typed unless the pop-up is visible and in front.** This fix came from a real bug this session: a hidden pop-up can stay the foreground window. Before the paste, the chat-box guard is checked again.
- **The API key** is read from the environment or the registry and sent only as a request header by the SDK. It's never logged; transcripts are logged by length only.
- **Hotkeys** exist only while the pop-up is visible (verified live) and fire only after the modifiers are released.
- **settings.json** is validated value by value, and a broken file is never overwritten. Model names only reach Google's API.
- **Single instance:** a session-local named mutex; a second copy exits.
- **A malicious program running as you** could imitate the pop-up or the chat box. That isn't a real boundary, though: such a program can already screenshot and type for itself.
- **Start with Windows** points at the exe wherever you put it, with your own permissions; nothing is elevated.

## 2. Feature test plan

"Covered" = an automated check in `tests/` or a live test run during development. Gaps first.

| Feature | Test | Expected | How | Priority | Status |
|---|---|---|---|---|---|
| Delivery | Claude's main window minimised, or closed to the tray, then 📷 | The link brings it back; the paste lands in a new chat | Manual | P1 | **Gap** |
| Packaged exe | 📷 and 🪟 end to end from `dist\QuickEntryPlus.exe` | New chat with the image and draft | Manual | P1 | **Gap** (verified from source only) |
| Voice (Gemini) | Real speech through the mic, both models | Clean transcript in the pop-up | Manual | P1 | **Gap** (TTS clip only) |
| Tray | Click each menu item: Pause, Start with Windows, Open settings, Open log, Quit | Each works; the startup shortcut appears or disappears | Manual | P1 | **Gap** (helpers tested, menu not clicked) |
| Start with Windows | Sign out and back in | Exe running, tray icon present, toolbar on pop-up | Manual | P1 | **Gap** |
| Multi-monitor / mixed DPI | Pop-up on a second screen; 📷 on each screen; 🪟 on a window there | Toolbar docks correctly; capture is the right size | Manual | P1 | **Gap** (one screen at 200% only) |
| Display scaling | Real 100%, 125% and 150% | Toolbar sharp and aligned with the bar | Manual | P2 | Covered (simulated render only) |
| Docking | Drag the pop-up, if it can be dragged | Toolbar follows | Manual | P2 | **Gap** (never moved) |
| Detection | `--selftest` also finds the pop-up window | Fails loudly after a Claude update changes it | Automated | P2 | **Gap**: add to the self-test |
| Claude not running | Start the app, then start or quit Claude | No errors; toolbar works once Claude is running | Manual | P2 | **Gap** |
| Voice | Close the pop-up while recording | Transcript inserted after the pop-up reopens | Manual | P2 | Logic only |
| Voice | Hold for 5 minutes | Stops automatically and transcribes | Automated (shorter cap) | P3 | **Gap** |
| Window capture | An admin (elevated) window; a minimised one | Message, no crash | Manual | P3 | Minimised: covered |
| Hotkeys | Another app already owns Alt+S | One message per run; other hotkeys work | Automated | P3 | Covered (`check_hotkeys`) |
| Tray | Restart Explorer | Tray icon comes back | Manual | P3 | **Gap** |
| Detection, focus | Toolbar click keeps the pop-up open and focused | Pop-up stays | Live | P1 | Covered |
| Screenshot | Drag, Esc, right-click, tiny drag | Paste / back to pop-up / back / ignored | Live | P1 | Covered |
| Delivery guard | No chat box; focus leaves while typing | Nothing pasted; message | Automated | P1 | Covered (`check_deliver`) |
| Window capture | Covered window, PrintWindow fallback, no target | Full frame / screen grab / message | Automated + live | P1 | Covered (`check_window`) |
| Voice | Windows engine, no key, Gemini 503, bad key, empty result | Win+H / Win+H + message / fallback model / Win+H / message | Automated + live | P1 | Covered (`check_voice`) |
| Settings | Reload, replaced file, broken JSON, bad values | Applied / applied / defaults, file kept / defaults + message | Automated | P1 | Covered (`check_settings`) |
| Pause, single instance | Paused pop-up; second copy | No toolbar or hotkeys; exits | Automated + live | P1 | Covered |
| Packaging | `--selftest` inside the exe | All components load | Automated (`build.ps1`) | P1 | Covered |
| Idle CPU | 60 s idle | Under 0.1% of a core | Measured | P2 | Covered (0.05%) |

## 3. Second opinion: Gemini 4 Neon (High), via the Gemini app

Gemini reviewed the source and sections 1–2 above. Each point was then checked here: **verified**
= reproduced by running code; **confirmed in code** = the code path plainly does it; **plausible**
= real risk, but whether it triggers depends on Windows' behaviour.

### Corrections to section 1
| Ref | Gemini's point | Verdict |
|---|---|---|
| S2 | Understated. If Claude's main window is already on an existing chat (`/chat/…`) with its message box focused, the guard can pass before the `new` link has navigated, so the draft and screenshot land in that existing chat. Fix: require the path to be exactly `/new` (parse it), and Claude's main window in front (`is_main_window()` already exists) | Confirmed in code: `/chat/` is accepted and the first poll is 100 ms after the link opens |
| "Fine" | "Nothing is typed unless the pop-up is in front" is too strong: `insert()` (`app.py:184-189`) makes a cross-process UIA call between the focus check and `type_text()` | Confirmed in code. Fix: re-check `watcher.focused()` right before typing |
| S4 | Understated. `is_blank()`'s 95% threshold also catches a mostly empty window (a blank editor or terminal), which then falls back to the screen grab and can capture an overlapping window | Confirmed in code (my threshold). Fix: count only a perfectly uniform image as blank |
| S3 | Two more log leaks: `--selftest` logs the focused page's full URL, and `basicConfig(level=INFO)` pulls third-party INFO logs (httpx2, google-genai, comtypes) into `qeplus.log` | Confirmed in code |
| "Fine" | "settings.json is validated value by value" is wrong | **Verified**: `"Alt+ß"` crashes start-up (`TypeError` escapes `validate`); `"S"` with no modifier is accepted; `false` silently disables a hotkey |
| S5, S7 | Fix details: `sys.unraisablehook` takes one argument (needs a wrapper), and timeouts need `CUIAutomation8` / `IUIAutomation2` | Correct |

### New findings
| ID | Severity | Where | Finding | Verdict |
|---|---|---|---|---|
| G1 | Medium | `deliver.py:37,65-69` | **Clipboard swap during delivery.** If you copy something (say a password) while Claude is opening, which takes up to 6 s, the final Ctrl+V pastes *that* into Claude instead of the screenshot. Fix: record `GetClipboardSequenceNumber()` after `setImage` and refuse to paste if it changed | Confirmed in code |
| G2 | Medium | `snip.py` | **Alt+F4 on the selection overlay wedges the app.** Neither `captured` nor `cancelled` fires, so `busy` stays `True`: no toolbar actions or hotkeys until restart, and other monitors' overlays stay open. Fix: treat an outside close as cancel | Confirmed in code (no `closeEvent`) |
| G3 | Medium | `app.py:176-206` | **Late transcripts act after you've moved on.** If you pause or click away while Gemini is working, the result reopens the pop-up (stealing focus). On failure it presses Win+H, which **turns the Windows microphone on without you asking**. Fix: when paused, or when the pop-up isn't still in front, save the text to the clipboard and show a message; never press Win+H from a late failure | Confirmed in code |
| G4 | Medium | `watcher.py:68` | **A stale pop-up handle is trusted.** Once seen, the handle is never re-checked. If Claude restarts and Windows reuses the number for another app's window, the toolbar docks there and keys can go to it. And if Claude quits while the pop-up is open, no HIDE event arrives, so the toolbar and Alt+S/W/M stay stuck. Fix: always re-check `is_popup()`, and also handle `EVENT_OBJECT_DESTROY` | Plausible (handle reuse is rare; quitting with the pop-up open is not) |
| G5 | Low | `app.py:359-363` | **Errors on background threads never show a message**: `QTimer.singleShot(0, fn)` from a worker thread never fires | **Verified** |
| G6 | Low | `voice.py:72-83` | **A microphone failure can leave the recorder stuck "recording".** `self.stream` is set before `start()` succeeds, and `stop()` isn't wrapped in `finally`; an unplugged USB mic jams the voice button and errors on every pop-up close | Confirmed in code |
| G7 | Low | Many Win32 calls | **Missing `argtypes` on calls that take handles.** 64-bit handle values above 0x7FFFFFFF raise `OverflowError` | Plausible: the same class of bug already happened once this session (`CreateCompatibleDC`) |
| G8 | Low | `toolbar.py:265-273` | **Mixed-DPI docking uses the old monitor's scale**, so the toolbar is misplaced the first time the pop-up opens on a screen with different scaling | Confirmed in code (untested: one monitor here) |
| G9 | Low | `watcher.py:41-43` | **Starting the app while the pop-up is in front** makes 🪟 target the pop-up itself | **Verified**: `is_app_window(pop-up)` is `True` |
| G10 | Low | various | Small ones: a pending hotkey can still fire after Pause (`disable()` leaves the release timer running); `grab_screen()` doesn't re-check that the window still exists or isn't minimised after its 250 ms wait; the selection isn't clamped to the screen; an empty user-level key value stops the lookup instead of trying the system one (**verified**) | Confirmed in code |

### Tests to add (Gemini's list, merged into section 2's gaps)
Existing chat already open, or focus in another Claude text box, during delivery · copying
something mid-delivery · Claude quit or restarted with the pop-up open · Pause or click away
during Gemini dictation (success and failure) · Alt+F4, Win+L or a UAC prompt during a
screenshot · unplugging the mic while recording · Win+V shows no captures (after the S1 fix) ·
malformed settings (`Alt+ß`, `S`, duplicates, `false`) · a near-empty window plus an overlapping
one for the fallback · long transcripts with emoji · starting the app with the pop-up open.
