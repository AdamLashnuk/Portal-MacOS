import os
import json
import uuid
import re
import shutil
import threading
import time
from PySide6.QtWidgets import (QFileDialog, QWidget, QPushButton, QVBoxLayout, QHBoxLayout, QLabel,
                               QFrame, QRubberBand, QGraphicsOpacityEffect, QSizePolicy,
                               QScrollArea, QDialog, QLineEdit, QListWidget, QListWidgetItem,
                               QStackedWidget, QMenu, QInputDialog)
from PySide6.QtCore import Qt, QUrl, QSize, QTimer, QSettings, QPropertyAnimation, QEasingCurve, Signal, QPoint, QRect, \
    QStandardPaths, \
    QParallelAnimationGroup, QSequentialAnimationGroup, QObject
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QCursor, QShortcut, QKeySequence, QPen
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage, QWebEngineSettings

from app.setting_panel import SettingPanel
from app.hotkeys.manager import HotkeyManager
from app.hotkeys.key_sequences import canonical_to_qt
from app.macos.window_level import WindowLevelController
from app.tab_reorder import ReorderableTabButton, TabReorderController
from app.utils import get_asset_path


def create_line_icon(shape, color, active_color=None, size=12, stroke_width=2):
    icon = QIcon()
    colors = (
        (QIcon.Mode.Normal, QColor(color)),
        (QIcon.Mode.Active, QColor(active_color or color)),
    )

    for mode, line_color in colors:
        pixmap = QPixmap(size * 2, size * 2)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(
            line_color,
            stroke_width,
            Qt.SolidLine,
            Qt.RoundCap,
            Qt.RoundJoin,
        ))

        inset = 2
        far_edge = size - inset
        center = size // 2
        if shape == "plus":
            painter.drawLine(QPoint(inset, center), QPoint(far_edge, center))
            painter.drawLine(QPoint(center, inset), QPoint(center, far_edge))
        elif shape == "close":
            painter.drawLine(QPoint(inset, inset), QPoint(far_edge, far_edge))
            painter.drawLine(QPoint(far_edge, inset), QPoint(inset, far_edge))
        else:
            raise ValueError(f"Unsupported line icon shape: {shape}")

        painter.end()
        icon.addPixmap(pixmap, mode)

    return icon


