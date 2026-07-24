import sys

try:
    import objc
except ImportError:
    objc = None

# Cocoa window level constants (from NSWindow.h).
# "Normal" is the same floor regular app windows live on.
# "Floating" is a couple floors up — palettes, inspectors, etc.
NS_NORMAL_WINDOW_LEVEL = 0
NS_FLOATING_WINDOW_LEVEL = 3


class WindowLevelController:
    """
    Sets a widget's actual NSWindow level directly via PyObjC, bypassing
    Qt.WindowStaysOnTopHint. Needed because Qt.Tool windows on macOS are
    backed by NSPanel, which already floats above normal app windows by
    default — toggling Qt's flag doesn't reliably move it between the
    "floating" and "normal" tiers. Setting the level directly does.
    """

    @staticmethod
    def _ns_window(widget):
        if sys.platform != "darwin" or objc is None or widget is None:
            return None
        try:
            ns_view = objc.objc_object(c_void_p=int(widget.winId()))
            return ns_view.window()
        except Exception as e:
            print("WindowLevelController: could not resolve NSWindow:", e)
            return None

    @classmethod
    def set_pinned(cls, widget, pinned: bool):
        ns_window = cls._ns_window(widget)
        if ns_window is None:
            return
        try:
            level = NS_FLOATING_WINDOW_LEVEL if pinned else NS_NORMAL_WINDOW_LEVEL
            ns_window.setLevel_(level)
            print(f"WindowLevelController: set level to {'floating' if pinned else 'normal'}")
        except Exception as e:
            print("WindowLevelController: could not set window level:", e)