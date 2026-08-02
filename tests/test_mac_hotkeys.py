import sys
import unittest
from unittest.mock import patch

from app.macos.mac_backend import (
    _MacHotkeyBinding,
    _build_unmodified_key_map,
    _normalize_key_for_hotkey,
    _to_pynput_combo,
    pynput_keyboard,
)

try:
    import Quartz
except ImportError:
    Quartz = None


@unittest.skipUnless(sys.platform == "darwin", "macOS-specific hotkey behavior")
class MacHotkeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.unmodified_key_map = _build_unmodified_key_map()

    def make_binding(self, shortcut):
        activations = []
        combo = _to_pynput_combo(shortcut)
        keys = pynput_keyboard.HotKey.parse(combo)
        binding = _MacHotkeyBinding.from_parsed_keys(
            keys, lambda: activations.append(True)
        )
        return binding, activations

    def canonical_key(self, key):
        if isinstance(key, pynput_keyboard.KeyCode) and key.char is not None:
            return pynput_keyboard.KeyCode.from_char(key.char.lower())
        return key

    def test_command_option_p_triggers_without_control(self):
        binding, activations = self.make_binding("Meta+Alt+P")

        option_p_event = pynput_keyboard.KeyCode(vk=35, char="π")
        normalized = self.canonical_key(
            _normalize_key_for_hotkey(option_p_event, self.unmodified_key_map)
        )
        flags = (
            Quartz.kCGEventFlagMaskCommand |
            Quartz.kCGEventFlagMaskAlternate
        )

        binding.press(normalized, flags)
        self.assertEqual(activations, [True])

    def test_listener_normalization_does_not_call_appkit(self):
        class ExplodingNSEvent:
            @staticmethod
            def eventWithCGEvent_(event):
                raise AssertionError("AppKit must not run on the listener thread")

        option_p_event = pynput_keyboard.KeyCode(vk=35, char="π")
        with patch("app.macos.mac_backend.NSEvent", ExplodingNSEvent):
            normalized = _normalize_key_for_hotkey(
                option_p_event, self.unmodified_key_map
            )

        self.assertEqual(normalized, pynput_keyboard.KeyCode.from_char("p"))

    def test_another_command_option_letter_triggers(self):
        binding, activations = self.make_binding("Meta+Alt+R")
        flags = (
            Quartz.kCGEventFlagMaskCommand |
            Quartz.kCGEventFlagMaskAlternate
        )
        option_r_event = pynput_keyboard.KeyCode(vk=15, char="®")
        normalized = self.canonical_key(
            _normalize_key_for_hotkey(option_r_event, self.unmodified_key_map)
        )

        binding.press(normalized, flags)
        self.assertEqual(activations, [True])

    def test_missing_command_or_option_does_not_trigger(self):
        binding, activations = self.make_binding("Meta+Alt+P")
        key = pynput_keyboard.KeyCode.from_char("p")

        binding.press(key, Quartz.kCGEventFlagMaskCommand)
        binding.press(key, Quartz.kCGEventFlagMaskAlternate)
        self.assertEqual(activations, [])

    def test_control_option_and_command_shift_still_trigger(self):
        control_binding, control_activations = self.make_binding("Ctrl+Alt+P")
        control_binding.press(
            pynput_keyboard.KeyCode.from_char("p"),
            Quartz.kCGEventFlagMaskControl | Quartz.kCGEventFlagMaskAlternate,
        )

        command_binding, command_activations = self.make_binding("Meta+Shift+Space")
        command_binding.press(
            pynput_keyboard.KeyCode.from_vk(pynput_keyboard.Key.space.value.vk),
            Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskShift,
        )

        self.assertEqual(control_activations, [True])
        self.assertEqual(command_activations, [True])

    def test_extra_modifiers_are_allowed(self):
        binding, activations = self.make_binding("Meta+Alt+P")
        flags = (
            Quartz.kCGEventFlagMaskCommand |
            Quartz.kCGEventFlagMaskAlternate |
            Quartz.kCGEventFlagMaskControl
        )

        binding.press(pynput_keyboard.KeyCode.from_char("p"), flags)
        self.assertEqual(activations, [True])

    def test_key_repeat_waits_for_release(self):
        binding, activations = self.make_binding("Meta+Alt+P")
        key = pynput_keyboard.KeyCode.from_char("p")
        flags = (
            Quartz.kCGEventFlagMaskCommand |
            Quartz.kCGEventFlagMaskAlternate
        )

        binding.press(key, flags)
        binding.press(key, flags)
        self.assertEqual(activations, [True])

        binding.release(key)
        binding.press(key, flags)
        self.assertEqual(activations, [True, True])


if __name__ == "__main__":
    unittest.main()
