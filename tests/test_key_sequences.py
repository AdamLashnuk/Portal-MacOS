import unittest

from app.hotkeys.key_sequences import canonical_to_qt, qt_to_canonical


class KeySequenceTranslationTests(unittest.TestCase):
    def test_mac_command_becomes_qt_control(self):
        self.assertEqual(canonical_to_qt("Meta+Shift+R", "darwin"), "Ctrl+Shift+R")

    def test_mac_physical_control_becomes_qt_meta(self):
        self.assertEqual(canonical_to_qt("Ctrl+R", "darwin"), "Meta+R")

    def test_captured_qt_sequence_returns_to_canonical_form(self):
        self.assertEqual(qt_to_canonical("Ctrl+Alt+P", "darwin"), "Meta+Alt+P")

    def test_plus_key_is_preserved(self):
        self.assertEqual(canonical_to_qt("Meta++", "darwin"), "Ctrl++")

    def test_other_platforms_are_unchanged(self):
        self.assertEqual(canonical_to_qt("Ctrl+R", "win32"), "Ctrl+R")
        self.assertEqual(qt_to_canonical("Ctrl+R", "linux"), "Ctrl+R")


if __name__ == "__main__":
    unittest.main()
