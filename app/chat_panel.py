import os
import json
import uuid
from app.hotkeys.manager import HotkeyManager
import re
from PySide6.QtWidgets import (QFileDialog, QWidget, QPushButton, QVBoxLayout, QHBoxLayout, QLabel,
                               QFrame, QRubberBand, QGraphicsOpacityEffect, QSizePolicy,
                               QScrollArea, QDialog, QLineEdit, QListWidget, QListWidgetItem,
                               QStackedWidget, QMenu, QInputDialog)
from PySide6.QtCore import Qt, QUrl, QSize, QTimer, QSettings, QPropertyAnimation, QEasingCurve, Signal, QPoint, QRect, \
    QStandardPaths, \
    QParallelAnimationGroup, QSequentialAnimationGroup, QObject
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QCursor, QShortcut, QKeySequence
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage, QWebEngineSettings

from app.setting_panel import SettingPanel
from app.utils import get_asset_path
from app.multitask.sender import MultitaskSender


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
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.Tool
        )
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
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search LLMs...")
        self.search_bar.textChanged.connect(self.filter_list)
        layout.addWidget(self.search_bar)
        self.add_llm_dialog = None
        self.list_widget = QListWidget()
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

        self.bubble = bubble
        self.drag_position = None
        self.tab_animations = []

        self.multitask_active = False
        self.multitask_view = None
        self.multitask_browsers = {}
        self.multitask_senders = {} 
        self.multitask_tab_button = None
        self.multitask_prompt_overlay = None

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
        self.setting_panel.clear_data_requested.connect(self.clear_browsing_data)

        self.hotkey_manager = HotkeyManager(self)
        self._mac_permission_prompted = False

        self.setting_panel.keybinds_updated.connect(self.apply_keybinds)
        self.apply_keybinds(self.setting_panel.current_keybinds)
        self.add_llm_dialog = None

        self.create_layout()
        self.apply_real_minimum_size()

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
                    key_str, lambda a=action_id: self.hotkey_bridge.trigger.emit(a)
                )
                if not ok:
                    print(f"Failed to bind global hotkey for {action_id}: {key_str}")
            else:
                sc = QShortcut(QKeySequence(key_str), self)
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
        # NOTE: after granting, macOS requires the app to be relaunched before
        # the permission takes effect — it won't apply to the already-running
        # process. Worth a small in-app banner here telling the user that;
        # I left it as a TODO rather than guessing at your UI style.

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

            if self.bubble:
                bubble_was_visible = self.bubble.isVisible()
                if is_pinned:
                    self.bubble.setWindowFlags(self.bubble.windowFlags() & ~Qt.WindowStaysOnTopHint)
                else:
                    self.bubble.setWindowFlags(self.bubble.windowFlags() | Qt.WindowStaysOnTopHint)

                if bubble_was_visible:
                    self.bubble.show()

    def cycle_next_llm(self):
        if not self.active_llms: return
        current_idx = -1
        for i, llm in enumerate(self.active_llms):
            if llm["id"] == self.current_provider_id:
                current_idx = i
                break
        if current_idx == -1 and self.active_llms: current_idx = 0

        next_idx = (current_idx + 1) % len(self.active_llms)
        next_llm = self.active_llms[next_idx]

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

        saved_size = self.settings.value("window_size")
        if saved_size:
            self.resize(saved_size)
        else:
            self.resize(900, 700)

        self.setWindowFlags(
            Qt.Popup |
            Qt.FramelessWindowHint
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
                color: #b4b4b4;
                font-size: 16px; 
                font-weight: 100;
                padding: 0px; 
                margin: 0px;
            }

            QPushButton#closeButton:hover {
                color: white;
                background-color: #333333;
                border-radius: 8px;
            }

            QPushButton#addButton {
                font-size: 20px;
                font-weight: bold;
                padding: 0px;
                padding-bottom: 6px; 
            }

            QPushButton#settingsButton {
                background-color: transparent;
                border: none;
                border-radius: 8px;
            }

            QPushButton#settingsButton:hover {
                background-color: #333333;
            }

            QPushButton#multitaskButton {
                background-color: rgba(99, 102, 241, 0.15);
                border: 1px solid rgba(129, 140, 248, 0.45);
                color: #ececec;
                border-radius: 10px;
                padding: 6px 12px;
                font-size: 13px;
                font-weight: 600;
            }

            QPushButton#multitaskButton:hover {
                background-color: rgba(99, 102, 241, 0.26);
                border: 1px solid rgba(165, 180, 252, 0.65);
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

        self.add_button = QPushButton("+")
        self.add_button.setObjectName("addButton")
        self.add_button.setFixedSize(26, 26)
        self.add_button.clicked.connect(self.open_add_llm_menu)

        self.scroll_area.setWidget(self.llm_container)
        self.render_active_llms()

        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("closeButton")
        self.close_button.setFixedSize(32, 32)
        self.close_button.clicked.connect(self.close_panel)

        self.settings_button = QPushButton()
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setFixedSize(32, 32)
        self.settings_button.clicked.connect(self.open_settings)

        self.multitask_button = QPushButton("Multitask")
        self.multitask_button.setObjectName("multitaskButton")
        self.multitask_button.setFixedHeight(32)
        self.multitask_button.clicked.connect(self.open_multitask_prompt)

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        icon_path = get_asset_path(os.path.join("assets", "gearsettingsgrey.png"))

        icon_pixmap = QPixmap(icon_path)
        if not icon_pixmap.isNull():
            self.settings_button.setIcon(QIcon(icon_pixmap))
            self.settings_button.setIconSize(QSize(20, 20))

        self.browser_stack = QStackedWidget()
        self.browser_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.browsers = {}

        self.profile = QWebEngineProfile("llm_profile", self.browser_stack)

        app_data_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)

        storage_path = os.path.join(app_data_dir, "Portal", "session_data")

        os.makedirs(storage_path, exist_ok=True)

        self.profile.setPersistentStoragePath(storage_path)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)

        # Hardening: Set a proper Accept-Language header. A missing or default
        # Accept-Language header is a common bot detection signal.
        self.profile.setHttpAcceptLanguage("en-US,en;q=0.9")

        self.profile.downloadRequested.connect(self.handle_download_requested)

        for llm in self.active_llms:
            self.add_browser_to_stack(llm["id"], llm["url"])

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

    def add_browser_to_stack(self, llm_id, url):
        browser = QWebEngineView()

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
        page = QWebEnginePage(profile, browser)

        def grant_feature_permission(origin, feature):
            # Remove the if statement so it automatically grants all requested features
            # (Clipboard, Audio, Notifications, etc.)
            page.setFeaturePermission(origin, feature, QWebEnginePage.PermissionPolicy.PermissionGrantedByUser)

        page.featurePermissionRequested.connect(grant_feature_permission)
        browser.setPage(page)
        browser.setUrl(QUrl(url))

        self.browsers[llm_id] = browser
        self.browser_stack.addWidget(browser)

    def get_profile_for_entry(self, llm_entry):
        profile_id = (llm_entry or {}).get("profile_id")
        if not profile_id:
            return self.profile

        if profile_id not in self.extra_profiles:
            app_data_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
            storage_path = os.path.join(app_data_dir, "Portal", "session_data_isolated", profile_id)
            os.makedirs(storage_path, exist_ok=True)

            profile = QWebEngineProfile(f"llm_isolated_{profile_id}", self.browser_stack)
            profile.setPersistentStoragePath(storage_path)
            profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
            profile.setHttpAcceptLanguage("en-US,en;q=0.9")
            self.extra_profiles[profile_id] = profile

        return self.extra_profiles[profile_id]

    def current_browser(self):
        return self.browser_stack.currentWidget()

    def render_active_llms(self):
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

        for i, llm in enumerate(self.active_llms):
            btn = QPushButton(llm["name"])
            btn.clicked.connect(
                lambda checked=False, name=llm["name"], url=llm["url"], llm_id=llm["id"]:
                self.open_llm_url(name, url, llm_id)
            )

            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, button=btn, llm_id=llm["id"]:
                self.show_llm_context_menu(button, llm_id)
            )

            self.llm_buttons[llm["id"]] = btn

            self.llm_layout.insertWidget(i, btn, alignment=Qt.AlignVCenter)

        if self.multitask_active:
            self.multitask_tab_button = QPushButton("Multitask")
            self.multitask_tab_button.setStyleSheet("""
                QPushButton {
                    background-color: rgba(99, 102, 241, 0.20);
                    color: #ffffff;
                    border: 1px solid rgba(129, 140, 248, 0.65);
                    border-radius: 10px;
                    padding: 8px 14px;
                    font-size: 14px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: rgba(99, 102, 241, 0.32);
                }
            """)
            self.multitask_tab_button.clicked.connect(self.show_multitask_tab)
            self.multitask_tab_button.setContextMenuPolicy(Qt.CustomContextMenu)
            self.multitask_tab_button.customContextMenuRequested.connect(
                lambda pos, button=self.multitask_tab_button: self.show_multitask_context_menu(button)
            )
            self.llm_layout.insertWidget(len(self.active_llms), self.multitask_tab_button, alignment=Qt.AlignVCenter)

        self.llm_layout.addStretch()

        self.add_button.setEnabled(True)
        self.add_button.show()

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
        del self.active_llms[index]

        if llm_id in self.browsers:
            browser_to_delete = self.browsers.pop(llm_id)
            self.browser_stack.removeWidget(browser_to_delete)
            browser_to_delete.deleteLater()

        if deleting_current:
            if self.active_llms:
                fallback = self.active_llms[0]
                self.current_provider = fallback["name"]
                self.current_provider_id = fallback["id"]
                if fallback["id"] in self.browsers:
                    self.browser_stack.setCurrentWidget(self.browsers[fallback["id"]])
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

        self.pool_plus_clone = QPushButton("+", self)
        self.pool_plus_clone.setObjectName("addButton")
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

        self.tab_animations.append(pop_anim)
        self.tab_animations.append(shrink_group)
        self.tab_animations.append(particle_group)
        self.tab_animations.append(collapse_group)

        pop_anim.start()

    def open_add_llm_menu(self):
        if self.add_llm_dialog is not None:
            self.add_llm_dialog.close()
            self.add_llm_dialog = None
            return

        dialog = AddLLMDialog(self)
        self.add_llm_dialog = dialog

        dialog.llm_selected.connect(self.add_llm_to_bar)

        button_pos = self.add_button.mapToGlobal(
            QPoint(0, self.add_button.height())
        )

        dialog.move(
            button_pos.x() - (dialog.width() // 2),
            button_pos.y() + 5
        )

        def clear_dialog_reference():
            if self.add_llm_dialog is dialog:
                self.add_llm_dialog = None

        dialog.finished.connect(clear_dialog_reference)
        dialog.destroyed.connect(clear_dialog_reference)

        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.search_bar.setFocus()

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
            self.save_setting("active_llms", json.dumps(self.active_llms))
            self.render_active_llms()

            self.add_browser_to_stack(new_llm["id"], new_llm["url"])
            QTimer.singleShot(0, lambda: self.play_plus_to_tab_animation(old_plus_rect, new_llm["id"]))

        drop_group.finished.connect(after_drop)
        self.tab_animations.append(drop_group)
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
            self.save_setting("active_llms", json.dumps(self.active_llms))
            self.render_active_llms()

            self.add_browser_to_stack(new_llm["id"], new_llm["url"])
            self.play_plus_to_tab_animation(old_plus_rect, new_llm["id"])

        def cleanup_splash():
            for widget in splash_widgets:
                widget.hide()  # <--- HIDE INSTEAD OF DELETE

        splash_group.finished.connect(cleanup_splash)
        self.tab_animations.append(splash_group)
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
        self.tab_animations.append(group)
        group.start()

    def open_multitask_prompt(self):
        if self.multitask_prompt_overlay:
            self.multitask_prompt_overlay.deleteLater()
            self.multitask_prompt_overlay = None
            return

        button_rect = self.widget_rect_in_panel(self.multitask_button)
        start_rect = QRect(
            button_rect.center().x() - 20,
            button_rect.center().y() - 16,
            40,
            32
        )

        target_w = min(650, max(500, self.width() - 100))
        target_h = 155

        target_rect = QRect(
            (self.width() - target_w) // 2,
            72,
            target_w,
            target_h
        )

        overlay = QFrame(self)
        overlay.setObjectName("multitaskPromptOverlay")
        overlay.setGeometry(start_rect)
        overlay.setStyleSheet("""
            QFrame#multitaskPromptOverlay {
                background-color: rgba(24, 24, 28, 245);
                border: 2px solid rgba(129, 140, 248, 180);
                border-radius: 18px;
            }
            QLabel {
                background: transparent;
                color: #ffffff;
                font-size: 15px;
                font-weight: 600;
            }
            QLineEdit {
                background-color: #151515;
                border: 1px solid #333333;
                border-radius: 10px;
                color: white;
                padding: 9px 12px;
                font-size: 14px;
                font-family: "Segoe UI";
            }
        """)

        overlay_layout = QVBoxLayout(overlay)
        overlay_layout.setContentsMargins(18, 14, 18, 14)
        overlay_layout.setSpacing(10)

        title = QLabel("Ask every AI")

        warning = QLabel(
            "PS: Make sure you're logged in to each AI. "
            "Providers that aren't signed in may not receive your prompt."
        )

        warning.setWordWrap(True)

        warning.setStyleSheet("""
            QLabel {
                color: #b4b4b4;
                font-size: 11px;
                background: transparent;
            }
        """)
        input_box = QLineEdit()
        input_box.setPlaceholderText("Type your question and press Enter...")

        overlay_layout.addWidget(title)
        overlay_layout.addWidget(warning)
        overlay_layout.addWidget(input_box)

        overlay.show()
        overlay.raise_()
        self.multitask_prompt_overlay = overlay

        grow = QPropertyAnimation(overlay, b"geometry")
        grow.setDuration(260)
        grow.setStartValue(start_rect)
        grow.setEndValue(target_rect)
        grow.setEasingCurve(QEasingCurve.OutCubic)

        fade = QGraphicsOpacityEffect(overlay)
        overlay.setGraphicsEffect(fade)
        fade_anim = QPropertyAnimation(fade, b"opacity")
        fade_anim.setDuration(220)
        fade_anim.setStartValue(0.0)
        fade_anim.setEndValue(1.0)
        fade_anim.setEasingCurve(QEasingCurve.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(grow)
        group.addAnimation(fade_anim)

        def focus_input():
            input_box.setFocus()

        group.finished.connect(focus_input)
        self.tab_animations.append(group)
        group.start()

        input_box.returnPressed.connect(lambda: self.submit_multitask_prompt(input_box.text().strip(), overlay))

    def submit_multitask_prompt(self, prompt, overlay):
        if not prompt:
            return

        start_rect = overlay.geometry()
        end_rect = QRect(start_rect.center().x(), start_rect.center().y(), 0, 0)

        shrink = QPropertyAnimation(overlay, b"geometry")
        shrink.setDuration(180)
        shrink.setStartValue(start_rect)
        shrink.setEndValue(end_rect)
        shrink.setEasingCurve(QEasingCurve.InCubic)

        effect = overlay.graphicsEffect()
        fade = QPropertyAnimation(effect, b"opacity")
        fade.setDuration(160)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)

        group = QParallelAnimationGroup(self)
        group.addAnimation(shrink)
        group.addAnimation(fade)

        def finish():
            overlay.deleteLater()
            self.multitask_prompt_overlay = None
            self.open_multitask_tab(prompt)

        group.finished.connect(finish)
        self.tab_animations.append(group)
        group.start()

    def open_multitask_tab(self, prompt):
        if self.multitask_active and self.multitask_view:
            self.show_multitask_tab()
            self.send_prompt_to_existing_multitask(prompt)
            return

        self.multitask_active = True
        self.build_multitask_view(prompt)
        self.render_active_llms()
        self.show_multitask_tab()

    def send_prompt_to_existing_multitask(self, prompt):
        if not self.multitask_browsers:
            return

        for llm in self.active_llms:
            browser = self.multitask_browsers.get(llm["id"])
            if browser:
                self.try_send_prompt_to_browser(browser, prompt)

    def show_multitask_tab(self):
        if self.multitask_view:
            self.content_stack.setCurrentWidget(self.multitask_view)

    def show_multitask_context_menu(self, button):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #1f1f1f; border: 1px solid #333333; border-radius: 8px; padding: 4px; color: #ececec; }
            QMenu::item { padding: 6px 16px; border-radius: 4px; }
            QMenu::item:selected { background-color: #333333; }
        """)
        delete_action = menu.addAction("Delete Multitask")
        chosen = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if chosen == delete_action:
            self.delete_multitask_tab()

    def delete_multitask_tab(self):
        self.multitask_active = False

        if self.multitask_view:
            if self.content_stack.currentWidget() is self.multitask_view:
                self.content_stack.setCurrentWidget(self.browser_stack)
            self.content_stack.removeWidget(self.multitask_view)
            self.multitask_view.deleteLater()
            self.multitask_view = None

        for browser in self.multitask_browsers.values():
            browser.deleteLater()
        self.multitask_browsers.clear()
        self.multitask_senders.clear()
        self.multitask_tab_button = None
        self.render_active_llms()

    def build_multitask_view(self, prompt):
        if self.multitask_view:
            self.content_stack.removeWidget(self.multitask_view)
            self.multitask_view.deleteLater()
            self.multitask_view = None
            self.multitask_browsers.clear()

        wrapper = QWidget()
        wrapper.setStyleSheet("""
            QWidget { background-color: transparent; }
            QLabel#multitaskTitle { color: #ffffff; font-size: 18px; font-weight: 700; }
            QLabel#multitaskQuestion { color: #b4b4b4; font-size: 13px; }
            QLabel#multitaskProvider { color: #ececec; font-size: 13px; font-weight: 600; padding: 3px 0px; }
            QFrame#multitaskCard { background-color: rgba(20, 20, 20, 150); border: 1px solid rgba(255, 255, 255, 20); border-radius: 14px; }
        """)

        outer = QVBoxLayout(wrapper)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        title = QLabel("Multitask")
        title.setObjectName("multitaskTitle")
        question = QLabel(prompt)
        question.setObjectName("multitaskQuestion")
        question.setWordWrap(True)
        outer.addWidget(title)
        outer.addWidget(question)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        row_holder = QWidget()
        row = QHBoxLayout(row_holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        for llm in self.active_llms:
            card = QFrame()
            card.setObjectName("multitaskCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 10, 10, 10)
            card_layout.setSpacing(8)

            label = QLabel(llm["name"])
            label.setObjectName("multitaskProvider")
            card_layout.addWidget(label)

            status = QLabel("Loading...")
            status.setStyleSheet("QLabel { color: #a5b4fc; font-size: 12px; background: transparent; }")
            card_layout.addWidget(status)

            browser = QWebEngineView()
            browser.setMinimumWidth(360)
            browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

            settings = browser.settings()
            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanPaste, True)

            page = QWebEnginePage(self.profile, browser)

            def grant_feature_permission(origin, feature, p=page):
                p.setFeaturePermission(origin, feature, QWebEnginePage.PermissionPolicy.PermissionGrantedByUser)

            page.featurePermissionRequested.connect(grant_feature_permission)
            browser.setPage(page)

            browser.setProperty("multitask_sent", False)
            browser.setProperty("multitask_started", False)
            browser.setProperty("multitask_provider", llm["name"])
            browser.setProperty("multitask_id", llm["id"])

            self.multitask_browsers[llm["id"]] = browser
            card_layout.addWidget(browser, 1)
            row.addWidget(card)

            def handle_loaded(ok, b=browser, q=prompt, st=status, provider=llm["name"]):
                if not ok:
                    st.setText("Page failed to load")
                    st.setStyleSheet("QLabel { color: #fca5a5; font-size: 12px; background: transparent; }")
                    return
                if b.property("multitask_started"):
                    return
                b.setProperty("multitask_started", True)
                sender = MultitaskSender(b, provider_name=provider, status_label=st, parent=self)
                self.multitask_senders[b.property("multitask_id")] = sender
                QTimer.singleShot(3500, lambda s=sender, qq=q: s.send(qq))

            browser.loadFinished.connect(handle_loaded)
            browser.setUrl(QUrl(llm["url"]))

        row.addStretch()
        scroll.setWidget(row_holder)
        outer.addWidget(scroll, 1)

        self.multitask_view = wrapper
        self.content_stack.addWidget(wrapper)

    def send_prompt_to_existing_multitask(self, prompt):
        if not self.multitask_browsers:
            return
        for llm in self.active_llms:
            browser = self.multitask_browsers.get(llm["id"])
            if browser:
                browser.setProperty("multitask_sent", False)
                sender = self.multitask_senders.get(llm["id"])
                if sender is None:
                    sender = MultitaskSender(browser, provider_name=llm["name"], parent=self)
                    self.multitask_senders[llm["id"]] = sender
                sender.send(prompt)

    def create_layout(self):
        top_bar = QHBoxLayout()
        self.title_bar = QFrame()
        self.title_bar.setFixedHeight(45)

        top_bar.setContentsMargins(18, 4, 18, 4)
        top_bar.addWidget(self.scroll_area, alignment=Qt.AlignVCenter)
        top_bar.addStretch()
        top_bar.addWidget(self.multitask_button, alignment=Qt.AlignVCenter)
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
            self.browser_stack.setCurrentWidget(self.browsers[llm_id])

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

    def clear_browsing_data(self):
        self.profile.cookieStore().deleteAllCookies()
        self.profile.clearHttpCache()

        js_clear = "window.localStorage.clear(); window.sessionStorage.clear();"

        for browser in self.browsers.values():
            browser.page().runJavaScript(js_clear, lambda res, b=browser: b.reload())

        if hasattr(self, "multitask_browsers"):
            for browser in self.multitask_browsers.values():
                browser.page().runJavaScript(js_clear, lambda res, b=browser: b.reload())

        self.show_browser()
        self.setting_panel.appearance_btn.setChecked(True)
        self.setting_panel.content_stack.setCurrentIndex(0)

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
        elif getattr(self, "multitask_view", None) is not None and current is self.multitask_view:
            self.resize_hidden_widget = self.multitask_view

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