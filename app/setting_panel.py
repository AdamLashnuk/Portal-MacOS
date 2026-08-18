import os
import json
import copy
import sys as sys_module
from PySide6.QtWidgets import (QApplication, QComboBox, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QStackedWidget,
                               QButtonGroup, QKeySequenceEdit, QLineEdit, QSlider, QLayout, QSizePolicy)
from PySide6.QtCore import Qt, QEvent, Signal, QSettings, QRect, QSize, QPoint
from PySide6.QtGui import QPixmap, QColor, QImage, QKeySequence, QPainter, QBrush

from app.hotkeys.key_sequences import canonical_to_qt, qt_to_canonical
from app.macos.focus_promoter import FocusPromoter
from app.utils import get_asset_path

_WINDOWS_DEFAULT_KEYBINDS = {
    "summon": {"label": "Summon Panel", "key": "Ctrl+Space", "is_global": True},
    "hide": {"label": "Hide Panel", "key": "Esc", "is_global": False},
    "next_llm": {"label": "Next LLM", "key": "Ctrl+Tab", "is_global": False},
    "refresh": {"label": "Hard Refresh", "key": "F5", "is_global": False},
    "quick_refresh": {"label": "Quick Refresh", "key": "Ctrl+R", "is_global": False},
    "pin_toggle": {"label": "Toggle Pin", "key": "Alt+P", "is_global": True}
}

_MAC_DEFAULT_KEYBINDS = {
    "summon": {"label": "Summon Panel", "key": "Meta+Shift+Space", "is_global": True},
    "hide": {"label": "Hide Panel", "key": "Esc", "is_global": False},
    "next_llm": {"label": "Next LLM", "key": "Meta+Shift+]", "is_global": False},
    "refresh": {"label": "Hard Refresh", "key": "Meta+Shift+R", "is_global": False},
    "quick_refresh": {"label": "Quick Refresh", "key": "Meta+R", "is_global": False},
    "pin_toggle": {"label": "Toggle Pin", "key": "Meta+Alt+P", "is_global": True},
}

DEFAULT_KEYBINDS = (
    _MAC_DEFAULT_KEYBINDS
    if sys_module.platform == "darwin"
    else _WINDOWS_DEFAULT_KEYBINDS
)


class CustomSlider(QWidget):
    valueChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(24)
        self._value = 0
        self._min = 0
        self._max = 100
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        self._is_hovered = False

    def setMinimum(self, min_val):
        self._min = min_val

    def setMaximum(self, max_val):
        self._max = max_val

    def value(self):
        return self._value

    def setValue(self, val):
        self._value = max(self._min, min(self._max, val))
        self.update()

    def enterEvent(self, event):
        self._is_hovered = True
        self.update()

    def leaveEvent(self, event):
        self._is_hovered = False
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        track_h = 6
        track_y = (h - track_h) / 2
        handle_r = 8

        if self.isEnabled():
            track_color = QColor("#151515")
            fill_color = QColor("#6366f1")
            handle_color = QColor("#ffffff") if self._is_hovered else QColor("#ececec")
        else:
            track_color = QColor("#1f1f1f")
            fill_color = QColor("#3a3a3a")
            handle_color = QColor("#555555")

        painter.setPen(Qt.NoPen)

        painter.setBrush(QBrush(track_color))
        painter.drawRoundedRect(0, track_y, w, track_h, 3, 3)

        ratio = (self._value - self._min) / max(1, (self._max - self._min))
        fill_w = ratio * w
        painter.setBrush(QBrush(fill_color))
        painter.drawRoundedRect(0, track_y, fill_w, track_h, 3, 3)

        painter.setBrush(QBrush(handle_color))
        handle_x = max(0, min(w - handle_r * 2, fill_w - handle_r))
        painter.drawEllipse(handle_x, int(h / 2 - handle_r), handle_r * 2, handle_r * 2)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled():
            self._update_value_from_pos(event.position().x())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self.isEnabled():
            self._update_value_from_pos(event.position().x())

    def _update_value_from_pos(self, x):
        ratio = x / self.width()
        val = int(self._min + ratio * (self._max - self._min))
        val = max(self._min, min(self._max, val))
        if val != self._value:
            self._value = val
            self.valueChanged.emit(self._value)
            self.update()


