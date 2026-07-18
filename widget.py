import os
from PySide6.QtWidgets import QWidget, QSystemTrayIcon, QMenu, QApplication
from PySide6.QtCore import Qt, QPoint, QRect, QSettings
from PySide6.QtGui import QGuiApplication, QPainter, QColor, QPixmap, QPen, QAction, QIcon, QCursor
from app.animation_window import AnimationWindow
from app.chat_panel import ChatPanel
from app.utils import get_asset_path


class FloatingWidget(QWidget):
    def __init__(self):
        super().__init__()

        self.is_hovered = False
        self.chat_panel = ChatPanel(self)
        self.drag_position = QPoint()
        self.was_dragging = False
        self.animation_window = AnimationWindow()
        self.animation_window.open_finished.connect(self.show_chat_panel_after_animation)
        self.animation_window.close_finished.connect(self.show_bubble_after_animation)

        self.setup_window()
        self.setup_tray_icon()
        self.set_initial_position()

    def set_initial_position(self):
        settings = QSettings("MyLLMWidget", "ChatPanel")

        saved_position = settings.value(
            "widget_startup_position",
            "center"
        )
        screen = QGuiApplication.primaryScreen().availableGeometry()
        app_width = self.width()
        app_height = self.height()
        margin = 20
        y_offset = margin + 30  # Pushes it away from the corners
        
        # FIX: Match the exact lowercase keys saved by setting_panel.py
        if saved_position == "top_right":
            x = screen.width() - app_width  
            y = y_offset                    
        elif saved_position == "top_left":
            x = 0                           
            y = y_offset                    
        elif saved_position == "bottom_right":
            x = screen.width() - app_width  
            y = screen.height() - app_height - y_offset 
        elif saved_position == "bottom_left":
            x = 0                           
            y = screen.height() - app_height - y_offset 
        elif saved_position == "center":
            x = (screen.width() - app_width) // 2
            y = (screen.height() - app_height) // 2
        else:
            # Fallback (Center)
            x = (screen.width() - app_width) // 2
            y = (screen.height() - app_height) // 2
            
        self.move(x, y)

    def setup_window(self):
        self.setFixedSize(90, 90)
        self.move(1200, 600)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_Hover)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.settings = QSettings("MyLLMWidget", "Portal")

        logo_path = get_asset_path(os.path.join("assets", "portalbig.png"))
        logo = QPixmap(logo_path)
        self.cached_logo = logo.scaled(85, 85, Qt.KeepAspectRatio, Qt.SmoothTransformation) if not logo.isNull() else None

    def target_screen_geometry(self):
        if self.chat_panel.isVisible():
            point = self.chat_panel.frameGeometry().center()
        else:
            point = self.frameGeometry().center()

        screen = QApplication.screenAt(point)
        if screen is None:
            screen = QApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QApplication.primaryScreen()
        return screen.availableGeometry()

    def setup_tray_icon(self):
        logo_path = get_asset_path(os.path.join("assets", "portalbig.png"))
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(logo_path))

        self.tray_menu = QMenu()
        self.tray_menu.setStyleSheet("""
            QMenu {
                background-color: #1f1f1f;
                border: 1px solid #333333;
                border-radius: 8px;
                padding: 4px;
                color: #ececec;
                font-family: "Segoe UI";
            }
            QMenu::item { padding: 6px 16px; border-radius: 4px; }
            QMenu::item:selected { background-color: #333333; }
            QMenu::separator { height: 1px; background: #333333; margin: 4px 8px; }
        """)

        self.toggle_action = QAction("Show / Hide Widget", self)
        self.toggle_action.triggered.connect(self.toggle_widget)
        self.tray_menu.addAction(self.toggle_action)

        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self.open_settings_directly)
        self.tray_menu.addAction(settings_action)

        reset_position_action = QAction("Reset Window Position", self)
        reset_position_action.triggered.connect(self.reset_window_position)
        self.tray_menu.addAction(reset_position_action)

        self.tray_menu.addSeparator()

        quit_action = QAction("Quit Portal", self)
        quit_action.triggered.connect(self.quit_app)
        self.tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.show()
        self.tray_icon.activated.connect(self.on_tray_icon_activated)

    def toggle_widget(self):
        if self.chat_panel.isVisible() or self.isVisible():
            self.chat_panel.hide()
            self.hide()
        else:
            self.show()
            self.raise_()

    def reset_window_position(self):
        screen = self.target_screen_geometry()

        bubble_x = screen.center().x() - (self.width() // 2)
        bubble_y = screen.center().y() - (self.height() // 2)
        self.move(bubble_x, bubble_y)

        panel_x, panel_y = self.calculate_chat_position()
        panel_x = max(screen.left(), min(panel_x, screen.left() + screen.width() - self.chat_panel.width()))
        panel_y = max(screen.top(), min(panel_y, screen.top() + screen.height() - self.chat_panel.height()))
        self.chat_panel.move(panel_x, panel_y)

        if self.chat_panel.isVisible():
            self.chat_panel.raise_()
        if self.isVisible():
            self.raise_()

    def open_settings_directly(self):
        if self.chat_panel.isVisible() and self.chat_panel.setting_panel.isVisible():
            self.chat_panel.hide()
            self.show()
            self.raise_()
            return

        if not self.chat_panel.isVisible():
            self.hide()
            target_x, target_y = self.calculate_chat_position()
            self.chat_panel.move(target_x, target_y)
            self.chat_panel.show()
            self.chat_panel.raise_()

        self.chat_panel.content_stack.setCurrentWidget(self.chat_panel.setting_panel)
        self.chat_panel.raise_()
        self.chat_panel.activateWindow()

    def quit_app(self):
        self.tray_icon.hide()
        QApplication.quit()

    def on_tray_icon_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_widget()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if self.is_hovered:
            painter.setBrush(QColor(15, 15, 15, 170))
            painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
            painter.drawRoundedRect(2, 2, self.width() - 4, self.height() - 4, 20, 20)

        if getattr(self, "cached_logo", None):
            x = (self.width() - self.cached_logo.width()) // 2
            y = (self.height() - self.cached_logo.height()) // 2
            painter.drawPixmap(x, y, self.cached_logo)

    def calculate_chat_position(self):
        screen = self.target_screen_geometry()
        panel_w = self.chat_panel.width()
        panel_h = self.chat_panel.height()

        target_x = max(screen.left(), min(self.x() - 350, screen.left() + screen.width() - panel_w))
        target_y = max(screen.top(), min(self.y() - 450, screen.top() + screen.height() - panel_h))
        return target_x, target_y

    def open_chat(self):
        target_x, target_y = self.calculate_chat_position()
        self.final_chat_x = target_x
        self.final_chat_y = target_y

        start_rect = QRect(self.x(), self.y(), self.width(), self.height())
        end_rect = QRect(target_x, target_y, self.chat_panel.width(), self.chat_panel.height())

        self.hide()
        self.animation_window.grow_from_to(start_rect, end_rect)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.was_dragging = False
            event.accept()
        elif event.button() == Qt.RightButton:
            self.tray_menu.exec(event.globalPosition().toPoint())
            event.accept()
    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.was_dragging = True
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if not self.was_dragging:
                self.open_chat()
            event.accept()

    def show_chat_panel_after_animation(self):
        self.chat_panel.move(self.final_chat_x, self.final_chat_y)
        self.chat_panel.reset_to_browser()
        self.chat_panel.show()
        self.chat_panel.raise_()

    def enterEvent(self, event):
        self.is_hovered = True
        self.update()

    def leaveEvent(self, event):
        self.is_hovered = False
        self.update()

    def close_chat_with_animation(self):
        start_rect = QRect(self.chat_panel.x(), self.chat_panel.y(), self.chat_panel.width(), self.chat_panel.height())
        end_rect = QRect(self.x(), self.y(), self.width(), self.height())
        self.chat_panel.reset_to_browser()
        self.chat_panel.hide()
        self.animation_window.shrink_from_to(start_rect, end_rect)

    def show_bubble_after_animation(self):
        self.show()
        self.raise_()