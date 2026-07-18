import keyboard as _keyboard
from app.hotkeys.backend_base import HotkeyBackend


class KeyboardLibBackend(HotkeyBackend):
    def unhook_all(self):
        try:
            _keyboard.unhook_all()
        except Exception:
            pass

    def register(self, key_str, callback):
        kb_str = (
            key_str.lower()
            .replace("meta", "windows")
            .replace("return", "enter")
            .replace("del", "delete")
            .replace("ins", "insert")
        )
        try:
            _keyboard.add_hotkey(kb_str, callback)
            return True
        except Exception as e:
            print(f"Failed to bind global hotkey {kb_str}: {e}")
            return False