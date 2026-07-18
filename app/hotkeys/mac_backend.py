"""
macOS global hotkeys via pynput, gated behind Apple's Input Monitoring
permission. Unlike Windows, a process cannot listen to system-wide
keystrokes here without the user explicitly flipping a switch in
System Settings > Privacy & Security > Accessibility (sometimes shown
as "Input Monitoring" depending on macOS version).
"""

from app.hotkeys.backend_base import HotkeyBackend

try:
    from pynput import keyboard as pynput_keyboard
except ImportError:
    pynput_keyboard = None

try:
    import Quartz
except ImportError:
    Quartz = None


# Qt's QKeySequence spells keys differently than pynput expects.
# This translates the tokens we actually use in DEFAULT_KEYBINDS.
_KEY_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl",
    "cmd": "cmd", "meta": "cmd", "command": "cmd",  # Qt's "Meta" == the Cmd key on macOS
    "alt": "alt", "option": "alt",
    "shift": "shift",
    "space": "space",
    "esc": "esc", "escape": "esc",
    "return": "enter", "enter": "enter",
    "tab": "tab",
    "del": "delete", "delete": "delete",
    "backspace": "backspace",
    "ins": "insert", "insert": "insert",
}


def _to_pynput_combo(key_str):
    """'Cmd+Shift+Space' -> '<cmd>+<shift>+<space>'. Returns None if unmappable."""
    tokens = []
    for part in (p.strip() for p in key_str.split("+") if p.strip()):
        lower = part.lower()
        mapped = _KEY_ALIASES.get(lower)
        if mapped:
            tokens.append(f"<{mapped}>")
        elif len(lower) == 1:
            tokens.append(lower)  # bare printable char, e.g. "p"
        elif lower.startswith("f") and lower[1:].isdigit():
            tokens.append(f"<{lower}>")  # F1-F12
        else:
            return None
    return "+".join(tokens) if tokens else None


class MacHotkeyBackend(HotkeyBackend):
    def __init__(self):
        self._listener = None
        self._bindings = {}

    def permission_status(self):
        if Quartz is None:
            return "unsupported"
        try:
            return "granted" if Quartz.CGPreflightListenEventAccess() else "denied"
        except Exception:
            return "unsupported"

    def request_permission(self):
        """
        Shows the native "Portal.app would like to control this computer"
        dialog. macOS only shows this once per install automatically —
        after a denial, the user must enable it manually in System Settings.
        """
        if Quartz is None:
            return
        try:
            Quartz.CGRequestListenEventAccess()
        except Exception:
            pass

    def unhook_all(self):
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self._bindings.clear()

    def register(self, key_str, callback):
        if pynput_keyboard is None:
            print("pynput is not installed; cannot register macOS global hotkeys.")
            return False
        combo = _to_pynput_combo(key_str)
        if combo is None:
            print(f"Could not map hotkey '{key_str}' to a macOS shortcut.")
            return False
        self._bindings[combo] = callback
        self._restart_listener()
        return True

    def _restart_listener(self):
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
        if not self._bindings:
            self._listener = None
            return
        # pynput's GlobalHotKeys runs its own background thread and fires
        # callbacks from that thread, not Qt's main thread. That's fine here
        # because the callback we pass in emits a Qt Signal — Qt automatically
        # queues signal emissions from a non-GUI thread onto the GUI thread,
        # so no extra thread-safety code is needed on our end.
        self._listener = pynput_keyboard.GlobalHotKeys(dict(self._bindings))
        self._listener.start()