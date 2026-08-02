import re
import sys


def _translate_modifiers(key_str, modifier_map):
    """Translate modifier tokens without changing the non-modifier key."""
    if not key_str:
        return ""

    modifier_names = "|".join(re.escape(name) for name in modifier_map)
    pattern = re.compile(rf"(^|\+)({modifier_names})(?=\+)", re.IGNORECASE)
    return pattern.sub(
        lambda match: match.group(1) + modifier_map[match.group(2).lower()],
        key_str,
    )


def canonical_to_qt(key_str, platform=None):
    """Convert Portal's physical-key vocabulary to Qt portable text.

    Portal consistently stores Meta as the Command key and Ctrl as the
    physical Control key. Qt's portable key-sequence names use the opposite
    mapping on macOS, so local QShortcuts need this conversion.
    """
    platform = sys.platform if platform is None else platform
    if platform != "darwin":
        return key_str

    return _translate_modifiers(
        key_str,
        {
            "meta": "Ctrl",
            "cmd": "Ctrl",
            "command": "Ctrl",
            "ctrl": "Meta",
            "control": "Meta",
        },
    )


def qt_to_canonical(key_str, platform=None):
    """Convert Qt portable text back to Portal's physical-key vocabulary."""
    platform = sys.platform if platform is None else platform
    if platform != "darwin":
        return key_str

    return _translate_modifiers(
        key_str,
        {
            "ctrl": "Meta",
            "meta": "Ctrl",
        },
    )