class ShortcutKeySequenceEdit(QKeySequenceEdit):
    capture_started = Signal()
    capture_cancelled = Signal()

    def __init__(self, sequence, parent=None):
        qt_sequence = QKeySequence(canonical_to_qt(sequence))
        super().__init__(qt_sequence, parent)
        self._capturing = False
        self._previous_sequence = QKeySequence(qt_sequence)
        self.setProperty("shortcutHovered", False)
        self.setMouseTracking(True)

        line_edit = self.findChild(QLineEdit)
        if line_edit:
            line_edit.setMouseTracking(True)
            line_edit.installEventFilter(self)

    def _set_hovered(self, hovered):
        if self.property("shortcutHovered") == hovered:
            return

        self.setProperty("shortcutHovered", hovered)
        self.style().unpolish(self)
        self.style().polish(self)

    def _is_editor_widget(self, widget):
        current = widget if isinstance(widget, QWidget) else None
        while current:
            if current is self:
                return True
            current = current.parentWidget()
        return False

    def _set_capture_placeholder(self, text):
        line_edit = self.findChild(QLineEdit)
        if line_edit:
            line_edit.setPlaceholderText(text)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if self._capturing:
            return

        FocusPromoter.promote(self.window())
        self._capturing = True
        self._previous_sequence = self.keySequence()

        # Clear the visible value without saving an empty shortcut. The
        # placeholder then tells the user exactly what to do next.
        self.blockSignals(True)
        self.clear()
        self.blockSignals(False)

        self._set_capture_placeholder("Press a shortcut...")
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)
        self.capture_started.emit()

    def focusOutEvent(self, event):
        if self._capturing and self.keySequence().isEmpty():
            # A click away without entering keys should leave the saved
            # shortcut untouched and restore what the user had before.
            self.blockSignals(True)
            self.setKeySequence(self._previous_sequence)
            self.blockSignals(False)
            self.capture_cancelled.emit()

        self._capturing = False
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)
        self._set_capture_placeholder("")
        FocusPromoter.demote()
        super().focusOutEvent(event)

    def eventFilter(self, watched, event):
        if isinstance(watched, QLineEdit) and self._is_editor_widget(watched):
            if event.type() == QEvent.Type.Enter:
                self._set_hovered(True)
            elif event.type() == QEvent.Type.Leave:
                self._set_hovered(False)

        if (
            self._capturing
            and event.type() == QEvent.Type.MouseButtonPress
            and not self._is_editor_widget(watched)
        ):
            self.clearFocus()

        return super().eventFilter(watched, event)


