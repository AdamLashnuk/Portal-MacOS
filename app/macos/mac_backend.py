"""
macOS global hotkeys via pynput, gated behind Apple's Input Monitoring
permission.
"""

import threading
from app.hotkeys.backend_base import HotkeyBackend

try:
    from pynput import keyboard as pynput_keyboard
    print(f"[DEBUG] pynput imported OK. Listener class = {pynput_keyboard.Listener}")
except ImportError as e:
    pynput_keyboard = None
    print(f"[DEBUG] pynput import FAILED: {e}")

try:
    import Quartz
    print("[DEBUG] Quartz imported OK")
except ImportError as e:
    Quartz = None
    print(f"[DEBUG] Quartz import FAILED: {e}")


_CAPS_LOCK_KEYCODE = 0x39

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
    combo = "+".join(tokens) if tokens else None
    print(f"[DEBUG] _to_pynput_combo({key_str!r}) -> {combo!r}")
    return combo


if pynput_keyboard is not None:
    class _SafeMacListener(pynput_keyboard.Listener):
        def _handle_message(self, proxy, event_type, event, refcon, injected):
            try:
                if Quartz is not None and event_type == Quartz.kCGEventFlagsChanged:
                    keycode = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventKeycode
                    )
                    if keycode == _CAPS_LOCK_KEYCODE:
                        print("[DEBUG] Caps Lock flagsChanged -> skipping")
                        return

                if Quartz is not None and event_type == Quartz.NSSystemDefined:
                    # pynput's own handling of this event type bridges into
                    # AppKit via NSEvent.eventWithCGEvent_(event) — confirmed
                    # via debug logging to be the actual crash site, firing as
                    # a companion event to Caps Lock on modern Mac keyboards,
                    # only while Portal's own window has focus. Portal has no
                    # way to bind media/system keys as hotkeys anyway (they're
                    # not in _KEY_ALIASES), so skipping this event type
                    # entirely costs no real functionality.
                    print("[DEBUG] NSSystemDefined event -> skipping")
                    return
            except Exception as e:
                print(f"[event filter] error inspecting event: {e}")
            return super()._handle_message(proxy, event_type, event, refcon, injected)





class MacHotkeyBackend(HotkeyBackend):
    def __init__(self):
        self._lock = threading.Lock()
        self._hotkeys = {}
        self._listener = None

        if pynput_keyboard is not None:
            listener_cls = _SafeMacListener if Quartz is not None else pynput_keyboard.Listener
            print(f"[DEBUG] Using listener class: {listener_cls}")
            try:
                self._listener = listener_cls(
                    on_press=self._on_press,
                    on_release=self._on_release,
                )
                self._listener.start()
                print("[DEBUG] Listener constructed and started successfully")
            except Exception as e:
                print(f"[DEBUG] Listener construction/start FAILED: {e}")

    def _on_press(self, key):
        print(f"[DEBUG] on_press: {key}")
        if key == pynput_keyboard.Key.caps_lock:
            print("[DEBUG] on_press caught caps_lock, ignoring")
            return
        with self._lock:
            hotkeys = list(self._hotkeys.values())
        for hk in hotkeys:
            try:
                hk.press(self._listener.canonical(key))
            except Exception as e:
                print(f"[DEBUG] hk.press error: {e}")

    def _on_release(self, key):
        print(f"[DEBUG] on_release: {key}")
        if key == pynput_keyboard.Key.caps_lock:
            print("[DEBUG] on_release caught caps_lock, ignoring")
            return
        with self._lock:
            hotkeys = list(self._hotkeys.values())
        for hk in hotkeys:
            try:
                hk.release(self._listener.canonical(key))
            except Exception as e:
                print(f"[DEBUG] hk.release error: {e}")

    def permission_status(self):
        if Quartz is None:
            print("[DEBUG] permission_status: Quartz is None -> 'unsupported'")
            return "unsupported"
        try:
            status = "granted" if Quartz.CGPreflightListenEventAccess() else "denied"
            print(f"[DEBUG] permission_status: {status}")
            return status
        except Exception as e:
            print(f"[DEBUG] permission_status error: {e} -> 'unsupported'")
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
        print(f"[DEBUG] register: combo={combo} registered successfully")
        return True