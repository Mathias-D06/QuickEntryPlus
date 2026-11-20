"""Step 2 probe: can UI Automation tell Claude's chat message box from other text boxes? Read-only.

Lists every text box (Edit control or ARIA textbox) in each visible main Claude window, with
its name, ARIA role/properties, automation id, whether it has focus, and its ancestor chain.
Also prints the element that has keyboard focus right now. Clicks and types nothing.
Run: .venv\\Scripts\\python spikes\\uia_probe.py
"""

from __future__ import annotations

import os
import sys

import comtypes.client

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qeplus.win import find_windows, is_main_window, user32  # noqa: E402

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
auto = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
walker = auto.ControlViewWalker


def info(e) -> str:
    r = e.CurrentBoundingRectangle
    return (f"type={e.CurrentLocalizedControlType!r} name={e.CurrentName[:60]!r} "
            f"aria={e.CurrentAriaRole!r} ariaProps={e.CurrentAriaProperties[:80]!r} "
            f"id={e.CurrentAutomationId!r} class={e.CurrentClassName[:40]!r} "
            f"focus={bool(e.CurrentHasKeyboardFocus)} offscreen={bool(e.CurrentIsOffscreen)} "
            f"rect=({r.left},{r.top},{r.right},{r.bottom})")


def chain(e, depth=10) -> str:
    names = []
    for _ in range(depth):
        e = walker.GetParentElement(e)
        if not e:
            break
        label = e.CurrentName[:30] or e.CurrentAutomationId or e.CurrentLocalizedControlType
        names.append(f"{e.CurrentLocalizedControlType}:{label!r}")
    return " < ".join(names)


def main():
    textbox = auto.CreateOrCondition(
        auto.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, UIA.UIA_EditControlTypeId),
        auto.CreatePropertyCondition(UIA.UIA_AriaRolePropertyId, "textbox"))
    for hwnd in find_windows(is_main_window):
        if not user32.IsWindowVisible(hwnd):
            continue
        root = auto.ElementFromHandle(hwnd)
        found = root.FindAll(UIA.TreeScope_Descendants, textbox)
        print(f"main window {hwnd:#x}: {found.Length} text boxes")
        for i in range(found.Length):
            e = found.GetElement(i)
            print(f"  [{i}] {info(e)}\n       in {chain(e)}")
        docs = root.FindAll(UIA.TreeScope_Descendants, auto.CreatePropertyCondition(
            UIA.UIA_ControlTypePropertyId, UIA.UIA_DocumentControlTypeId))
        for i in range(docs.Length):
            d = docs.GetElement(i)
            url = d.GetCurrentPropertyValue(UIA.UIA_ValueValuePropertyId)
            print(f"  document name={d.CurrentName[:50]!r} url={str(url)[:90]!r} "
                  f"offscreen={bool(d.CurrentIsOffscreen)}")
    f = auto.GetFocusedElement()
    print(f"\nfocused now: {info(f)}\n  in {chain(f)}")


if __name__ == "__main__":
    main()
