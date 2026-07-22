import sys

try:
    from AppKit import NSApplication, NSApplicationActivationPolicyRegular, NSApplicationActivationPolicyAccessory
    import objc
except ImportError:
    NSApplication = None
    objc = None

NS_WINDOW_STYLE_MASK_NONACTIVATING_PANEL = 1 << 7


class FocusPromoter:
    _depth = 0
    _saved_style_mask = None
    _saved_ns_window = None

    @classmethod
    def promote(cls, widget=None):
        if sys.platform != "darwin" or NSApplication is None:
            return
        cls._depth += 1
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        app.activateIgnoringOtherApps_(True)

        if widget is not None and objc is not None:
            try:
                ns_view = objc.objc_object(c_void_p=int(widget.winId()))
                ns_window = ns_view.window()
                if ns_window is not None:
                    current_mask = ns_window.styleMask()
                    cls._saved_style_mask = current_mask
                    cls._saved_ns_window = ns_window
                    new_mask = current_mask & ~NS_WINDOW_STYLE_MASK_NONACTIVATING_PANEL
                    ns_window.setStyleMask_(new_mask)
                    ns_window.makeKeyAndOrderFront_(None)
                    print("FocusPromoter: style mask cleared and window made key")
            except Exception as e:
                print("FocusPromoter: could not make window key:", e)

    @classmethod
    def demote(cls):
        if sys.platform != "darwin" or NSApplication is None:
            return
        cls._depth = max(0, cls._depth - 1)
        if cls._depth == 0:
            if cls._saved_ns_window is not None and cls._saved_style_mask is not None:
                try:
                    cls._saved_ns_window.setStyleMask_(cls._saved_style_mask)
                except Exception as e:
                    print("FocusPromoter: could not restore style mask:", e)
            cls._saved_ns_window = None
            cls._saved_style_mask = None
            NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)