# Quick Entry Plus

**Version 1.3.0** · Windows 11 · `QuickEntryPlus-1.3.0-win64.zip` (exe + this README)

A small Windows 11 companion for Claude Desktop's quick entry pop-up (Ctrl+Alt+Space). It docks a
slim toolbar beside the pop-up with three Mac-style extras:

| Button | Hotkey | What it does |
|---|---|---|
| 📷 Screenshot | Alt+S | Drag-select part of the screen and send it to Claude with your draft |
| 🪟 Attach window | Alt+W | Capture the window you were in before opening the pop-up and send it |
| 🎤 Dictate | Alt+M | Windows voice typing, or Gemini transcription (see [Voice](#voice)) |

It never modifies Claude Desktop. It's a separate app that watches for the pop-up and sits next
to it. The toolbar never takes focus, so the pop-up stays open while you use it.

## Install

1. Unzip `QuickEntryPlus-1.3.0-win64.zip` and put `QuickEntryPlus.exe` somewhere permanent,
   for example `%LOCALAPPDATA%\Programs\QuickEntryPlus\`. The start-with-Windows shortcut
   points at wherever the exe is when you turn that option on. `SHA256SUMS.txt` beside the
   build lists the files' checksums (`Get-FileHash QuickEntryPlus.exe` in PowerShell).
   To upgrade, quit the old copy from the tray and replace the exe.
2. Run it. Windows SmartScreen may warn because the exe isn't signed: choose **More info →
   Run anyway**.
3. Find the clay **+** icon in the tray. Windows 11 puts new icons in the hidden overflow (the
   **^** arrow); drag it onto the taskbar to keep it visible.
4. Right-click the icon and tick **Start with Windows** if you want it running all the time.

Requirements: Windows 11 and Claude Desktop with quick entry enabled. Nothing else: the exe
includes Python and every library.

## Using it

Open the pop-up as usual (Ctrl+Alt+Space, or your remapped Copilot key). The toolbar appears to its
right, or below it if there's no room, and disappears when the pop-up closes. Hover a button to
see its hotkey. The window button's tooltip names the window it will capture.

**Where screenshots go.** Claude's quick entry pop-up on Windows ignores pasted images (tested
with every common clipboard format), so screenshots and window captures go to a **new chat in
the main Claude window**, along with whatever you'd typed in the pop-up. The app pastes only
after Windows UI Automation confirms that Claude Desktop's main window is in front and keyboard
focus is in the message box of a **new** chat (`claude.ai/new`), never an existing chat or a
browser tab. If it can't confirm that, nothing is typed or pasted: the image stays on your
clipboard and a message tells you so. If you copy something else while Claude is opening,
nothing is pasted either, so your copied text can't end up in the chat. The draft is cleared
from the pop-up the next time you open it.

Captures and transcripts the app puts on the clipboard are kept out of clipboard history (Win+V)
and Cloud Clipboard sync.

**Cancelling a screenshot:** Esc or right-click returns you to the pop-up.

**Hotkeys** exist only while the pop-up is open, so Alt+S, Alt+W and Alt+M keep working normally
in other apps.

### Voice

- **`windows`** (default): opens Windows voice typing (Win+H) in the pop-up. Press the 🎤 button
  or Alt+M, then speak. It stops by itself after a pause, or press Esc.
- **`gemini`**: click 🎤 to record (the button turns clay with a pulsing dot), click again to
  stop (a spinner shows). Gemini returns a clean transcript (punctuation added, filler words
  removed), which is typed into the pop-up. If Gemini fails, the app falls back to Windows voice
  typing and tells you why. If you've moved on by the time Gemini answers (the pop-up closed,
  or you paused the app), the transcript goes on your clipboard instead. The app never reopens
  the pop-up or switches the microphone on by itself.

To use Gemini:

1. Get an API key from Google AI Studio.
2. Start → "Edit environment variables for your account" → add `GEMINI_API_KEY`. The running app
   picks it up without a restart.
3. In the settings file set `"voice_engine": "gemini"`.

The default model is `gemini-3.8-flash`. If it's overloaded (503/504) or times out, the same
recording is retried on `gemini_fallback_model` (`gemini-3.1-flash-lite`). Audio is sent to
Google only in this mode, and only the recording you just made.

**Privacy:** on the Gemini API's free tier, Google's terms let it use what you send, including
these recordings, to improve its products. With billing enabled on the key's project, the
paid-tier terms apply instead. The default `windows` engine sends nothing to Google.

## Answer card (ask Claude without opening its window)

Press **Ctrl+Alt+Shift+Space** (or the Copilot key, once remapped: see below). Quick Entry Plus
opens its own input bar, styled like Claude's pop-up. Type a question and press Enter; the answer
streams into a card below the bar, as rendered Markdown with a **Copy** link on each code block.
Ask follow-ups in the same bar: they continue the same conversation.

- **📷 / 🪟 / 🎤** on the bar (or Alt+S / Alt+W / Alt+M while it's focused) attach a screenshot
  or the window you were in, or dictate into the bar. Attached images go with your next question.
- **Esc** closes the bar and card and stops Claude. The conversation continues next time you open
  the bar. **New** starts a fresh one.
- **Open in Claude** copies the conversation and opens a new chat in the Claude app, for when a
  quick question turns into real work.
- The card grows to at most about 60% of the screen, then scrolls. It stays on top, but it never
  takes focus by itself: a streaming answer won't pull you out of another window. If you click
  away before asking anything, the bar closes like Claude's pop-up.
- **Move it** by dragging the six-dot grip that appears at the bar's left edge on hover, the
  logo, or any gap around the buttons. The text field and buttons keep their clicks. The bar and
  card move together, with Windows' own dragging. Where you drop it is remembered for that
  monitor (in `%APPDATA%\QuickEntryPlus\positions.json`), and it's kept inside the screen's
  work area, never off-screen or under the taskbar. Near the bottom of the screen the card opens
  above the bar. Double-click the grip, or Tray → **Reset position**, to put it back at the
  top centre.
- It opens and closes with a short fade and scale, the card grows smoothly as the answer
  streams, and three pulsing dots show while Claude thinks. All of it turns instant when Windows'
  **Animation effects** setting (Settings → Accessibility → Visual effects) is off, or with
  `"animations": false`.

**How it answers.** It runs **Claude Code in headless mode** on your own Claude Pro/Max sign-in.
There's no API key, and answers count towards your plan's usage like any Claude chat. Each
conversation runs:

- **in a scratch folder** (`%APPDATA%\QuickEntryPlus\workspace`), never in your projects;
- **with no tools at all**, so it can't read or change files or run commands (images are sent
  inside the message);
- **without your settings, hooks, plugins, MCP servers or `CLAUDE.md`**;
- **with a short system prompt**: *"You are a quick desktop assistant. Answer concisely; the user
  is in the middle of other work."*

A Claude process is started as soon as you open the bar, so answers begin about a second after
Enter. Windows kills it, along with anything it started, if Quick Entry Plus ever exits, so no
processes are left behind. Captures are deleted when you press New and when the app starts.

**One-time setup:**
1. Have `claude.exe`: the copy bundled with Claude Desktop is found automatically, or a
   standalone `claude.exe` on `PATH`. An npm install's `claude.cmd` shim is never used, because
   cmd.exe would re-parse the arguments.
2. Sign Claude Code in. The copy bundled with Claude Desktop isn't signed in on its own. If the
   card says so, it shows the exact command to run: `& "<path to claude.exe>" auth login`. Sign in
   with your Claude account in the browser. Tray → **Open log**, or `QuickEntryPlus.exe
   --selftest`, shows whether it's signed in.
3. Point the Copilot key at the bar: **PowerToys → Keyboard Manager → Remap a shortcut**. Edit
   your existing mapping **Win+Shift+F23 → Ctrl+Alt+Space** so it maps to **Ctrl+Alt+Shift+Space**
   instead. Claude's own pop-up still opens with Ctrl+Alt+Space from the keyboard, with the
   docked toolbar as before.

**Gemini** isn't offered as a second backend: Gemini CLI no longer accepts a Google sign-in for
personal accounts, and an API key is the only other way in.

### More ways to ask

Each of these can be switched on or off in the settings file. Push-to-talk is **off** by
default (it changes what Caps Lock does and sends audio to Google): turn it on with
`"push_to_talk": true`.

| Feature | How | Notes |
|---|---|---|
| **Push-to-talk** (off by default) | Hold **Caps Lock**, speak, release | A small "Listening…" pill shows while you hold the key. Your words are transcribed with Gemini and sent straight to the answer card, unless the bar already has your typing, an attachment or a chip: then they're added and you press Enter. **Esc** while holding discards the recording. While this is on, Caps Lock doesn't toggle capitals; **Shift+Caps Lock** still does. Needs `GEMINI_API_KEY` (and Google may use free-tier audio). A quick tap does nothing. Locking Windows, sleep, a UAC prompt, an admin window coming to the front, Pause, or 5 minutes of holding all discard the recording (nothing is uploaded) |
| **Double-tap** | Tap **Ctrl** twice quickly (within ~300 ms) | Opens the bar. Shortcuts like Ctrl+C, holding Ctrl, or typing between the taps don't count. Can be Alt instead |
| **Recent** | The 🕘 button on the bar | Your last 10 conversations, titled by their first question. Choosing one shows its history and continues it with `--resume`; if Claude Code no longer has that session, the conversation continues in a new one with the old text sent as context. Stored in plain text in `%APPDATA%\QuickEntryPlus\sessions.json` (saved after each answer, never after an error) |
| **Open full Claude** | **Ctrl+Alt+Shift+C** | Brings Claude Desktop's main window to the front (restoring it if minimised; never one of its dialogs), or starts Claude |
| **Ask about selection** | Select text anywhere, press **Ctrl+Alt+Shift+Q** | Opens the bar with a removable **Selected text** chip that's sent with your question. Where the app exposes its selection through UI Automation it's read directly, with no keystrokes and no clipboard use. Otherwise it copies the selection and puts your whole clipboard back (every format: text, HTML, images, files), unless something else was copied meanwhile. Windows Terminal, the console and Git Bash get Ctrl+Insert, so a running program is never interrupted; in other terminals (PuTTY, Alacritty, WezTerm, ConEmu, terminals inside VS Code or JetBrains…) it sends nothing and asks you to copy yourself. Password fields and admin windows are refused |
| **Clipboard chip** | Open the bar within 30 s of copying something | An **Attach clipboard** chip (or **Attach copied image**). Click to attach, ✕ to dismiss. Only copies made in other apps count, never ones marked private (password managers) and never this app's own; if the clipboard changes after the chip appears, nothing is attached |

**How push-to-talk and double-tap work.** They need a low-level keyboard hook, which sees every
keystroke on the system. It's kept minimal:
- it runs on its own thread and is installed only while one of them is on (and not while paused);
- it swallows nothing but the push-to-talk key (and Esc while that key is held), and passes
  every other key straight through (220 typed keystrokes arrived intact in testing);
- it does no logging or other slow work per key, because Windows silently removes a hook that
  takes too long;
- it ignores synthetic keystrokes, so PowerToys remaps and the app's own keystrokes (which carry
  their own tag) can't trigger it.

**PowerToys and other keyboard tools.** Low-level hooks run newest first. PowerToys Keyboard
Manager sees a key before this app only if it hooked in later, so whichever started last wins.
A key that PowerToys remaps reaches this app as a synthetic keystroke and is ignored, so you
can't remap another key *to* the push-to-talk key; remap Caps Lock itself (or pick another
`push_to_talk_key`) instead. If push-to-talk stops reacting after you restart PowerToys, toggle
`push_to_talk` off and on (or restart the app) so its hook is installed again.

All hotkeys are registered globally. If another program already owns one, the log says
`couldn't register …` and a message appears once.

## Settings

Tray → **Open settings file** opens `%APPDATA%\QuickEntryPlus\settings.json`. Changes apply as
soon as you save.

```json
{
  "toolbar_position": "auto",
  "popup_shortcut": "Ctrl+Alt+Space",
  "hotkeys": { "screenshot": "Alt+S", "window": "Alt+W", "voice": "Alt+M" },
  "voice_engine": "windows",
  "gemini_model": "gemini-3.8-flash",
  "gemini_fallback_model": "gemini-3.1-flash-lite",
  "transcript_cleanup": true,
  "push_to_talk": false,
  "ask_selection_hotkey": "Ctrl+Alt+Shift+Q"
}
```

| Setting | Values |
|---|---|
| `toolbar_position` | `auto` (right of the pop-up, below if no room), `right`, `below` |
| `ask_hotkey` | Opens the answer card's input bar (default `Ctrl+Alt+Shift+Space`). Always registered while the app runs (not while paused) |
| `voice_autosend` | `true`: a question dictated into the bar is sent as soon as it's transcribed (Gemini voice engine only) |
| `push_to_talk`, `push_to_talk_key` | `true`/`false` (default `false`); `CapsLock`, `ScrollLock`, `Pause`, `RightCtrl`, `RightAlt`, `Insert` or `F1`–`F24` |
| `double_tap`, `double_tap_key` | `true`/`false`; `Ctrl` or `Alt` |
| `recent_sessions` | `true`/`false`: keep the last 10 conversations and show 🕘 |
| `open_claude_hotkey`, `ask_selection_hotkey` | A hotkey, or `""` to turn it off (defaults `Ctrl+Alt+Shift+C`, `Ctrl+Alt+Shift+Q`). Ctrl+Alt+letter or digit without Shift is AltGr on many non-English layouts (Ctrl+Alt+Q types @ on German keyboards), so it's accepted but you get a warning |
| `clipboard_chip` | `true`/`false`: offer to attach something copied in the last 30 s |
| `animations` | `true`/`false` (default `true`): open/close, card, chip and button animations. Also off whenever Windows' Animation effects setting is off |
| `popup_shortcut` | Claude's quick entry shortcut, used to reopen the pop-up (after a cancelled screenshot, for example). Change it if you changed Claude's |
| `hotkeys` | Modifiers `Alt` `Ctrl` `Shift` `Win`, plus `A`–`Z`, `0`–`9`, `Space`, `Tab` or `Enter` (these need at least one modifier), or `F1`–`F24`. `""` turns a hotkey off |
| `voice_engine` | `windows` or `gemini` |
| `gemini_model`, `gemini_fallback_model` | Any Gemini model id that accepts audio |
| `transcript_cleanup` | `true`: clean transcript. `false`: word for word |

If the file isn't valid JSON, the app uses the defaults and tells you, but never overwrites your
file. A single bad value falls back to its default, with a message naming it.

## Tray menu

**Pause** (or left-click the icon) hides the toolbar and frees the hotkeys and keyboard hook
until you resume. It also discards any recording, cancels a screenshot or paste in progress, and
drops a transcription or selection copy that finishes while paused.
**Start with Windows**, **Open settings file**, **Open log**, **Reset position** (the answer
bar back to the top centre, forgetting where you dragged it), **Quit**.

Log: `%APPDATA%\QuickEntryPlus\qeplus.log` (1 MB, 3 old copies kept). Unexpected errors are
logged and shown as a small message; they never crash the app.

## Troubleshooting

| Problem | Try |
|---|---|
| No toolbar | Is it paused (grey icon)? Open the log: a "pop-up shown" line should appear each time you open the pop-up. If not, a Claude update may have changed the pop-up window; see [How it works](#how-it-works) |
| "Alt+S is taken by another app" | Pick another combo in `hotkeys` |
| Gemini always falls back | The log has the error. 503/504 means Google's model is overloaded; 400/403 usually means a bad key |
| "Couldn't find Claude's chat box" | The screenshot is on your clipboard: paste it with Ctrl+V |
| A captured window is blank or a splash screen | Windows suspends Store apps in the background: bring the window to the front first |
| "… is partly covered, so it can't be captured" | That window draws nothing for a normal capture, so the app would have to grab the screen, which would capture whatever covers it. Bring it to the front and try again |
| "The clipboard changed before pasting" | Something was copied while Claude was opening. Try again without copying in the meantime |
| Window capture fails for an app running as administrator | Windows blocks capturing elevated windows from a normal app |
| Mic does nothing in Gemini mode | Settings → Privacy & security → Microphone → allow desktop apps |
| Settings or log seem to be missing when run from a Claude Code terminal | Processes started inside Claude Desktop's MSIX container have AppData redirected to `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming`. Start the exe from Explorer instead |

## Build from source

Python 3.12:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m qeplus                 # run from source
.venv\Scripts\python -m qeplus --selftest      # check every component; results go to the log
powershell -ExecutionPolicy Bypass -File packaging\build.ps1   # dist\QuickEntryPlus.exe
```

`build.ps1` regenerates the icon, builds a single windowed exe with PyInstaller (version info
from `packaging\version_info.txt`), then runs the exe's `--selftest` and fails if any check
fails. The self-test also confirms that Claude's pop-up window is still recognised when Claude is
running, so a Claude update that changes the pop-up shows up at build time.

Self-checks (each runs on its own; input is mocked wherever something would be typed or sent):

```powershell
.venv\Scripts\python tests\check_settings.py   # settings, tray helpers, pause
.venv\Scripts\python tests\check_hotkeys.py    # parsing, release-before-fire, conflicts
.venv\Scripts\python tests\check_voice.py      # real 1 s mic recording; Gemini mocked
.venv\Scripts\python tests\check_deliver.py    # the paste guard
.venv\Scripts\python tests\check_window.py     # window capture and screen-grab fallback
.venv\Scripts\python tests\check_fixes.py      # every fix from REVIEW.md (dims the screen briefly)
.venv\Scripts\python tests\check_askbar.py     # answer card: rendering, window, wiring (Claude mocked)
.venv\Scripts\python tests\check_motion.py     # dragging, remembered positions, animations
.venv\Scripts\python tests\check_claude_cli.py # real Claude Code: stream, follow-up, resume, no orphans
.venv\Scripts\python tests\check_keys.py       # push-to-talk and double-tap logic, the real hook
.venv\Scripts\python tests\check_sessions.py   # recent sessions, including a real resume
.venv\Scripts\python tests\check_selection.py  # open Claude, ask about selection, clipboard chip
.venv\Scripts\python tests\check_review3.py    # fixes F-01..F-21 (fake Claude Code, no keys sent)
```

`spikes/` holds the probes used to work out how the pop-up behaves (inspector, focus, paste,
UI Automation) and the live end-to-end scripts. These send real keystrokes, so don't run them
while you're using another app. `spikes\smoke_exe.py` tests the installed exe. They aren't part
of the app. `spikes\out\` holds their screenshots, which include whatever was on your screen,
so don't share that folder.

## How it works

- **Detection** (`watcher.py`): `SetWinEventHook` for show, hide, move and foreground events. No
  polling: idle CPU measured at about 0.05% of one core. The pop-up is `claude.exe`'s
  `Chrome_WidgetWin_1` window titled "Claude" that is always-on-top, has no caption and isn't
  no-activate. Claude creates it once and shows and hides it. The window is 606×470 but mostly
  transparent; the visible bar is its top 56 px, which is what the toolbar lines up with.
- **Toolbar** (`toolbar.py`): a frameless Qt window with `WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW`,
  so clicks never move focus (verified: the pop-up closes when it loses focus). Colours, radius
  and border were sampled from screenshots of the pop-up, and everything is drawn as vectors so
  it's sharp at 100–200% scaling.
- **Screenshot** (`snip.py`): a dimmed overlay per screen; the region is grabbed after the overlay
  closes.
- **Window** (`capture.py`): `PrintWindow(PW_RENDERFULLCONTENT)`, cropped to the DWM frame. Falls
  back to a screen grab if the window draws nothing.
- **Delivery** (`deliver.py`, `uia.py`): Claude's own `claude://claude.ai/new?surface=chat` link,
  then the UI Automation check described above, then typing and Ctrl+V with `SendInput`.
- **Voice** (`voice.py`): `sounddevice` at 16 kHz mono, sent inline as WAV via `google-genai`
  with `thinking_level="low"`. The mic is open only while recording.

Known limits: a screenshot selection can't span two monitors, and your clipboard isn't restored
after a capture.