class SettingPanel(QWidget):
    color_changed = Signal(str)
    opacity_changed = Signal(int)
    clear_current_data_requested = Signal()
    clear_all_data_requested = Signal()
    keybinds_updated = Signal(dict)  # Tells ChatPanel to reload its shortcuts

    def __init__(self):
        super().__init__()

        self.settings = QSettings("MyLLMWidget", "Portal")
        saved_binds = self.settings.value("shortcuts")
        if saved_binds:
            self.current_keybinds = json.loads(saved_binds)
            for k, v in DEFAULT_KEYBINDS.items():
                if k not in self.current_keybinds:
                    self.current_keybinds[k] = v
        else:
            self.current_keybinds = copy.deepcopy(DEFAULT_KEYBINDS)

        self.keybind_widgets = {}  # Stores references to the UI inputs

        self.setStyleSheet("""
            /* Global widget rules */
            QWidget { background-color: #1a1a1a; color: #ececec; font-family: "Segoe UI"; }
            QWidget#settingPanelMain { border-radius: 12px; }
            QWidget#transparentWidget { background-color: transparent; }
            QLabel { background-color: transparent; }

            /* --- Sidebar Styling --- */
            QFrame#sidebar { background-color: #1f1f1f; border-right: 1px solid #333333; }
            QLabel#sidebarTitle { font-size: 18px; font-weight: bold; color: #ffffff; }
            QPushButton.sidebarButton { background-color: transparent; border: none; border-radius: 6px; color: #b4b4b4; font-size: 14px; font-weight: 500; text-align: left; padding: 10px 15px; }
            QPushButton.sidebarButton:hover { background-color: #2a2a2a; color: #ececec; }
            QPushButton.sidebarButton:checked { background-color: #333333; color: #ffffff; }

            /* --- Content Area Styling --- */
            QLabel.pageTitle { font-size: 24px; font-weight: 600; color: #ffffff; margin-bottom: 10px; }
            QFrame.settingCard { background-color: #242424; border: 1px solid #333333; border-radius: 10px; }
            QLabel.cardTitle { font-size: 16px; font-weight: 500; color: #ffffff; }
            QLabel.cardText { font-size: 14px; color: #b4b4b4; }

            /* --- Danger Button Styling --- */
            QPushButton.dangerButton { background-color: rgba(220, 38, 38, 0.15); border: 1px solid rgba(220, 38, 38, 0.5); color: #f87171; border-radius: 8px; padding: 10px 20px; font-weight: 600; font-size: 14px; }
            QPushButton.dangerButton:hover { background-color: rgba(220, 38, 38, 0.25); color: #fca5a5; }

            /* --- Keybinds UI Styling --- */
            QKeySequenceEdit { background-color: #242424; border: 1px solid transparent; border-radius: 14px; color: #ececec; padding: 6px 12px; }
            QKeySequenceEdit QLineEdit { background-color: #242424; border: 1px solid #242424; border-radius: 12px; color: #ececec; padding: 0; }
            QKeySequenceEdit QLineEdit::placeholder { color: #a3a3a3; }
            QKeySequenceEdit:hover { background-color: #2a2a2a; border-color: #4a4a4a; }
            QKeySequenceEdit:hover QLineEdit { background-color: #2a2a2a; border-color: #2a2a2a; border-radius: 12px; }
            QKeySequenceEdit QLineEdit:hover { border-color: #4a4a4a; }
            QKeySequenceEdit[shortcutHovered="true"] QLineEdit { background-color: #2a2a2a; border-color: #4a4a4a; }
            QKeySequenceEdit:focus { background-color: #2a2a2a; border-color: #737373; }
            QKeySequenceEdit:focus QLineEdit { background-color: #2a2a2a; border-color: #2a2a2a; border-radius: 12px; }
            QKeySequenceEdit QLineEdit:focus { border-color: #737373; }
            QPushButton.scopeToggle { background-color: #242424; color: #b4b4b4; border: 1px solid #333333; border-radius: 6px; padding: 6px 0px; font-size: 13px; min-height: 18px; }
            QPushButton.scopeToggle:hover { background-color: #2a2a2a; color: #ececec; }
            QPushButton.scopeToggle:checked { color: #818cf8; border: 1px solid #6366f1; background-color: rgba(99, 102, 241, 0.1); }
            QFrame.rowDivider { background-color: #333333; }

        """)
        self.create_layout()

    def save_all_keybinds(self):
        self.settings.setValue("shortcuts", json.dumps(self.current_keybinds))
        self.settings.sync()
        self.keybinds_updated.emit(self.current_keybinds)

    def _migrate_legacy_color(self, legacy_value):
        """
        Pulls a base RGB + opacity percentage out of an old-style saved
        value, back when each preset baked a fixed alpha directly into its
        rgba(...) string. Used once, the first time this runs after the
        opacity slider is added, so existing users land on roughly the
        same look they had before rather than snapping to a default.
        """
        if legacy_value == "transparent":
            return "transparent", 50

        try:
            inner = legacy_value[legacy_value.index("(") + 1: legacy_value.index(")")]
            r, g, b, a = [int(p.strip()) for p in inner.split(",")]
            base_color = f"rgb({r}, {g}, {b})"
            opacity_percent = round((a / 255) * 100)
            return base_color, opacity_percent
        except (ValueError, IndexError):
            return "rgb(15, 15, 15)", 86  # ~220/255

    def _current_rgba_string(self):
        if self.selected_base_color == "transparent":
            return "transparent"
        inner = self.selected_base_color[self.selected_base_color.index("(") + 1: self.selected_base_color.index(")")]
        alpha = round((self.current_opacity / 100) * 255)
        return f"rgba({inner}, {alpha})"

    def _on_color_selected(self, button):
        self.selected_base_color = self.preset_rgb[button]
        is_transparent = self.selected_base_color == "transparent"

        self.opacity_slider.setEnabled(not is_transparent)

        self._emit_and_save_appearance()

    def _on_opacity_changed(self, value):
        self.current_opacity = value
        self.opacity_value_label.setText(f"{value}%")
        self._emit_and_save_appearance()

    def _emit_and_save_appearance(self):
        rgba_string = self._current_rgba_string()
        self.color_changed.emit(rgba_string)
        self.opacity_changed.emit(self.current_opacity)

        app_settings = QSettings("MyLLMWidget", "ChatPanel")
        app_settings.setValue("resize_color_base", self.selected_base_color)
        app_settings.setValue("resize_opacity", self.current_opacity)
        app_settings.sync()

    def update_keybind_seq(self, action_id, seq_str):
        self.current_keybinds[action_id]["key"] = seq_str
        self.save_all_keybinds()

    def update_keybind_scope(self, action_id, is_global):
        self.current_keybinds[action_id]["is_global"] = is_global
        self.save_all_keybinds()

    @staticmethod
    def size_keybind_editor(key_edit):
        shortcut_text = (
            key_edit.keySequence().toString(QKeySequence.NativeText)
            or "Set shortcut"
        )
        text_width = key_edit.fontMetrics().horizontalAdvance(shortcut_text)
        key_edit.setFixedWidth(max(96, min(text_width + 32, 160)))

    def reset_keybinds(self):
        self.current_keybinds = copy.deepcopy(DEFAULT_KEYBINDS)
        for action_id, data in self.current_keybinds.items():
            if action_id in self.keybind_widgets:
                w = self.keybind_widgets[action_id]
                w["edit"].blockSignals(True)
                w["toggle"].blockSignals(True)

                w["edit"].setKeySequence(
                    QKeySequence(canonical_to_qt(data["key"]))
                )
                self.size_keybind_editor(w["edit"])
                w["toggle"].setChecked(data["is_global"])
                w["toggle"].setText("Global" if data["is_global"] else "Local")
                w["label"].setText(data["label"])  # <-- THIS NEW LINE UPDATES THE TEXT

                w["edit"].blockSignals(False)
                w["toggle"].blockSignals(False)
        self.save_all_keybinds()

    def _on_startup_position_selected(self, mode):
        self.current_startup_position = mode

        self.app_settings.setValue(
            "widget_startup_position",
            mode
        )

        self.app_settings.sync()

    def create_layout(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(180)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(15, 25, 15, 25)
        sidebar_layout.setSpacing(8)

        header_layout = QHBoxLayout()
        header_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header_layout.setSpacing(10)
        header_layout.setContentsMargins(10, 0, 0, 0)

        icon_label = QLabel()
        icon_path = get_asset_path(os.path.join("assets", "portalbig.png"))

        if os.path.exists(icon_path):
            logo_pixmap = QPixmap(icon_path)

            if not logo_pixmap.isNull():
                icon_label.setPixmap(logo_pixmap.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        title = QLabel("Settings")
        title.setObjectName("sidebarTitle")
        header_layout.addWidget(icon_label)
        header_layout.addWidget(title)
        sidebar_layout.addLayout(header_layout)
        sidebar_layout.addSpacing(15)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        self.appearance_btn = QPushButton("Appearance")
        self.appearance_btn.setProperty("class", "sidebarButton")
        self.appearance_btn.setCheckable(True)
        self.appearance_btn.setChecked(True)
        self.nav_group.addButton(self.appearance_btn, 0)
        sidebar_layout.addWidget(self.appearance_btn)

        self.keybinds_btn = QPushButton("Keybinds")
        self.keybinds_btn.setProperty("class", "sidebarButton")
        self.keybinds_btn.setCheckable(True)
        self.nav_group.addButton(self.keybinds_btn, 1)
        sidebar_layout.addWidget(self.keybinds_btn)

        self.privacy_btn = QPushButton("Privacy && Data")
        self.privacy_btn.setProperty("class", "sidebarButton")
        self.privacy_btn.setCheckable(True)
        self.nav_group.addButton(self.privacy_btn, 2)
        sidebar_layout.addWidget(self.privacy_btn)
        sidebar_layout.addStretch()

        self.content_stack = QStackedWidget()

        self.appearance_page = QWidget()
        app_layout = QVBoxLayout(self.appearance_page)
        app_layout.setContentsMargins(40, 40, 40, 40)
        app_layout.setAlignment(Qt.AlignTop)

        app_title = QLabel("Appearance")
        app_title.setProperty("class", "pageTitle")
        app_layout.addWidget(app_title)

        appearance_card = QFrame()
        appearance_card.setProperty("class", "settingCard")
        appearance_card_layout = QVBoxLayout(appearance_card)
        appearance_card_layout.setContentsMargins(20, 20, 20, 20)
        appearance_card_layout.setSpacing(14)

        def add_appearance_section(section):
            if appearance_card_layout.count() > 0:
                divider = QFrame()
                divider.setFixedHeight(1)
                divider.setProperty("class", "rowDivider")
                appearance_card_layout.addWidget(divider)

            appearance_card_layout.addWidget(section)

        color_card = QFrame()
        color_card.setObjectName("transparentWidget")
        color_card_layout = QVBoxLayout(color_card)
        color_card_layout.setContentsMargins(0, 0, 0, 0)
        color_card_layout.setSpacing(15)

        resize_title = QLabel("Window Color")
        resize_title.setProperty("class", "cardTitle")
        color_card_layout.addWidget(resize_title)

        color_layout = QHBoxLayout()
        color_layout.setAlignment(Qt.AlignLeft)
        color_layout.setSpacing(15)

        self.color_group = QButtonGroup(self)
        self.color_group.setExclusive(True)

        self.btn_transparent = QPushButton("✕")
        self.btn_transparent.setFixedSize(40, 40)
        self.btn_transparent.setCheckable(True)
        self.btn_transparent.setStyleSheet(
            "QPushButton { background-color: transparent; border: 2px dashed #444444; border-radius: 8px; color: #555555; font-weight: bold; } QPushButton:hover { border: 2px dashed #b4b4b4; color: #b4b4b4; } QPushButton:checked { border: 2px solid #6366f1; color: #6366f1; } ")

        self.btn_grey = QPushButton()
        self.btn_grey.setFixedSize(40, 40)
        self.btn_grey.setCheckable(True)
        self.btn_grey.setStyleSheet(
            "QPushButton { background-color: rgb(15, 15, 15); border: 2px solid #333333; border-radius: 8px; } QPushButton:hover { border: 2px solid #b4b4b4; } QPushButton:checked { border: 2px solid #6366f1; }")

        self.btn_purple = QPushButton()
        self.btn_purple.setFixedSize(40, 40)
        self.btn_purple.setCheckable(True)
        self.btn_purple.setStyleSheet(
            "QPushButton { background-color: rgb(45, 25, 65); border: 2px solid #333333; border-radius: 8px; } QPushButton:hover { border: 2px solid #b4b4b4; } QPushButton:checked { border: 2px solid #6366f1; }")

        self.btn_blue = QPushButton()
        self.btn_blue.setFixedSize(40, 40)
        self.btn_blue.setCheckable(True)
        self.btn_blue.setStyleSheet(
            "QPushButton { background-color: rgb(15, 30, 50); border: 2px solid #333333; border-radius: 8px; } QPushButton:hover { border: 2px solid #b4b4b4; } QPushButton:checked { border: 2px solid #6366f1; }")

        self.btn_red = QPushButton()
        self.btn_red.setFixedSize(40, 40)
        self.btn_red.setCheckable(True)
        self.btn_red.setStyleSheet(
            "QPushButton { background-color: rgb(50, 15, 20); border: 2px solid #333333; border-radius: 8px; } QPushButton:hover { border: 2px solid #b4b4b4; } QPushButton:checked { border: 2px solid #6366f1; }")

        self.btn_green = QPushButton()
        self.btn_green.setFixedSize(40, 40)
        self.btn_green.setCheckable(True)
        self.btn_green.setStyleSheet(
            "QPushButton { background-color: rgb(15, 40, 25); border: 2px solid #333333; border-radius: 8px; } QPushButton:hover { border: 2px solid #b4b4b4; } QPushButton:checked { border: 2px solid #6366f1; }")

        self.btn_amber = QPushButton()
        self.btn_amber.setFixedSize(40, 40)
        self.btn_amber.setCheckable(True)
        self.btn_amber.setStyleSheet(
            "QPushButton { background-color: rgb(55, 35, 15); border: 2px solid #333333; border-radius: 8px; } QPushButton:hover { border: 2px solid #b4b4b4; } QPushButton:checked { border: 2px solid #6366f1; }")

        self.color_group.addButton(self.btn_transparent)
        self.color_group.addButton(self.btn_grey)
        self.color_group.addButton(self.btn_purple)
        self.color_group.addButton(self.btn_blue)
        self.color_group.addButton(self.btn_red)     
        self.color_group.addButton(self.btn_green)   
        self.color_group.addButton(self.btn_amber)

        color_layout.addWidget(self.btn_transparent)
        color_layout.addWidget(self.btn_grey)
        color_layout.addWidget(self.btn_purple)
        color_layout.addWidget(self.btn_blue)
        color_layout.addWidget(self.btn_red)       
        color_layout.addWidget(self.btn_green)     
        color_layout.addWidget(self.btn_amber)

        self.preset_rgb = {
            self.btn_transparent: "transparent",
            self.btn_grey: "rgb(15, 15, 15)",
            self.btn_purple: "rgb(45, 25, 65)",
            self.btn_blue: "rgb(15, 30, 50)",
            self.btn_red: "rgb(50, 15, 20)",      
            self.btn_green: "rgb(15, 40, 25)",    
            self.btn_amber: "rgb(55, 35, 15)",    
        }

        self.app_settings = QSettings("MyLLMWidget", "ChatPanel")

        saved_base_color = self.app_settings.value("resize_color_base", None)
        saved_opacity = self.app_settings.value("resize_opacity", None)

        if saved_base_color is None or saved_opacity is None:
            legacy_color = self.app_settings.value("resize_color", "rgba(15, 15, 15, 220)")
            saved_base_color, saved_opacity = self._migrate_legacy_color(legacy_color)

        self.selected_base_color = saved_base_color
        self.current_opacity = int(saved_opacity)

        if saved_base_color == "transparent":
            self.btn_transparent.setChecked(True)
        elif saved_base_color == "rgb(45, 25, 65)":
            self.btn_purple.setChecked(True)
        elif saved_base_color == "rgb(15, 30, 50)":
            self.btn_blue.setChecked(True)
        elif saved_base_color == "rgb(50, 15, 20)":   
            self.btn_red.setChecked(True)             
        elif saved_base_color == "rgb(15, 40, 25)":  
            self.btn_green.setChecked(True)          
        elif saved_base_color == "rgb(55, 35, 15)":  
            self.btn_amber.setChecked(True)           
        else:
            self.btn_grey.setChecked(True)

        color_card_layout.addLayout(color_layout)
        add_appearance_section(color_card)

        opacity_card = QFrame()
        opacity_card.setObjectName("transparentWidget")
        opacity_card_layout = QVBoxLayout(opacity_card)
        opacity_card_layout.setContentsMargins(0, 0, 0, 0)
        opacity_card_layout.setSpacing(15)

        opacity_caption = QLabel("Background Opacity")
        opacity_caption.setProperty("class", "cardTitle")
        opacity_card_layout.addWidget(opacity_caption)

        opacity_row = QHBoxLayout()
        opacity_row.setSpacing(15)

        self.opacity_slider = CustomSlider()
        self.opacity_slider.setMinimum(0)
        self.opacity_slider.setMaximum(100)
        self.opacity_slider.setValue(self.current_opacity)
        self.opacity_slider.setEnabled(saved_base_color != "transparent")

        self.opacity_value_label = QLabel(f"{self.current_opacity}%")
        self.opacity_value_label.setProperty("class", "cardText")
        self.opacity_value_label.setFixedWidth(40)

        opacity_row.addWidget(self.opacity_slider)
        opacity_row.addWidget(self.opacity_value_label)
        opacity_card_layout.addLayout(opacity_row)

        add_appearance_section(opacity_card)

        # --- WIDGET STARTUP POSITION CARD ---
        startup_pos_card = QFrame()
        startup_pos_card.setObjectName("transparentWidget")

        startup_pos_layout = QVBoxLayout(startup_pos_card)
        startup_pos_layout.setContentsMargins(0, 0, 0, 0)
        startup_pos_layout.setSpacing(12)

        startup_pos_title = QLabel("Widget Startup Position")
        startup_pos_title.setProperty("class", "cardTitle")
        startup_pos_layout.addWidget(startup_pos_title)

        startup_pos_desc = QLabel(
            "Determines the exact screen location where Portal spawns upon opening."
        )
        startup_pos_desc.setProperty("class", "cardText")
        startup_pos_desc.setWordWrap(True)
        startup_pos_layout.addWidget(startup_pos_desc)


        self.startup_position_group = QButtonGroup(self)
        self.startup_position_group.setExclusive(True)

        startup_buttons_layout = QHBoxLayout()
        startup_buttons_layout.setSpacing(8)

        self.startup_pos_buttons = {}

        startup_position_modes = {
            "Center": "center",
            "Top Right": "top_right",
            "Top Left": "top_left",
            "Bottom Right": "bottom_right",
            "Bottom Left": "bottom_left",
        }

        for text, mode in startup_position_modes.items():
            btn = QPushButton(text)

            btn.setProperty("class", "scopeToggle")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)

            self.startup_position_group.addButton(btn)

            startup_buttons_layout.addWidget(btn)

            self.startup_pos_buttons[btn] = mode


        saved_startup_position = self.app_settings.value(
            "widget_startup_position",
            "center"
        )

        checked_button = None

        for btn, mode in self.startup_pos_buttons.items():
            if mode == saved_startup_position:
                checked_button = btn
                break

        if checked_button:
            checked_button.setChecked(True)


        for btn, mode in self.startup_pos_buttons.items():
            btn.clicked.connect(
                lambda checked=False, m=mode:
                self._on_startup_position_selected(m)
            )


        startup_pos_layout.addLayout(startup_buttons_layout)

        add_appearance_section(startup_pos_card)

        if sys_module.platform in ("win32", "darwin"):
            from app.utils import set_startup, check_startup_enabled

            startup_card = QFrame()
            startup_card.setObjectName("transparentWidget")
            startup_card_layout = QVBoxLayout(startup_card)
            startup_card_layout.setContentsMargins(0, 0, 0, 0)
            startup_card_layout.setSpacing(12)

            startup_title = QLabel("System")
            startup_title.setProperty("class", "cardTitle")
            startup_card_layout.addWidget(startup_title)

            self.btn_startup = QPushButton("Launch Portal on Startup")
            self.btn_startup.setProperty("class", "scopeToggle")
            self.btn_startup.setCheckable(True)
            self.btn_startup.setCursor(Qt.PointingHandCursor)
            
            # Read the platform's real launch-at-login configuration.
            self.btn_startup.setChecked(check_startup_enabled())

            # Update the Windows registry or macOS LaunchAgent.
            self.btn_startup.toggled.connect(lambda checked: set_startup(checked))

            startup_card_layout.addWidget(self.btn_startup)
            add_appearance_section(startup_card)

        app_layout.addWidget(appearance_card)

        self.btn_transparent.clicked.connect(lambda: self._on_color_selected(self.btn_transparent))
        self.btn_grey.clicked.connect(lambda: self._on_color_selected(self.btn_grey))
        self.btn_purple.clicked.connect(lambda: self._on_color_selected(self.btn_purple))
        self.btn_blue.clicked.connect(lambda: self._on_color_selected(self.btn_blue))
        self.btn_red.clicked.connect(lambda: self._on_color_selected(self.btn_red))       
        self.btn_green.clicked.connect(lambda: self._on_color_selected(self.btn_green))   
        self.btn_amber.clicked.connect(lambda: self._on_color_selected(self.btn_amber))   
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)

        self.privacy_page = QWidget()
        priv_layout = QVBoxLayout(self.privacy_page)
        priv_layout.setContentsMargins(40, 40, 40, 40)
        priv_layout.setAlignment(Qt.AlignTop)

        priv_title = QLabel("Privacy & Data")
        priv_title.setProperty("class", "pageTitle")
        priv_layout.addWidget(priv_title)

        priv_card = QFrame()
        priv_card.setProperty("class", "settingCard")
        priv_card_layout = QVBoxLayout(priv_card)
        priv_card_layout.setContentsMargins(20, 20, 20, 20)
        priv_card_layout.setSpacing(15)

        danger_title = QLabel("Clear Browsing Data")
        danger_title.setProperty("class", "cardTitle")
        priv_card_layout.addWidget(danger_title)

        danger_desc = QLabel(
            "Reset only the selected tab's isolated session, or clear every "
            "AI provider session in Portal. Affected tabs will be logged out "
            "and restarted with clean browsing data."
        )
        danger_desc.setProperty("class", "cardText")
        danger_desc.setWordWrap(True)
        priv_card_layout.addWidget(danger_desc)

        self.btn_clear_current_data = QPushButton("Clear Current Tab")
        self.btn_clear_current_data.setProperty("class", "dangerButton")
        self.btn_clear_current_data.setCursor(Qt.PointingHandCursor)
        self.btn_clear_current_data.clicked.connect(
            lambda: self.clear_current_data_requested.emit()
        )

        self.btn_clear_all_data = QPushButton("Clear All Providers")
        self.btn_clear_all_data.setProperty("class", "dangerButton")
        self.btn_clear_all_data.setCursor(Qt.PointingHandCursor)
        self.btn_clear_all_data.clicked.connect(
            lambda: self.clear_all_data_requested.emit()
        )

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.btn_clear_current_data)
        btn_layout.addWidget(self.btn_clear_all_data)
        btn_layout.addStretch()
        priv_card_layout.addLayout(btn_layout)
        priv_layout.addWidget(priv_card)

        self.keybinds_page = QWidget()
        kb_layout = QVBoxLayout(self.keybinds_page)
        kb_layout.setContentsMargins(40, 40, 40, 40)
        kb_layout.setAlignment(Qt.AlignTop)

        kb_header_layout = QHBoxLayout()
        kb_title = QLabel("Keybinds")
        kb_title.setProperty("class", "pageTitle")
        kb_header_layout.addWidget(kb_title)
        kb_header_layout.addStretch()

        self.btn_reset_keybinds = QPushButton("Reset Keybinds")
        self.btn_reset_keybinds.setCursor(Qt.PointingHandCursor)
        self.btn_reset_keybinds.setStyleSheet("""
            QPushButton { background-color: transparent; color: #b4b4b4; font-size: 13px; font-weight: 500; text-decoration: underline; border: none; margin-bottom: 10px; }
            QPushButton:hover { color: #ececec; }
        """)
        self.btn_reset_keybinds.clicked.connect(self.reset_keybinds)
        kb_header_layout.addWidget(self.btn_reset_keybinds)
        kb_layout.addLayout(kb_header_layout)

        kb_card = QFrame()
        kb_card.setProperty("class", "settingCard")
        kb_card_layout = QVBoxLayout(kb_card)
        kb_card_layout.setContentsMargins(20, 20, 20, 20)
        kb_card_layout.setSpacing(10)

        def add_keybind_row(action_id, data, is_last=False):
            row_widget = QWidget()
            row_widget.setObjectName("transparentWidget")
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)

            lbl = QLabel(data["label"])
            lbl.setProperty("class", "cardTitle")
            row_layout.addWidget(lbl)
            row_layout.addStretch()

            toggle = QPushButton("Global" if data["is_global"] else "Local")
            toggle.setProperty("class", "scopeToggle")
            toggle.setCheckable(True)
            toggle.setChecked(data["is_global"])
            toggle.setCursor(Qt.PointingHandCursor)
            toggle.setFixedWidth(65)

            key_edit = ShortcutKeySequenceEdit(data["key"])
            key_edit.setCursor(Qt.PointingHandCursor)
            key_edit.setToolTip("Click to change shortcut")
            self.size_keybind_editor(key_edit)

            toggle.toggled.connect(lambda checked, t=toggle, a=action_id: [t.setText("Global" if checked else "Local"),
                                                                           self.update_keybind_scope(a, checked)])

            def handle_key_sequence_changed(seq, action=action_id, editor=key_edit):
                self.update_keybind_seq(
                    action,
                    qt_to_canonical(seq.toString(QKeySequence.PortableText)),
                )
                self.size_keybind_editor(editor)

            key_edit.keySequenceChanged.connect(handle_key_sequence_changed)
            key_edit.capture_started.connect(lambda editor=key_edit: self.size_keybind_editor(editor))
            key_edit.capture_cancelled.connect(lambda editor=key_edit: self.size_keybind_editor(editor))

            row_layout.addWidget(toggle)
            row_layout.addSpacing(10)
            row_layout.addWidget(key_edit)
            kb_card_layout.addWidget(row_widget)

            self.keybind_widgets[action_id] = {"edit": key_edit, "toggle": toggle, "label": lbl}

            if not is_last:
                divider = QFrame()
                divider.setFixedHeight(1)
                divider.setProperty("class", "rowDivider")
                kb_card_layout.addWidget(divider)

        keys = list(self.current_keybinds.keys())
        for i, action_id in enumerate(keys):
            add_keybind_row(action_id, self.current_keybinds[action_id], is_last=(i == len(keys) - 1))

        kb_layout.addWidget(kb_card)

        self.content_stack.addWidget(self.appearance_page)
        self.content_stack.addWidget(self.keybinds_page)
        self.content_stack.addWidget(self.privacy_page)

        self.nav_group.idClicked.connect(self.content_stack.setCurrentIndex)

        main_layout.addWidget(self.sidebar)
        main_layout.addWidget(self.content_stack)
