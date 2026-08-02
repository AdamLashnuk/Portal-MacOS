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

try:
    from AppKit import NSEvent
except ImportError:
    NSEvent = None


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


def _build_unmodified_key_map():
    """Build a layout-aware keycode map while running on the main thread."""
    if Quartz is None or NSEvent is None:
        return {}

    key_map = {}
    for keycode in range(128):
        try:
            event = Quartz.CGEventCreateKeyboardEvent(None, keycode, True)
            if Quartz.CGEventGetType(event) != Quartz.kCGEventKeyDown:
                continue
            ns_event = NSEvent.eventWithCGEvent_(event)
            characters = ns_event.charactersIgnoringModifiers() if ns_event else None
            if characters and len(characters) == 1:
                key_map[keycode] = characters.lower()
        except Exception:
            continue
    return key_map


def _normalize_key_for_hotkey(key, unmodified_key_map):
    """Return the key's unmodified character for global-hotkey matching.

    macOS translates Option+letter combinations into characters such as
    Option+P -> π before pynput sees them. HotKey.parse("<alt>+p") still
    waits for the literal character "p", so use the hardware keycode and
    AppKit's charactersIgnoringModifiers value for matching.
    """
    if pynput_keyboard is None:
        return key
    if not isinstance(key, pynput_keyboard.KeyCode) or key.vk is None:
        return key
    if key.char and len(key.char) == 1 and key.char.isascii():
        return key

    character = unmodified_key_map.get(key.vk)
    if character:
        return pynput_keyboard.KeyCode(vk=key.vk, char=character)
    return key


def _current_modifier_flags():
    if Quartz is None:
        return 0
    return Quartz.CGEventSourceFlagsState(
        Quartz.kCGEventSourceStateCombinedSessionState
    )


class _MacHotkeyBinding:
    """A single macOS hotkey matched against Quartz's live modifier flags."""

    def __init__(self, required_flags, trigger_key, callback):
        self.required_flags = required_flags
        self.trigger_key = trigger_key
        self.callback = callback
        self.active = False

    @classmethod
    def from_parsed_keys(cls, keys, callback):
        if pynput_keyboard is None or Quartz is None:
            raise RuntimeError("macOS hotkeys require pynput and Quartz")

        modifier_flags = {
            pynput_keyboard.Key.cmd: Quartz.kCGEventFlagMaskCommand,
            pynput_keyboard.Key.alt: Quartz.kCGEventFlagMaskAlternate,
            pynput_keyboard.Key.ctrl: Quartz.kCGEventFlagMaskControl,
            pynput_keyboard.Key.shift: Quartz.kCGEventFlagMaskShift,
        }

        required_flags = 0
        trigger_keys = []
        for key in keys:
            flag = modifier_flags.get(key)
            if flag is None:
                trigger_keys.append(key)
            else:
                required_flags |= flag

        if len(trigger_keys) != 1:
            raise ValueError("a global hotkey must contain exactly one trigger key")

        return cls(required_flags, trigger_keys[0], callback)

    def press(self, key, modifier_flags):
        if key != self.trigger_key:
            return
        if modifier_flags & self.required_flags != self.required_flags:
            return
        if self.active:
            return

        self.active = True
        self.callback()

    def release(self, key):
        if key == self.trigger_key:
            self.active = False


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
        # AppKit text translation is main-thread-only. Build this lookup while
        # ChatPanel is being constructed on the GUI thread, then let the
        # listener thread perform plain dictionary lookups.
        self._unmodified_key_map = _build_unmodified_key_map()

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
        canonical_key = self._listener.canonical(
            _normalize_key_for_hotkey(key, self._unmodified_key_map)
        )
        modifier_flags = _current_modifier_flags()
        for hk in hotkeys:
            try:
                hk.press(canonical_key, modifier_flags)
            except Exception as e:
                print(f"[DEBUG] hk.press error: {e}")

    def _on_release(self, key):
        print(f"[DEBUG] on_release: {key}")
        if key == pynput_keyboard.Key.caps_lock:
            print("[DEBUG] on_release caught caps_lock, ignoring")
            return
        with self._lock:
            hotkeys = list(self._hotkeys.values())
        canonical_key = self._listener.canonical(
            _normalize_key_for_hotkey(key, self._unmodified_key_map)
        )
        for hk in hotkeys:
            try:
                hk.release(canonical_key)
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
            hotkey = _MacHotkeyBinding.from_parsed_keys(keys, callback)
        except Exception as e:
            print(f"Could not parse hotkey '{key_str}': {e}")
            return False

        with self._lock:
            self._hotkeys[combo] = hotkey
        print(f"[DEBUG] register: combo={combo} registered successfully")
        return True
