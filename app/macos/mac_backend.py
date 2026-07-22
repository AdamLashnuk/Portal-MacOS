"""
macOS global hotkeys via pynput, gated behind Apple's Input Monitoring
permission.
"""

import threading
from app.hotkeys.backend_base import HotkeyBackend

try:
    from pynput import keyboard as pynput_keyboard
except ImportError:
    pynput_keyboard = None

try:
    import Quartz
except ImportError:
    Quartz = None


_KEY_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl",
    "cmd": "cmd", "meta": "cmd", "command": "cmd",
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
    tokens = []
    for part in (p.strip() for p in key_str.split("+") if p.strip()):
        lower = part.lower()
        mapped = _KEY_ALIASES.get(lower)
        if mapped:
            tokens.append(f"<{mapped}>")
        elif len(lower) == 1:
            tokens.append(lower)
        elif lower.startswith("f") and lower[1:].isdigit():
            tokens.append(f"<{lower}>")
        else:
            return None
    return "+".join(tokens) if tokens else None


class MacHotkeyBackend(HotkeyBackend):
    def __init__(self):
        self._lock = threading.Lock()
        self._hotkeys = {}  # combo string -> pynput.keyboard.HotKey
        self._listener = None

        if pynput_keyboard is not None:
            # Start the ONE listener for the whole app lifetime. We never
            # stop/restart this — that repeated stop/start was what caused
            # the crash. Adding/changing/removing hotkeys later only edits
            # self._hotkeys, guarded by the lock; the native macOS hook
            # itself is never touched again after this line.
            self._listener = pynput_keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
            )
            self._listener.start()

    def _on_press(self, key):
        with self._lock:
            hotkeys = list(self._hotkeys.values())
        for hk in hotkeys:
            try:
                hk.press(self._listener.canonical(key))
            except Exception:
                pass

    def _on_release(self, key):
        with self._lock:
            hotkeys = list(self._hotkeys.values())
        for hk in hotkeys:
            try:
                hk.release(self._listener.canonical(key))
            except Exception:
                pass

    def permission_status(self):
        if Quartz is None:
            return "unsupported"
        try:
            return "granted" if Quartz.CGPreflightListenEventAccess() else "denied"
        except Exception:
            return "unsupported"

    def request_permission(self):
        if Quartz is None:
            return
        try:
            Quartz.CGRequestListenEventAccess()
        except Exception:
            pass

    def unhook_all(self):
        with self._lock:
            self._hotkeys.clear()

    def register(self, key_str, callback):
        if pynput_keyboard is None:
            print("pynput is not installed; cannot register macOS global hotkeys.")
            return False
        combo = _to_pynput_combo(key_str)
        if combo is None:
            print(f"Could not map hotkey '{key_str}' to a macOS shortcut.")
            return False
        try:
            keys = pynput_keyboard.HotKey.parse(combo)
        except Exception as e:
            print(f"Could not parse hotkey '{key_str}': {e}")
            return False

        hotkey = pynput_keyboard.HotKey(keys, callback)
        with self._lock:
            self._hotkeys[combo] = hotkey
        return True