class HotkeyBackend:
    """Interface every platform-specific hotkey backend implements."""

    def permission_status(self):
        """'granted' | 'denied' | 'unsupported' (no permission concept on this OS)."""
        return "granted"

    def request_permission(self):
        """Trigger the OS-native permission prompt, if one exists."""
        pass

    def register(self, key_str, callback):
        """Register a single global hotkey. Returns True on success."""
        raise NotImplementedError

    def commit(self):
        """Called once after all register() calls in a batch are done.
        Backends that need to (re)start a listener only after all bindings
        are known should override this. No-op by default."""
        pass

    def unhook_all(self):
        raise NotImplementedError