class PortalWebEngineView(QWebEngineView):
    """QWebEngineView that supports real JavaScript popup windows.

    OAuth providers (including Claude's Google sign-in flow) commonly launch
    authentication with window.open()/target=_blank. A plain QWebEngineView
    has nowhere to put that new window, so the click can appear to do nothing.

    The popup deliberately uses the SAME QWebEngineProfile as its opener so
    cookies/session state created during sign-in are visible to the original
    Claude tab when the popup closes.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup_windows = []

    def createWindow(self, window_type):
        popup = PortalWebEngineView()
        popup.setAttribute(Qt.WA_DeleteOnClose, True)
        popup.setWindowTitle("Portal Sign In")
        popup.resize(560, 760)

        # Critical: keep OAuth cookies/session in the same profile as the
        # browser that opened this popup.
        opener_page = self.page()
        if opener_page is not None:
            popup_page = QWebEnginePage(opener_page.profile(), popup)
            popup.setPage(popup_page)

        # Match the capabilities enabled on Portal's normal embedded tabs.
        popup_settings = popup.settings()
        popup_settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        popup_settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        popup_settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        popup_settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        popup_settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        popup_settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanPaste, True)

        # Keep a Python reference alive until the OAuth popup closes.
        self._popup_windows.append(popup)

        def forget_popup(*_):
            try:
                self._popup_windows.remove(popup)
            except ValueError:
                pass

        popup.destroyed.connect(forget_popup)
        popup.page().windowCloseRequested.connect(popup.close)

        popup.show()
        popup.raise_()
        popup.activateWindow()
        return popup


class GlobalHotkeyBridge(QObject):
    trigger = Signal(str)


class HorizontalWheelScrollArea(QScrollArea):
    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - delta)
        event.accept()


class AddLLMDialog(QDialog):
    llm_selected = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setFixedSize(200, 250)
        self.setStyleSheet("""
            QDialog {
                background-color: #1f1f1f;
                border: 1px solid #333333;
                border-radius: 8px;
            }
            QLineEdit {
                background-color: #151515;
                border: 1px solid #333333;
                border-radius: 6px;
                color: white;
                padding: 6px 10px;
                font-family: "Segoe UI";
            }
            QListWidget {
                background-color: transparent;
                border: none;
                color: white;
                outline: none;
                font-family: "Segoe UI";
                margin-top: 5px;
            }
            QListWidget::item {
                padding: 8px;
                border-radius: 4px;
            }
            QListWidget::item:hover {
                background-color: #333333;
            }
            QListWidget::item:selected {
                background-color: #444444;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 2px 0px;
            }
            QScrollBar::handle:vertical {
                background-color: #555555;
                border-radius: 3px;
                min-height: 24px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #777777;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search LLMs...")
        self.search_bar.textChanged.connect(self.filter_list)
        layout.addWidget(self.search_bar)

        self.list_widget = QListWidget()
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_widget.itemClicked.connect(self.on_item_clicked)
        layout.addWidget(self.list_widget)

        self.all_llms = [
            {"name": "ChatGPT", "url": "https://chatgpt.com"},
            {"name": "Claude", "url": "https://claude.ai"},
            {"name": "Gemini", "url": "https://gemini.google.com"},
            {"name": "Perplexity", "url": "https://perplexity.ai"},
            {"name": "DeepSeek", "url": "https://chat.deepseek.com"},
            {"name": "Z.ai", "url": "https://z.ai/"}
        ]
        self.populate_list(self.all_llms)

    def populate_list(self, llm_list):
        self.list_widget.clear()
        for llm in llm_list:
            item = QListWidgetItem(llm["name"])
            item.setData(Qt.UserRole, llm["url"])
            self.list_widget.addItem(item)

    def filter_list(self, text):
        filtered = [llm for llm in self.all_llms if text.lower() in llm["name"].lower()]
        self.populate_list(filtered)

    def on_item_clicked(self, item):
        name = item.text()
        url = item.data(Qt.UserRole)
        self.llm_selected.emit(name, url)
        self.accept()


class ChatPanel(QWidget):
    def __init__(self, bubble=None):
        super().__init__()

        self.extra_profiles = {}
        self.add_llm_dialog = None

        self.bubble = bubble
        self.drag_position = None
        self.tab_animations = []

        self.resize_margin = 8
        self.resize_direction = None

        self.pending_geometry = None
        self.resize_timer = QTimer(self)
        self.resize_timer.setInterval(5)
        self.resize_timer.timeout.connect(self.apply_pending_geometry)

        self.local_shortcuts = []
        self.hotkey_bridge = GlobalHotkeyBridge()
        self.hotkey_bridge.trigger.connect(self.execute_hotkey_action)

        self.setup_window()
        self.setup_animation_pool()
        self.create_widgets()

        self.setting_panel = SettingPanel()
        self.setting_panel.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding
        )

        self.setting_panel.color_changed.connect(self.update_content_area_color)
        self.setting_panel.clear_current_data_requested.connect(
            self.clear_current_browsing_data
        )
        self.setting_panel.clear_all_data_requested.connect(
            self.clear_all_browsing_data
        )

        self.hotkey_manager = HotkeyManager(self)
        self._mac_permission_prompted = False

        self.setting_panel.keybinds_updated.connect(self.apply_keybinds)
        self.apply_keybinds(self.setting_panel.current_keybinds)

        self.create_layout()
        self.apply_real_minimum_size()

    def track_animation(self, animation):
        """Keep an animation alive only until its final frame has run."""
        self.tab_animations.append(animation)
        animation.finished.connect(lambda anim=animation: self.release_animation(anim))

    def release_animation(self, animation):
        try:
            self.tab_animations.remove(animation)
        except ValueError:
            return
        animation.deleteLater()

    def apply_real_minimum_size(self):
        """
        Computes the true minimum window size from the fully-assembled layout,
        rather than guessing at title bar / margin constants. Must run after
        create_layout() so self.container's real QVBoxLayout (with its actual
        margins/spacing) exists. Temporarily switches the content stack to the
        settings panel — the larger of the two views — so the layout's
        sizeHint reflects whichever page actually drives the minimum.
        """
        previous_widget = self.content_stack.currentWidget()
        self.content_stack.setCurrentWidget(self.setting_panel)

        browser_min = QSize(400, 400)
        layout_hint = self.container.layout().minimumSize()

        min_width = max(browser_min.width(), layout_hint.width())
        min_height = max(browser_min.height(), layout_hint.height())
        self.setMinimumSize(min_width, min_height)

        self.content_stack.setCurrentWidget(previous_widget)

    def apply_keybinds(self, keybinds_dict):
        self.hotkey_manager.unhook_all()

        for sc in self.local_shortcuts:
            sc.setParent(None)
            sc.deleteLater()
        self.local_shortcuts.clear()

        permission_blocked = False

        for action_id, data in keybinds_dict.items():
            key_str = data["key"]
            is_global = data["is_global"]

            if not key_str:
                continue

            if is_global:
                status = self.hotkey_manager.permission_status()
                if status == "denied":
                    permission_blocked = True
                    continue
                ok = self.hotkey_manager.register(
                    key_str,
                    lambda a=action_id: self.hotkey_bridge.trigger.emit(a),
                )
                if not ok:
                    print(f"Failed to bind global hotkey for {action_id}: {key_str}")
            else:
                sc = QShortcut(QKeySequence(canonical_to_qt(key_str)), self)
                sc.activated.connect(lambda a=action_id: self.execute_hotkey_action(a))
                self.local_shortcuts.append(sc)

        self.hotkey_manager.commit()

        if permission_blocked:
            self.prompt_for_mac_accessibility_permission()

    def prompt_for_mac_accessibility_permission(self):
        if self._mac_permission_prompted:
            return
        self._mac_permission_prompted = True
        self.hotkey_manager.request_permission()

    def execute_hotkey_action(self, action_id):
        if action_id == "summon":
            if self.isVisible():
                self.close_panel()
            else:
                if self.bubble:
                    self.bubble.open_chat()
                else:
                    self.show()
                    self.raise_()
                    self.activateWindow()

        elif action_id == "hide":
            self.close_panel()

        elif action_id == "next_llm":
            self.cycle_next_llm()

        elif action_id == "quick_refresh":
            if self.current_browser():
                self.current_browser().reload()

        elif action_id == "refresh":
            if self.current_browser():
                from PySide6.QtWebEngineCore import QWebEnginePage
                self.current_browser().page().action(QWebEnginePage.ReloadAndBypassCache).trigger()

        elif action_id == "pin_toggle":
            is_pinned = bool(self.windowFlags() & Qt.WindowStaysOnTopHint)

            panel_was_visible = self.isVisible()
            if is_pinned:
                self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
            else:
                self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

            if panel_was_visible:
                self.show()
                WindowLevelController.set_pinned(self, not is_pinned)
                if is_pinned:
                    self.lower()

            if self.bubble:
                bubble_was_visible = self.bubble.isVisible()
                if is_pinned:
                    self.bubble.setWindowFlags(self.bubble.windowFlags() & ~Qt.WindowStaysOnTopHint)
                else:
                    self.bubble.setWindowFlags(self.bubble.windowFlags() | Qt.WindowStaysOnTopHint)

                if bubble_was_visible:
                    self.bubble.show()
                    WindowLevelController.set_pinned(self.bubble, not is_pinned)
                    if is_pinned:
                        self.bubble.lower()

    def cycle_next_llm(self):
        ordered_llms = self.ordered_provider_tabs()
        if not ordered_llms: return
        current_idx = -1
        for i, llm in enumerate(ordered_llms):
            if llm["id"] == self.current_provider_id:
                current_idx = i
                break
        if current_idx == -1: current_idx = 0

        next_idx = (current_idx + 1) % len(ordered_llms)
        next_llm = ordered_llms[next_idx]

        self.current_provider = next_llm["name"]
        self.current_provider_id = next_llm["id"]
        self.save_setting("current_provider", self.current_provider)
        self.save_setting("current_provider_id", self.current_provider_id)

        self.open_llm_url(next_llm["name"], next_llm["url"], next_llm["id"])

    def save_setting(self, key, value):
        self.settings.setValue(key, value)
        self.settings.sync()

    def setup_window(self):

        self.settings = QSettings("MyLLMWidget", "Portal")
        self.current_provider = self.settings.value("current_provider", "ChatGPT")
        self.current_provider_id = self.settings.value("current_provider_id", None)

        active_str = self.settings.value("active_llms")
        if active_str:
            self.active_llms = json.loads(active_str)
            migrated = False
            for llm in self.active_llms:
                if "id" not in llm:
                    llm["id"] = str(uuid.uuid4())
                    migrated = True
            if migrated:
                self.save_setting("active_llms", json.dumps(self.active_llms))
        else:
            self.active_llms = [
                {"id": str(uuid.uuid4()), "name": "ChatGPT", "url": "https://chatgpt.com"},
                {"id": str(uuid.uuid4()), "name": "Claude", "url": "https://claude.ai"},
                {"id": str(uuid.uuid4()), "name": "Gemini", "url": "https://gemini.google.com"}
            ]
            # The generated IDs are referenced by the saved current tab and
            # tab order, so they must survive the first restart.
            self.save_setting("active_llms", json.dumps(self.active_llms))

        active_ids = [llm["id"] for llm in self.active_llms]
        if self.current_provider_id not in active_ids:
            if self.active_llms:
                first_llm = self.active_llms[0]
                self.current_provider = first_llm["name"]
                self.current_provider_id = first_llm["id"]
            else:
                self.current_provider_id = None
            self.save_setting("current_provider", self.current_provider)
            self.save_setting("current_provider_id", self.current_provider_id)

        saved_tab_order = self.settings.value("provider_tab_order")
        try:
            requested_order = json.loads(saved_tab_order) if saved_tab_order else []
        except (TypeError, json.JSONDecodeError):
            requested_order = []

        self.provider_tab_order = [
            llm_id for llm_id in requested_order if llm_id in active_ids
        ]
        self.provider_tab_order.extend(
            llm_id for llm_id in active_ids if llm_id not in self.provider_tab_order
        )

        saved_size = self.settings.value("window_size")
        if saved_size:
            self.resize(saved_size)
        else:
            self.resize(900, 700)

        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)
        self.setMouseTracking(True)

        self.setStyleSheet("""
            QWidget {
                background-color: transparent;
                color: #ececec;
                font-family: "Segoe UI";
            }

            QFrame#mainContainer {
                background-color: rgba(15, 15, 15, 180);
                border: 1px solid rgba(255, 255, 255, 20);
                border-radius: 24px;
            }

            QLabel#title {
                font-size: 20px;
                font-weight: 600;
            }

            QPushButton {
                background-color: #303030;
                color: #ececec;
                border: 1px solid #444444;
                border-radius: 10px;
                padding: 8px 14px;
                font-size: 14px;
            }

            QPushButton:hover {
                background-color: #3a3a3a;
            }

            QPushButton#closeButton {
                background-color: transparent;
                border: none;
                padding: 0px; 
                margin: 0px;
            }

            QPushButton#closeButton:hover {
                background-color: #333333;
                border-radius: 8px;
            }

            QPushButton#addButton {
                padding: 0px;
            }

            QPushButton#settingsButton {
                background-color: transparent;
                border: none;
                border-radius: 8px;
            }

            QPushButton#settingsButton:hover {
                background-color: #333333;
            }

        """)

    def create_widgets(self):
        self.container = QFrame()
        self.container.setObjectName("mainContainer")
        self.container.setMouseTracking(True)

        self.scroll_area = HorizontalWheelScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setFixedHeight(45)
        self.scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
        """)

        self.llm_container = QWidget()
        self.llm_layout = QHBoxLayout(self.llm_container)
        self.llm_layout.setContentsMargins(0, 0, 0, 0)
        self.llm_layout.setSpacing(10)

        self.tab_reorder_controller = TabReorderController(
            self.llm_container,
            self.llm_layout,
            self,
        )
        self.tab_reorder_controller.order_changed.connect(self.apply_provider_tab_order)
        self.tab_reorder_controller.order_committed.connect(self.save_provider_tab_order)

        self.add_button = QPushButton()
        self.add_button.setObjectName("addButton")
        self.add_button.setFixedSize(26, 26)
        self.add_button.setIcon(create_line_icon("plus", "#ececec"))
        self.add_button.setIconSize(QSize(12, 12))
        self.add_button.clicked.connect(self.open_add_llm_menu)

        self.scroll_area.setWidget(self.llm_container)
        self.render_active_llms()

        self.close_button = QPushButton()
        self.close_button.setObjectName("closeButton")
        self.close_button.setFixedSize(32, 32)
        self.close_button.setIcon(create_line_icon("close", "#b4b4b4", "#ffffff"))
        self.close_button.setIconSize(QSize(12, 12))
        self.close_button.clicked.connect(self.close_panel)

        self.settings_button = QPushButton()
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setFixedSize(32, 32)
        self.settings_button.clicked.connect(self.open_settings)

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        icon_path = get_asset_path(os.path.join("assets", "gearsettingsgrey.png"))

        icon_pixmap = QPixmap(icon_path)
        if not icon_pixmap.isNull():
            self.settings_button.setIcon(QIcon(icon_pixmap))
            self.settings_button.setIconSize(QSize(20, 20))

        self.browser_stack = QStackedWidget()
        self.browser_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.browsers = {}

        app_data_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        storage_path = os.path.join(app_data_dir, "Portal", "session_data")
        self.profile = self.create_browser_profile("llm_profile", storage_path)

        for llm in self.active_llms:
            self.add_browser_to_stack(
                llm["id"],
                llm["url"],
                load_immediately=llm["id"] == self.current_provider_id,
            )

        if self.current_provider_id and self.current_provider_id in self.browsers:
            self.browser_stack.setCurrentWidget(self.browsers[self.current_provider_id])
        elif self.active_llms:
            self.browser_stack.setCurrentWidget(self.browsers[self.active_llms[0]["id"]])

    def handle_download_requested(self, download):
        downloads_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        suggested_name = download.downloadFileName() or "download"
        suggested_path = os.path.join(downloads_dir, suggested_name)

        path, _ = QFileDialog.getSaveFileName(self, "Save File", suggested_path)
        if not path:
            download.cancel()
            return

        download.setDownloadDirectory(os.path.dirname(path))
        download.setDownloadFileName(os.path.basename(path))
        download.accept()

    def create_browser_profile(self, profile_name, storage_path):
        """Create a persistent profile with Portal's shared browser behavior."""
        os.makedirs(storage_path, exist_ok=True)

        profile = QWebEngineProfile(profile_name, self.browser_stack)
        profile.setPersistentStoragePath(storage_path)
        profile.setCachePath(os.path.join(storage_path, "cache"))
        profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)

        # Hardening: Set a proper Accept-Language header. A missing or default
        # Accept-Language header is a common bot detection signal.
        profile.setHttpAcceptLanguage("en-US,en;q=0.9")

        # Downloads belong to their profile. Connecting this here guarantees
        # that shared and isolated LLM tabs all open the same Save File dialog.
        profile.downloadRequested.connect(self.handle_download_requested)
        return profile

    def add_browser_to_stack(self, llm_id, url, load_immediately=False):
        browser = PortalWebEngineView()

        browser_policy = browser.sizePolicy()
        browser_policy.setHorizontalPolicy(QSizePolicy.Expanding)
        browser_policy.setVerticalPolicy(QSizePolicy.Expanding)
        browser_policy.setRetainSizeWhenHidden(True)
        browser.setSizePolicy(browser_policy)

        # Hardening: Explicitly enable typical browser capabilities. If these
        # remain off/default, the engine looks suspiciously limited.
        settings = browser.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)

        # Add these two lines to allow clipboard access
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanPaste, True)

        idx = self._find_llm_index(llm_id)
        llm_entry = self.active_llms[idx] if idx != -1 else {}
        profile = self.get_profile_for_entry(llm_entry)
        self.set_browser_profile(browser, profile)

        browser.setProperty("portal_url", url)
        browser.setProperty("portal_loaded", False)
        if load_immediately:
            self.ensure_browser_loaded(browser, url)

        self.browsers[llm_id] = browser
        self.browser_stack.addWidget(browser)

    @staticmethod
    def ensure_browser_loaded(browser, url=None):
        """Load a normal provider tab once, when the user first selects it."""
        if browser.property("portal_loaded"):
            return
        target_url = url or browser.property("portal_url")
        if target_url:
            browser.setProperty("portal_loaded", True)
            browser.setUrl(QUrl(target_url))

    def set_browser_profile(self, browser, profile):
        """Give a browser a page backed by the requested session profile."""
        page = QWebEnginePage(profile, browser)

        def grant_feature_permission(origin, feature):
            # Remove the if statement so it automatically grants all requested features
            # (Clipboard, Audio, Notifications, etc.)
            page.setFeaturePermission(origin, feature, QWebEnginePage.PermissionPolicy.PermissionGrantedByUser)

        page.featurePermissionRequested.connect(grant_feature_permission)
        browser.setPage(page)

    def get_profile_for_entry(self, llm_entry):
        profile_id = (llm_entry or {}).get("profile_id")
        if not profile_id:
            return self.profile

        if profile_id not in self.extra_profiles:
            storage_path = self.isolated_profile_storage_path(profile_id)

            profile = self.create_browser_profile(f"llm_isolated_{profile_id}", storage_path)
            self.extra_profiles[profile_id] = profile

        return self.extra_profiles[profile_id]

    @staticmethod
    def isolated_profile_storage_path(profile_id):
        """Return a path guaranteed to stay inside Portal's isolated-data root."""
        app_data_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        root = os.path.abspath(os.path.join(app_data_dir, "Portal", "session_data_isolated"))
        storage_path = os.path.abspath(os.path.join(root, str(profile_id)))
        if os.path.normcase(os.path.commonpath((root, storage_path))) != os.path.normcase(root):
            raise ValueError("Invalid isolated browser profile ID")
        return storage_path

    @staticmethod
    def remove_profile_storage(storage_path):
        """Remove a released WebEngine profile without blocking Portal's UI."""
        def remove_with_retries():
            for attempt in range(6):
                try:
                    shutil.rmtree(storage_path)
                    return
                except FileNotFoundError:
                    return
                except OSError as error:
                    if attempt == 5:
                        print(f"Failed to remove browser profile data at {storage_path}: {error}")
                        return
                    time.sleep(0.25 * (attempt + 1))

        threading.Thread(target=remove_with_retries, daemon=True).start()

    def release_unused_profile(self, profile_id):
        """Destroy and erase an isolated profile after its final tab is deleted."""
        if not profile_id or any(llm.get("profile_id") == profile_id for llm in self.active_llms):
            return
        profile = self.extra_profiles.pop(profile_id, None)
        if profile is None:
            return
        storage_path = self.isolated_profile_storage_path(profile_id)
        profile.destroyed.connect(
            lambda *_args, path=storage_path: self.remove_profile_storage(path)
        )
        profile.deleteLater()

    def current_browser(self):
        return self.browser_stack.currentWidget()

    def render_active_llms(self):
        self.tab_reorder_controller.reset()
        self.llm_buttons = {}

        for i in reversed(range(self.llm_layout.count())):
            item = self.llm_layout.itemAt(i)
            widget = item.widget()

            if not widget or widget != self.add_button:
                self.llm_layout.takeAt(i)
                if widget:
                    widget.deleteLater()

        if self.llm_layout.indexOf(self.add_button) == -1:
            self.llm_layout.addWidget(self.add_button, alignment=Qt.AlignVCenter)

        ordered_llms = self.ordered_provider_tabs()
        for i, llm in enumerate(ordered_llms):
            btn = ReorderableTabButton(llm["name"], llm["id"])
            btn.clicked.connect(
                lambda checked=False, name=llm["name"], url=llm["url"], llm_id=llm["id"]:
                self.open_llm_url(name, url, llm_id)
            )
            btn.drag_started.connect(self.tab_reorder_controller.begin_drag)
            btn.drag_moved.connect(self.tab_reorder_controller.move_drag)
            btn.drag_finished.connect(self.tab_reorder_controller.finish_drag)

            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, button=btn, llm_id=llm["id"]:
                self.show_llm_context_menu(button, llm_id)
            )

            self.llm_buttons[llm["id"]] = btn

            self.llm_layout.insertWidget(i, btn, alignment=Qt.AlignVCenter)

        self.llm_layout.addStretch()

        self.add_button.setEnabled(True)
        self.add_button.show()
        self.tab_reorder_controller.set_tabs(
            self.llm_buttons,
            [llm["id"] for llm in ordered_llms],
        )

    def ordered_provider_tabs(self):
        llms_by_id = {llm["id"]: llm for llm in self.active_llms}
        ordered = [
            llms_by_id[llm_id]
            for llm_id in self.provider_tab_order
            if llm_id in llms_by_id
        ]
        ordered_ids = {llm["id"] for llm in ordered}
        ordered.extend(llm for llm in self.active_llms if llm["id"] not in ordered_ids)
        return ordered

    def apply_provider_tab_order(self, ordered_ids):
        active_ids = {llm["id"] for llm in self.active_llms}
        self.provider_tab_order = [
            llm_id for llm_id in ordered_ids if llm_id in active_ids
        ]
        self.provider_tab_order.extend(
            llm["id"] for llm in self.active_llms
            if llm["id"] not in self.provider_tab_order
        )

    def save_provider_tab_order(self, ordered_ids):
        self.apply_provider_tab_order(ordered_ids)
        self.save_setting("provider_tab_order", json.dumps(self.provider_tab_order))

    def add_to_provider_tab_order(self, llm_id, after_id=None):
        current_order = [llm["id"] for llm in self.ordered_provider_tabs()]
        if llm_id in current_order:
            current_order.remove(llm_id)

        if after_id in current_order:
            current_order.insert(current_order.index(after_id) + 1, llm_id)
        else:
            current_order.append(llm_id)

        self.provider_tab_order = current_order
        self.save_setting("provider_tab_order", json.dumps(self.provider_tab_order))

    def remove_from_provider_tab_order(self, llm_id):
        self.provider_tab_order = [
            tab_id for tab_id in self.provider_tab_order if tab_id != llm_id
        ]
        self.save_setting("provider_tab_order", json.dumps(self.provider_tab_order))

    def show_llm_context_menu(self, button, llm_id):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #1f1f1f; border: 1px solid #333333; border-radius: 8px; padding: 4px; color: #ececec; }
            QMenu::item { padding: 6px 16px; border-radius: 4px; }
            QMenu::item:selected { background-color: #333333; }
            QMenu::separator { height: 1px; background: #333333; margin: 4px 8px; }
        """)

        rename_action = menu.addAction("Rename")
        duplicate_action = menu.addAction("Duplicate")
        default_action = menu.addAction("Set as Default")
        menu.addSeparator()
        delete_action = menu.addAction("Delete")

        if llm_id == self.current_provider_id:
            default_action.setEnabled(False)
            default_action.setText("Set as Default ✓")

        chosen = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

        if chosen == rename_action:
            self.rename_llm_entry(llm_id)
        elif chosen == duplicate_action:
            self.duplicate_llm_entry(llm_id)
        elif chosen == default_action:
            self.set_default_llm_entry(llm_id)
        elif chosen == delete_action:
            self.delete_llm_entry(llm_id)

    def _base_llm_name(self, name):
        return re.sub(r"\s\(\d+\)$", "", name)

    def _unique_llm_name(self, name):
        base_name = self._base_llm_name(name)
        existing_names = {llm["name"] for llm in self.active_llms}
        if base_name not in existing_names:
            return base_name
        n = 2
        while f"{base_name} ({n})" in existing_names:
            n += 1
        return f"{base_name} ({n})"


    def _find_llm_index(self, llm_id):
        for i, llm in enumerate(self.active_llms):
            if llm["id"] == llm_id: return i
        return -1

    def rename_llm_entry(self, llm_id):
        index = self._find_llm_index(llm_id)
        if index == -1: return

        old_name = self.active_llms[index]["name"]
        new_name, ok = QInputDialog.getText(self, "Rename", "New name:", text=old_name)
        new_name = new_name.strip()

        if not ok or not new_name or new_name == old_name: return

        self.active_llms[index]["name"] = new_name

        if llm_id == self.current_provider_id:
            self.current_provider = new_name
            self.save_setting("current_provider", self.current_provider)

        self.save_setting("active_llms", json.dumps(self.active_llms))
        self.render_active_llms()

    def duplicate_llm_entry(self, llm_id):
        index = self._find_llm_index(llm_id)
        if index == -1: return

        original = self.active_llms[index]
        new_name = self._unique_llm_name(original["name"])
        copy_entry = {
            "id": str(uuid.uuid4()),
            "name": new_name,
            "url": original["url"],
            "profile_id": original.get("profile_id"),  # None = shared main profile, or same isolated id
        }
        self.active_llms.insert(index + 1, copy_entry)
        self.add_to_provider_tab_order(copy_entry["id"], after_id=llm_id)

        self.add_browser_to_stack(copy_entry["id"], copy_entry["url"])

        self.save_setting("active_llms", json.dumps(self.active_llms))
        self.render_active_llms()

    def set_default_llm_entry(self, llm_id):
        index = self._find_llm_index(llm_id)
        if index == -1: return

        entry = self.active_llms[index]
        self.current_provider = entry["name"]
        self.current_provider_id = entry["id"]
        self.save_setting("current_provider", self.current_provider)
        self.save_setting("current_provider_id", self.current_provider_id)

        self.render_active_llms()

    def delete_llm_entry(self, llm_id):
        index = self._find_llm_index(llm_id)
        if index == -1:
            return
        self.play_delete_pop_animation(llm_id)

    def finish_delete_llm_entry(self, llm_id):
        index = self._find_llm_index(llm_id)
        if index == -1:
            self.render_active_llms()
            return

        deleting_current = llm_id == self.current_provider_id
        deleted_entry = self.active_llms.pop(index)
        self.remove_from_provider_tab_order(llm_id)

        if llm_id in self.browsers:
            browser_to_delete = self.browsers.pop(llm_id)
            self.browser_stack.removeWidget(browser_to_delete)
            for popup in list(browser_to_delete._popup_windows):
                popup.close()
            browser_to_delete.deleteLater()

        self.release_unused_profile(deleted_entry.get("profile_id"))

        if deleting_current:
            if self.active_llms:
                fallback = self.active_llms[0]
                self.current_provider = fallback["name"]
                self.current_provider_id = fallback["id"]
                if fallback["id"] in self.browsers:
                    fallback_browser = self.browsers[fallback["id"]]
                    self.browser_stack.setCurrentWidget(fallback_browser)
                    self.ensure_browser_loaded(fallback_browser, fallback["url"])
            else:
                self.current_provider = "ChatGPT"
                self.current_provider_id = None
            self.save_setting("current_provider", self.current_provider)
            self.save_setting("current_provider_id", self.current_provider_id)

        self.save_setting("active_llms", json.dumps(self.active_llms))
        self.render_active_llms()

    def setup_animation_pool(self):
        self.pool_drop = QLabel(self)
        self.pool_drop.setFixedSize(14, 14)
        self.pool_drop.setStyleSheet("QLabel { background-color: #ececec; border-radius: 7px; }")
        self.pool_drop.hide()
        self.pool_drop_opacity = QGraphicsOpacityEffect(self.pool_drop)
        self.pool_drop.setGraphicsEffect(self.pool_drop_opacity)

        self.pool_ripple = QLabel(self)
        self.pool_ripple.setStyleSheet(
            "QLabel { background-color: transparent; border: 2px solid rgba(165, 120, 255, 180); border-radius: 5px; }")
        self.pool_ripple.hide()
        self.pool_ripple_opacity = QGraphicsOpacityEffect(self.pool_ripple)
        self.pool_ripple.setGraphicsEffect(self.pool_ripple_opacity)

        self.pool_splash_dots = []
        for i in range(6):
            size = 6 if i % 2 else 8
            radius = size // 2
            dot = QLabel(self)
            dot.setFixedSize(size, size)
            dot.setStyleSheet(
                f"QLabel {{ background-color: rgba(170, 125, 255, 220); border: 1px solid rgba(235, 225, 255, 180); border-radius: {radius}px; }}")
            dot.hide()
            opacity = QGraphicsOpacityEffect(dot)
            dot.setGraphicsEffect(opacity)
            self.pool_splash_dots.append((dot, opacity))

        self.pool_pop_dots = []
        for i in range(6):
            size = 4 if i % 2 else 5
            radius = size // 2
            dot = QLabel(self)
            dot.setFixedSize(size, size)
            dot.setStyleSheet(
                f"QLabel {{ background-color: rgba(170, 125, 255, 220); border: 1px solid rgba(235, 225, 255, 180); border-radius: {radius}px; }}")
            dot.hide()
            opacity = QGraphicsOpacityEffect(dot)
            dot.setGraphicsEffect(opacity)
            self.pool_pop_dots.append((dot, opacity))

        self.pool_ghost_btn = QPushButton(self)
        self.pool_ghost_btn.setStyleSheet("""
            QPushButton {
                background-color: #303030;
                color: #ececec;
                border: 1px solid #444444;
                border-radius: 10px;
                padding: 8px 14px;
                font-size: 14px;
                font-family: "Segoe UI";
            }
        """)
        self.pool_ghost_btn.hide()
        self.pool_ghost_opacity = QGraphicsOpacityEffect(self.pool_ghost_btn)
        self.pool_ghost_btn.setGraphicsEffect(self.pool_ghost_opacity)

        self.pool_tab_clone = QPushButton(self)
        self.pool_tab_clone.hide()

        self.pool_plus_clone = QPushButton(self)
        self.pool_plus_clone.setObjectName("addButton")
        self.pool_plus_clone.setIcon(create_line_icon("plus", "#ececec"))
        self.pool_plus_clone.setIconSize(QSize(12, 12))
        self.pool_plus_clone.hide()

    def play_delete_pop_animation(self, llm_id):
        button = self.llm_buttons.get(llm_id)
        if not button:
            self.finish_delete_llm_entry(llm_id)
            return

        button_rect = self.widget_rect_in_panel(button)
        center = button_rect.center()
        start_width = button.width()
        start_height = button.height()

        button.setEnabled(False)
        button.setMinimumWidth(start_width)
        button.setMaximumWidth(start_width)
        button.setMinimumHeight(start_height)
        button.setMaximumHeight(start_height)

        button_opacity = QGraphicsOpacityEffect(button)
        button.setGraphicsEffect(button_opacity)
        button_opacity.setOpacity(0.0)

        ghost = self.pool_ghost_btn
        ghost.setText(button.text())
        ghost.setGeometry(button_rect)
        self.pool_ghost_opacity.setOpacity(1.0)
        ghost.show()
        ghost.raise_()
        ghost_opacity = self.pool_ghost_opacity

        start_rect = QRect(button_rect)

        pop_rect = QRect(
            start_rect.x() - 4,
            start_rect.y() - 3,
            start_rect.width() + 8,
            start_rect.height() + 6
        )

        shrink_rect = QRect(
            center.x() - 2,
            center.y() - 2,
            4,
            4
        )

        pop_anim = QPropertyAnimation(ghost, b"geometry")
        pop_anim.setDuration(115)
        pop_anim.setStartValue(start_rect)
        pop_anim.setEndValue(pop_rect)
        pop_anim.setEasingCurve(QEasingCurve.OutCubic)

        shrink_anim = QPropertyAnimation(ghost, b"geometry")
        shrink_anim.setDuration(230)
        shrink_anim.setStartValue(pop_rect)
        shrink_anim.setEndValue(shrink_rect)
        shrink_anim.setEasingCurve(QEasingCurve.InBack)

        fade_anim = QPropertyAnimation(ghost_opacity, b"opacity")
        fade_anim.setDuration(40)
        fade_anim.setStartValue(1.0)
        fade_anim.setEndValue(0.0)
        fade_anim.setEasingCurve(QEasingCurve.OutCubic)

        shrink_group = QParallelAnimationGroup(self)
        shrink_group.addAnimation(shrink_anim)
        shrink_group.addAnimation(fade_anim)

        particle_group = QParallelAnimationGroup(self)
        pop_offsets = [
            QPoint(-18, -8), QPoint(-12, 12), QPoint(15, -11),
            QPoint(19, 7), QPoint(-2, -18), QPoint(5, 17)
        ]
        pop_dots = []

        for i, offset in enumerate(pop_offsets):
            dot, dot_opacity = self.pool_pop_dots[i]
            dot.move(center.x() - (dot.width() // 2), center.y() - (dot.height() // 2))
            dot_opacity.setOpacity(1.0)
            dot.show()
            dot.raise_()

            pop_dots.append(dot)

            end_rect = QRect(
                center.x() + offset.x(),
                center.y() + offset.y(),
                1,
                1
            )

            dot_move = QPropertyAnimation(dot, b"geometry")
            dot_move.setDuration(210)
            dot_move.setStartValue(dot.geometry())
            dot_move.setEndValue(end_rect)
            dot_move.setEasingCurve(QEasingCurve.OutCubic)

            dot_fade = QPropertyAnimation(dot_opacity, b"opacity")
            dot_fade.setDuration(210)
            dot_fade.setStartValue(1.0)
            dot_fade.setEndValue(0.0)
            dot_fade.setEasingCurve(QEasingCurve.OutQuad)

            particle_group.addAnimation(dot_move)
            particle_group.addAnimation(dot_fade)

        collapse_min = QPropertyAnimation(button, b"minimumWidth")
        collapse_min.setDuration(320)
        collapse_min.setStartValue(start_width)
        collapse_min.setEndValue(0)
        collapse_min.setEasingCurve(QEasingCurve.OutCubic)

        collapse_max = QPropertyAnimation(button, b"maximumWidth")
        collapse_max.setDuration(320)
        collapse_max.setStartValue(start_width)
        collapse_max.setEndValue(0)
        collapse_max.setEasingCurve(QEasingCurve.OutCubic)

        collapse_group = QParallelAnimationGroup(self)
        collapse_group.addAnimation(collapse_min)
        collapse_group.addAnimation(collapse_max)

        def finish_pop():
            shrink_group.start()
            particle_group.start()

        def start_slide_after_pause():
            ghost.hide()  # <--- HIDE INSTEAD OF DELETE
            for dot in pop_dots:
                dot.hide()  # <--- HIDE INSTEAD OF DELETE
            QTimer.singleShot(180, collapse_group.start)

        def finish_delete():
            button.setGraphicsEffect(None)
            button.setMinimumWidth(0)
            button.setMaximumWidth(16777215)
            button.setMinimumHeight(0)
            button.setMaximumHeight(16777215)
            self.finish_delete_llm_entry(llm_id)

        pop_anim.finished.connect(finish_pop)
        shrink_group.finished.connect(start_slide_after_pause)
        collapse_group.finished.connect(finish_delete)

        self.track_animation(pop_anim)
        self.track_animation(shrink_group)
        self.track_animation(particle_group)
        self.track_animation(collapse_group)

        pop_anim.start()

    def open_add_llm_menu(self):
        if self.add_llm_dialog is not None:
            self.close_add_llm_dialog()
            return

        dialog = AddLLMDialog(self)
        self.add_llm_dialog = dialog
        dialog.llm_selected.connect(self.add_llm_to_bar)

        button_pos = self.add_button.mapToGlobal(QPoint(0, self.add_button.height()))
        dialog.move(button_pos.x() - (dialog.width() // 2), button_pos.y() + 5)

        def clear_dialog_reference(*_):
            if self.add_llm_dialog is dialog:
                self.add_llm_dialog = None

        dialog.finished.connect(clear_dialog_reference)
        dialog.destroyed.connect(clear_dialog_reference)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.search_bar.setFocus()

    def close_add_llm_dialog(self):
        dialog = self.add_llm_dialog
        if dialog is None:
            return

        self.add_llm_dialog = None
        dialog.close()

    def moveEvent(self, event):
        self.close_add_llm_dialog()
        super().moveEvent(event)

    def resizeEvent(self, event):
        self.close_add_llm_dialog()
        super().resizeEvent(event)

    def add_llm_to_bar(self, name, url):
        unique_name = self._unique_llm_name(name)
        new_llm = {
            "id": str(uuid.uuid4()),
            "name": unique_name,
            "url": url,
            "profile_id": str(uuid.uuid4()),   # fresh, isolated, logged-out session
        }
        self.play_add_llm_animation(new_llm)

    def widget_rect_in_panel(self, widget):
        top_left = widget.mapTo(self, QPoint(0, 0))
        return QRect(top_left, widget.size())

    def play_add_llm_animation(self, new_llm):
        old_plus_rect = self.widget_rect_in_panel(self.add_button)
        plus_center = old_plus_rect.center()

        self.add_button.setEnabled(False)

        drop = self.pool_drop
        opacity = self.pool_drop_opacity
        opacity.setOpacity(1.0)

        drop_start = QRect(plus_center.x() - 7, plus_center.y() - 70, 14, 14)
        drop_end = QRect(plus_center.x() - 4, plus_center.y() - 4, 8, 8)

        drop.setGeometry(drop_start)
        drop.show()
        drop.raise_()

        drop_move = QPropertyAnimation(drop, b"geometry")
        drop_move.setDuration(280)
        drop_move.setStartValue(drop_start)
        drop_move.setEndValue(drop_end)
        drop_move.setEasingCurve(QEasingCurve.InCubic)

        drop_fade = QPropertyAnimation(opacity, b"opacity")
        drop_fade.setDuration(280)
        drop_fade.setStartValue(1.0)
        drop_fade.setEndValue(0.15)
        drop_fade.setEasingCurve(QEasingCurve.InCubic)

        drop_group = QParallelAnimationGroup(self)
        drop_group.addAnimation(drop_move)
        drop_group.addAnimation(drop_fade)

        def after_drop():
            drop.hide()  # <--- HIDE INSTEAD OF DELETE
            self.active_llms.append(new_llm)
            self.add_to_provider_tab_order(new_llm["id"])
            self.save_setting("active_llms", json.dumps(self.active_llms))
            self.render_active_llms()

            self.add_browser_to_stack(new_llm["id"], new_llm["url"])
            QTimer.singleShot(0, lambda: self.play_plus_to_tab_animation(old_plus_rect, new_llm["id"]))

        drop_group.finished.connect(after_drop)
        self.track_animation(drop_group)
        drop_group.start()

    def play_water_splash(self, old_plus_rect, plus_center, new_llm):
        ripple = self.pool_ripple
        ripple_opacity = self.pool_ripple_opacity
        ripple_opacity.setOpacity(0.9)

        ripple_start = QRect(plus_center.x() - 5, plus_center.y() - 5, 10, 10)
        ripple_end = QRect(plus_center.x() - 28, plus_center.y() - 28, 56, 56)
        ripple.setGeometry(ripple_start)
        ripple.show()
        ripple.raise_()

        ripple_grow = QPropertyAnimation(ripple, b"geometry")
        ripple_grow.setDuration(240)
        ripple_grow.setStartValue(ripple_start)
        ripple_grow.setEndValue(ripple_end)
        ripple_grow.setEasingCurve(QEasingCurve.OutCubic)

        ripple_fade = QPropertyAnimation(ripple_opacity, b"opacity")
        ripple_fade.setDuration(240)
        ripple_fade.setStartValue(0.9)
        ripple_fade.setEndValue(0.0)
        ripple_fade.setEasingCurve(QEasingCurve.OutQuad)

        splash_group = QParallelAnimationGroup(self)
        splash_group.addAnimation(ripple_grow)
        splash_group.addAnimation(ripple_fade)

        splash_offsets = [
            QPoint(-30, -15), QPoint(-21, 18), QPoint(24, -18),
            QPoint(31, 11), QPoint(-5, -30), QPoint(8, 25)
        ]

        splash_widgets = [ripple]
        for i, offset in enumerate(splash_offsets):
            dot, opacity = self.pool_splash_dots[i]
            splash_widgets.append(dot)

            dot.move(plus_center.x() - (dot.width() // 2), plus_center.y() - (dot.height() // 2))
            opacity.setOpacity(1.0)
            dot.show()
            dot.raise_()

            dot_end = QRect(
                plus_center.x() + offset.x(),
                plus_center.y() + offset.y(),
                max(2, dot.width() - 3),
                max(2, dot.height() - 3)
            )

            dot_move = QPropertyAnimation(dot, b"geometry")
            dot_move.setDuration(260)
            dot_move.setStartValue(dot.geometry())
            dot_move.setEndValue(dot_end)
            dot_move.setEasingCurve(QEasingCurve.OutCubic)

            dot_fade = QPropertyAnimation(opacity, b"opacity")
            dot_fade.setDuration(260)
            dot_fade.setStartValue(1.0)
            dot_fade.setEndValue(0.0)
            dot_fade.setEasingCurve(QEasingCurve.OutQuad)

            splash_group.addAnimation(dot_move)
            splash_group.addAnimation(dot_fade)

        def start_tab_materialize():
            self.active_llms.append(new_llm)
            self.add_to_provider_tab_order(new_llm["id"])
            self.save_setting("active_llms", json.dumps(self.active_llms))
            self.render_active_llms()

            self.add_browser_to_stack(new_llm["id"], new_llm["url"])
            self.play_plus_to_tab_animation(old_plus_rect, new_llm["id"])

        def cleanup_splash():
            for widget in splash_widgets:
                widget.hide()  # <--- HIDE INSTEAD OF DELETE

        splash_group.finished.connect(cleanup_splash)
        self.track_animation(splash_group)
        splash_group.start()

    def play_plus_to_tab_animation(self, old_plus_rect, new_llm_id):
        new_tab = self.llm_buttons.get(new_llm_id)
        if not new_tab:
            self.add_button.setEnabled(True)
            return

        new_tab_rect = self.widget_rect_in_panel(new_tab)
        new_plus_rect = self.widget_rect_in_panel(self.add_button)

        new_tab.hide()
        self.add_button.hide()

        tab_clone = self.pool_tab_clone
        tab_clone.setText(new_tab.text())
        tab_clone.setGeometry(old_plus_rect)
        tab_clone.show()
        tab_clone.raise_()

        plus_clone = self.pool_plus_clone
        plus_clone.setFixedSize(self.add_button.size())
        plus_clone.setGeometry(old_plus_rect)
        plus_clone.show()
        plus_clone.raise_()

        tab_anim = QPropertyAnimation(tab_clone, b"geometry")
        tab_anim.setDuration(260)
        tab_anim.setStartValue(old_plus_rect)
        tab_anim.setEndValue(new_tab_rect)
        tab_anim.setEasingCurve(QEasingCurve.OutBack)

        plus_anim = QPropertyAnimation(plus_clone, b"geometry")
        plus_anim.setDuration(320)
        plus_anim.setStartValue(old_plus_rect)
        plus_anim.setEndValue(new_plus_rect)
        plus_anim.setEasingCurve(QEasingCurve.OutBack)

        group = QParallelAnimationGroup(self)
        group.addAnimation(tab_anim)
        group.addAnimation(plus_anim)

        def finish():
            tab_clone.hide()  # <--- HIDE INSTEAD OF DELETE
            plus_clone.hide()  # <--- HIDE INSTEAD OF DELETE
            new_tab.show()
            self.add_button.show()
            self.add_button.setEnabled(True)

        group.finished.connect(finish)
        self.track_animation(group)
        group.start()

    def create_layout(self):
        top_bar = QHBoxLayout()
        self.title_bar = QFrame()
        self.title_bar.setFixedHeight(45)

        top_bar.setContentsMargins(18, 4, 18, 4)
        top_bar.addWidget(self.scroll_area, alignment=Qt.AlignVCenter)
        top_bar.addStretch()
        top_bar.addWidget(self.settings_button, alignment=Qt.AlignVCenter)
        top_bar.addWidget(self.close_button, alignment=Qt.AlignVCenter)

        self.title_bar.setLayout(top_bar)

        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(12, 12, 12, 12)
        container_layout.addWidget(self.title_bar)

        self.content_stack = QStackedWidget()
        self.content_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.content_stack.addWidget(self.browser_stack)
        self.content_stack.addWidget(self.setting_panel)
        self.content_stack.setCurrentWidget(self.browser_stack)

        container_layout.addWidget(self.content_stack, 1)

        self.container.setLayout(container_layout)

        saved_base = self.settings.value("resize_color_base", None)
        saved_opacity = self.settings.value("resize_opacity", None)
        if saved_base is None or saved_opacity is None:
            legacy = self.settings.value("resize_color", "transparent")
            saved_base, saved_opacity = self._migrate_legacy_color(legacy)
        initial_color = self._compose_rgba(saved_base, int(saved_opacity))

        self.container.setStyleSheet(
            f"QFrame#mainContainer {{ background-color: {initial_color}; border: 1px solid rgba(255, 255, 255, 20); border-radius: 24px; }}")

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.container)
        self.setLayout(main_layout)
        self.show_browser()

    def _migrate_legacy_color(self, legacy_value):
        if legacy_value == "transparent":
            return "transparent", 50
        try:
            inner = legacy_value[legacy_value.index("(") + 1: legacy_value.index(")")]
            r, g, b, a = [int(p.strip()) for p in inner.split(",")]
            return f"rgb({r}, {g}, {b})", round((a / 255) * 100)
        except (ValueError, IndexError):
            return "rgb(15, 15, 15)", 86

    def _compose_rgba(self, base_color, opacity_percent):
        if base_color == "transparent":
            return "transparent"
        inner = base_color[base_color.index("(") + 1: base_color.index(")")]
        alpha = round((opacity_percent / 100) * 255)
        return f"rgba({inner}, {alpha})"

    def show_browser(self):
        self.content_stack.setCurrentWidget(self.browser_stack)

    def open_llm_url(self, name, url, llm_id=None):
        self.show_browser()
        if llm_id and llm_id in self.browsers:
            browser = self.browsers[llm_id]
            self.browser_stack.setCurrentWidget(browser)
            self.ensure_browser_loaded(browser, url)

            self.current_provider = name
            self.current_provider_id = llm_id
            self.save_setting("current_provider", self.current_provider)
            self.save_setting("current_provider_id", self.current_provider_id)

    def close_panel(self):
        self.reset_to_browser()
        if self.bubble:
            self.bubble.close_chat_with_animation()
        else:
            self.hide()

    def update_content_area_color(self, new_color):
        self.container.setStyleSheet(
            f"QFrame#mainContainer {{ background-color: {new_color}; border: 1px solid rgba(255, 255, 255, 20); border-radius: 24px; }}")
        self.save_setting("resize_color", new_color)

    def return_to_browser_after_clearing_data(self):
        self.show_browser()
        self.setting_panel.appearance_btn.setChecked(True)
        self.setting_panel.content_stack.setCurrentIndex(0)

    def clear_profile_browsing_data(self, profile, browsers):
        """Clear one profile, then reload its tabs after Qt finishes."""
        browsers = list(browsers)
        clear_storage = "window.localStorage.clear(); window.sessionStorage.clear();"
        pending_storage_callbacks = len(browsers)
        cleanup_started = False

        def start_profile_cleanup():
            nonlocal cleanup_started
            if cleanup_started:
                return
            cleanup_started = True
            reload_scheduled = False

            def schedule_reload():
                nonlocal reload_scheduled
                if reload_scheduled:
                    return
                reload_scheduled = True
                try:
                    profile.clearHttpCacheCompleted.disconnect(schedule_reload)
                except (RuntimeError, TypeError):
                    pass

                # Cookie deletion is asynchronous too. The short delay after
                # cache completion lets both WebEngine cleanup requests settle.
                def reload_browsers():
                    for browser in browsers:
                        try:
                            # Provider tabs explicitly use False until first
                            # opened; only reload the ones already loaded.
                            if browser.property("portal_loaded") is not False:
                                browser.reload()
                        except RuntimeError:
                            # The tab may have been closed during cleanup.
                            pass

                QTimer.singleShot(250, reload_browsers)

            profile.clearHttpCacheCompleted.connect(schedule_reload)
            profile.cookieStore().deleteAllCookies()
            profile.clearHttpCache()

            # Keep automatic reloading reliable even if WebEngine does not
            # emit its cache-completed signal for an already-empty cache.
            QTimer.singleShot(2000, schedule_reload)

        def storage_cleared(_result=None):
            nonlocal pending_storage_callbacks
            pending_storage_callbacks -= 1
            if pending_storage_callbacks <= 0:
                start_profile_cleanup()

        if browsers:
            for browser in browsers:
                browser.page().runJavaScript(clear_storage, storage_cleared)

            # A stalled provider page must not prevent cookies from clearing.
            QTimer.singleShot(1000, start_profile_cleanup)
        else:
            start_profile_cleanup()

    def clear_current_browsing_data(self):
        browser = self.current_browser()
        index = self._find_llm_index(self.current_provider_id)
        if browser is None or index == -1:
            return

        entry = self.active_llms[index]
        profile_id = entry.get("profile_id")
        profile_users = sum(
            1 for llm in self.active_llms
            if llm.get("profile_id") == profile_id
        )

        if profile_id and profile_users == 1:
            # This profile belongs only to the selected tab, so it is safe to
            # remove the whole session without affecting any other provider.
            profile = browser.page().profile()
            self.clear_profile_browsing_data(profile, [browser])
        else:
            # Legacy tabs and duplicated tabs can share a profile. Move only
            # the selected tab to a brand-new profile so the others stay
            # signed in while this tab starts with a clean session.
            new_profile_id = str(uuid.uuid4())
            entry["profile_id"] = new_profile_id
            self.save_setting("active_llms", json.dumps(self.active_llms))

            new_profile = self.get_profile_for_entry(entry)
            self.set_browser_profile(browser, new_profile)
            browser.setUrl(QUrl(entry["url"]))

        self.return_to_browser_after_clearing_data()

    def clear_all_browsing_data(self):
        browsers = list(self.browsers.values())
        browsers_by_profile = {
            profile: []
            for profile in [self.profile, *self.extra_profiles.values()]
        }
        for browser in browsers:
            profile = browser.page().profile()
            browsers_by_profile.setdefault(profile, []).append(browser)

        for profile, profile_browsers in browsers_by_profile.items():
            self.clear_profile_browsing_data(profile, profile_browsers)

        self.return_to_browser_after_clearing_data()

    def hideEvent(self, event):
        self.save_setting("window_size", self.size())
        super().hideEvent(event)

    def open_settings(self):
        if self.content_stack.currentWidget() is self.setting_panel:
            self.content_stack.setCurrentWidget(self.browser_stack)
        else:
            self.content_stack.setCurrentWidget(self.setting_panel)

    def get_resize_direction(self, pos):
        w = self.width()
        h = self.height()
        margin = 16
        x, y = pos.x(), pos.y()

        left = x < margin
        right = x > (w - margin)
        top = y < margin
        bottom = y > (h - margin)

        if left and top: return Qt.TopLeftSection
        if right and top: return Qt.TopRightSection
        if left and bottom: return Qt.BottomLeftSection
        if right and bottom: return Qt.BottomRightSection
        if left: return Qt.LeftSection
        if right: return Qt.RightSection
        if top: return Qt.TopSection
        if bottom: return Qt.BottomSection
        return None

    def update_cursor_shape(self, pos):
        direction = self.get_resize_direction(pos)
        if direction in (Qt.TopSection, Qt.BottomSection):
            self.setCursor(QCursor(Qt.SizeVerCursor))
        elif direction in (Qt.LeftSection, Qt.RightSection):
            self.setCursor(QCursor(Qt.SizeHorCursor))
        elif direction in (Qt.TopLeftSection, Qt.BottomRightSection):
            self.setCursor(QCursor(Qt.SizeFDiagCursor))
        elif direction in (Qt.TopRightSection, Qt.BottomLeftSection):
            self.setCursor(QCursor(Qt.SizeBDiagCursor))
        else:
            self.setCursor(QCursor(Qt.ArrowCursor))

    def hide_resize_heavy_content(self):
        """
        QWebEngineViews repaint badly while a frameless translucent window is being resized,
        especially from the left/top edges because the window position and size change together.
        Hide whichever heavy view is currently active, then show it again on release.

        The settings panel is included here too: it's a real, live Qt layout
        (sidebar + cards) that re-flows and re-measures itself on every resize
        tick. Left visible during a drag, it fights the outer ChatPanel's own
        timer-throttled resize for control of the geometry, which is what let
        the window land below its true minimum specifically while this page
        was the current one.
        """
        self.resize_hidden_widget = None
        current = self.content_stack.currentWidget()

        if current is self.browser_stack:
            self.resize_hidden_widget = self.browser_stack
        elif current is self.setting_panel:
            self.resize_hidden_widget = self.setting_panel

        if self.resize_hidden_widget:
            self.resize_hidden_widget.hide()

    def show_resize_heavy_content(self):
        hidden = getattr(self, "resize_hidden_widget", None)
        if hidden:
            hidden.show()
        self.resize_hidden_widget = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            position = event.position().toPoint()
            direction = self.get_resize_direction(position)

            if direction:
                self.resize_direction = direction
                self.initial_geometry = QRect(self.geometry())
                self.initial_global_pos = event.globalPosition().toPoint()
                self.pending_geometry = None

                self.hide_resize_heavy_content()
                self.resize_timer.start()

                event.accept()
            else:
                self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()

    # Resize the window based on the dragged edge or corner.
    def mouseMoveEvent(self, event):
        position = event.position().toPoint()

        if not event.buttons() & Qt.LeftButton:
            self.update_cursor_shape(position)
            return

        if self.resize_direction:
            delta = event.globalPosition().toPoint() - self.initial_global_pos
            geom = QRect(self.initial_geometry)
            min_w, min_h = self.minimumWidth(), self.minimumHeight()

            new_left = geom.left()
            new_top = geom.top()
            new_right = geom.right()
            new_bottom = geom.bottom()

            if self.resize_direction in (Qt.LeftSection, Qt.TopLeftSection, Qt.BottomLeftSection):
                new_left = min(geom.right() - min_w + 1, geom.left() + delta.x())

            if self.resize_direction in (Qt.RightSection, Qt.TopRightSection, Qt.BottomRightSection):
                new_right = max(geom.left() + min_w - 1, geom.right() + delta.x())
            #
            if self.resize_direction in (Qt.TopSection, Qt.TopLeftSection, Qt.TopRightSection):
                new_top = min(geom.bottom() - min_h + 1, geom.top() + delta.y())

            if self.resize_direction in (Qt.BottomSection, Qt.BottomLeftSection, Qt.BottomRightSection):
                new_bottom = max(geom.top() + min_h - 1, geom.bottom() + delta.y())

            # Final safety clamp: regardless of any drift in initial_geometry
            # (e.g. from the native window manager resolving a prior setGeometry
            # call to a slightly different size than requested), never let the
            # actually-applied rect be smaller than the real minimum. Clamping
            # here, on the rect we're about to apply, is more reliable than only
            # clamping the per-edge delta math against a baseline that may itself
            # already be stale.
            if new_right - new_left + 1 < min_w:
                if self.resize_direction in (Qt.LeftSection, Qt.TopLeftSection, Qt.BottomLeftSection):
                    new_left = new_right - min_w + 1
                else:
                    new_right = new_left + min_w - 1

            if new_bottom - new_top + 1 < min_h:
                if self.resize_direction in (Qt.TopSection, Qt.TopLeftSection, Qt.TopRightSection):
                    new_top = new_bottom - min_h + 1
                else:
                    new_bottom = new_top + min_h - 1

            target = QRect(QPoint(new_left, new_top), QPoint(new_right, new_bottom))

            if self.geometry() != target:
                self.pending_geometry = target.getRect()

            event.accept()

        elif self.drag_position:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def apply_pending_geometry(self):
        if self.pending_geometry is not None:
            left, top, width, height = self.pending_geometry
            min_w, min_h = self.minimumWidth(), self.minimumHeight()
            width = max(width, min_w)
            height = max(height, min_h)
            self.setGeometry(left, top, width, height)
            self.pending_geometry = None

    def mouseReleaseEvent(self, event):
        self.drag_position = None

        if self.resize_direction:
            self.resize_direction = None
            self.resize_timer.stop()
            self.apply_pending_geometry()
            self.show_resize_heavy_content()

        self.setCursor(QCursor(Qt.ArrowCursor))
        event.accept()

    def reset_to_browser(self):
        self.content_stack.setCurrentWidget(self.browser_stack)
