from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Signal
from PySide6.QtGui import QPainter, QColor


class AnimationWindow(QWidget):
    open_finished = Signal()
    close_finished = Signal()
#
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(15, 15, 15, 220))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(self.rect(), 24, 24)

    def grow_from_to(self, start_rect, end_rect):
        self.run_animation(start_rect, end_rect, self.finish_open)

    def shrink_from_to(self, start_rect, end_rect):
        self.run_animation(start_rect, end_rect, self.finish_close)

    def run_animation(self, start_rect, end_rect, finished_function):
        self.setGeometry(start_rect)
        self.show()
        self.raise_()

        self.animation = QPropertyAnimation(self, b"geometry")
        self.animation.setDuration(260)
        self.animation.setStartValue(start_rect)
        self.animation.setEndValue(end_rect)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.finished.connect(finished_function)
        self.animation.start()

    def finish_open(self):
        self.hide()
        self.open_finished.emit()

    def finish_close(self):
        self.hide()
        self.close_finished.emit()
