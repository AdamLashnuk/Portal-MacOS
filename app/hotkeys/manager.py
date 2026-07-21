import sys
from PySide6.QtCore import QObject

from app.hotkeys.windows_backend import KeyboardLibBackend

if sys.platform == "darwin":
    from app.hotkeys.mac_backend import MacHotkeyBackend


class HotkeyManager(QObject):
    """Cross-platform facade. ChatPanel talks only to this."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.backend = MacHotkeyBackend() if sys.platform == "darwin" else KeyboardLibBackend()

    def permission_status(self):
        return self.backend.permission_status()

    def request_permission(self):
        self.backend.request_permission()

    def unhook_all(self):
        self.backend.unhook_all()

    def commit(self):
        self.backend.commit()

    def register(self, key_str, callback):
        return self.backend.register(key_str, callback)