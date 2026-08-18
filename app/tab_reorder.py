from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPoint,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRect,
    Signal,
    Qt,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QPushButton,
)


SLIDE_DURATION_MS = 160
DROP_DURATION_MS = 140
REORDER_OVERLAP_RATIO = 0.70


class ReorderableTabButton(QPushButton):
    """Provider button that preserves clicks while exposing tab drag events."""

    drag_started = Signal(str, QPoint)
    drag_moved = Signal(str, QPoint)
    drag_finished = Signal(str)

    def __init__(self, text, llm_id, parent=None):
        super().__init__(text, parent)
        self.llm_id = llm_id
        self.drag_start_global = None
        self.is_reordering = False

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_start_global = event.globalPosition().toPoint()
            self.is_reordering = False

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self.drag_start_global is not None:
            current_global = event.globalPosition().toPoint()
            drag_distance = (current_global - self.drag_start_global).manhattanLength()

            if not self.is_reordering and drag_distance >= QApplication.startDragDistance():
                self.is_reordering = True
                self.setDown(False)
                self.drag_started.emit(self.llm_id, self.drag_start_global)

            if self.is_reordering:
                self.drag_moved.emit(self.llm_id, current_global)
                event.accept()
                return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        was_reordering = self.is_reordering
        self.drag_start_global = None
        self.is_reordering = False

        if event.button() == Qt.LeftButton and was_reordering:
            self.setDown(False)
            self.drag_finished.emit(self.llm_id)
            event.accept()
            return

        super().mouseReleaseEvent(event)


class TabReorderController(QObject):
    """Runs Chrome-style provider-tab dragging independently of panel data."""

    order_changed = Signal(list)
    order_committed = Signal(list)

    def __init__(self, container, layout, parent=None):
        super().__init__(parent)
        self.container = container
        self.layout = layout
        self.buttons = {}
        self.tab_ids = []
        self.slot_rects = {}

        self.active_tab_id = None
        self.drag_clone = None
        self.drag_offset_x = 0
        self.hidden_button_effect = None
        self.sibling_animation = None
        self.drop_animation = None

    def set_tabs(self, buttons, ordered_ids):
        self.reset()
        self.buttons = dict(buttons)
        self.tab_ids = list(ordered_ids)
        self._refresh_slot_rects()

    def begin_drag(self, llm_id, press_global):
        button = self.buttons.get(llm_id)
        if button is None or llm_id not in self.tab_ids:
            return

        if self.active_tab_id is not None or self.drag_clone is not None:
            self.reset()

        self.active_tab_id = llm_id
        self._refresh_slot_rects()

        button_top_left = button.mapToGlobal(QPoint(0, 0))
        self.drag_offset_x = press_global.x() - button_top_left.x()

        self.drag_clone = self._make_drag_clone(button)
        self.drag_clone.setGeometry(button.geometry())
        self.drag_clone.show()
        self.drag_clone.raise_()

        self.hidden_button_effect = QGraphicsOpacityEffect(button)
        self.hidden_button_effect.setOpacity(0.0)
        button.setGraphicsEffect(self.hidden_button_effect)
        button.setCursor(Qt.ClosedHandCursor)

    def move_drag(self, llm_id, global_position):
        if llm_id != self.active_tab_id or self.drag_clone is None:
            return

        cursor_in_container = self.container.mapFromGlobal(global_position)
        left_limit, right_limit = self._drag_limits()
        target_left = cursor_in_container.x() - self.drag_offset_x
        target_left = max(left_limit, min(target_left, right_limit))
        self.drag_clone.move(target_left, self.drag_clone.y())

        target_index = self._target_index(self.drag_clone.geometry())
        source_index = self.tab_ids.index(llm_id)
        if target_index != source_index:
            self._move_tab(source_index, target_index)

        self.drag_clone.raise_()

    def finish_drag(self, llm_id):
        if llm_id != self.active_tab_id or self.drag_clone is None:
            return

        self.order_committed.emit(list(self.tab_ids))
        target_rect = self.slot_rects.get(llm_id, self.buttons[llm_id].geometry())

        self._stop_drop_animation()
        animation = QPropertyAnimation(self.drag_clone, b"geometry", self)
        animation.setDuration(DROP_DURATION_MS)
        animation.setStartValue(self.drag_clone.geometry())
        animation.setEndValue(target_rect)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.finished.connect(lambda: self._finish_drop(animation))
        self.drop_animation = animation
        animation.start()

    def reset(self):
        self._stop_sibling_animation()
        self._stop_drop_animation()
        self._restore_dragged_button()

        if self.drag_clone is not None:
            self.drag_clone.deleteLater()

        self.drag_clone = None
        self.active_tab_id = None
        self.hidden_button_effect = None

    def _make_drag_clone(self, source_button):
        clone = QPushButton(source_button.text(), self.container)
        clone.setObjectName(source_button.objectName())
        clone.setStyleSheet(source_button.styleSheet())
        clone.setFont(source_button.font())
        clone.setIcon(source_button.icon())
        clone.setIconSize(source_button.iconSize())
        clone.setFixedSize(source_button.size())
        clone.setFocusPolicy(Qt.NoFocus)
        clone.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        clone.setCursor(Qt.ClosedHandCursor)

        shadow = QGraphicsDropShadowEffect(clone)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 150))
        clone.setGraphicsEffect(shadow)
        return clone

    def _drag_limits(self):
        if not self.slot_rects or self.drag_clone is None:
            return 0, 0

        left = min(rect.left() for rect in self.slot_rects.values())
        right_edge = max(rect.right() for rect in self.slot_rects.values())
        right = max(left, right_edge - self.drag_clone.width() + 2)
        return left, right

    def _target_index(self, dragged_rect):
        source_index = self.tab_ids.index(self.active_tab_id)
        target_index = 0
        for index, tab_id in enumerate(self.tab_ids):
            if tab_id == self.active_tab_id:
                continue

            slot = self.slot_rects.get(tab_id)
            if slot is None:
                continue

            overlap = max(1, round(min(dragged_rect.width(), slot.width()) * REORDER_OVERLAP_RATIO))
            if index < source_index:
                target_index += dragged_rect.left() > slot.right() - overlap + 1
            else:
                target_index += dragged_rect.right() >= slot.left() + overlap - 1
        return target_index

    def _move_tab(self, source_index, target_index):
        current_rects = {
            tab_id: QRect(button.geometry())
            for tab_id, button in self.buttons.items()
        }
        self._stop_sibling_animation()

        moved_id = self.tab_ids.pop(source_index)
        self.tab_ids.insert(target_index, moved_id)

        dragged_button = self.buttons[moved_id]
        self.layout.removeWidget(dragged_button)
        self.layout.insertWidget(target_index, dragged_button, alignment=Qt.AlignVCenter)
        self.layout.invalidate()
        self.layout.activate()

        target_rects = {
            tab_id: QRect(button.geometry())
            for tab_id, button in self.buttons.items()
        }
        self.slot_rects = target_rects
        self._animate_siblings(current_rects, target_rects)
        self.order_changed.emit(list(self.tab_ids))

    def _animate_siblings(self, current_rects, target_rects):
        group = QParallelAnimationGroup(self)

        for tab_id, button in self.buttons.items():
            if tab_id == self.active_tab_id:
                continue

            start_rect = current_rects.get(tab_id)
            end_rect = target_rects.get(tab_id)
            if start_rect is None or end_rect is None or start_rect == end_rect:
                continue

            button.setGeometry(start_rect)
            animation = QPropertyAnimation(button, b"geometry", group)
            animation.setDuration(SLIDE_DURATION_MS)
            animation.setStartValue(start_rect)
            animation.setEndValue(end_rect)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            group.addAnimation(animation)

        if group.animationCount() == 0:
            group.deleteLater()
            return

        group.finished.connect(lambda: self._clear_sibling_animation(group))
        self.sibling_animation = group
        group.start()

    def _refresh_slot_rects(self):
        self.layout.invalidate()
        self.layout.activate()
        self.slot_rects = {
            tab_id: QRect(button.geometry())
            for tab_id, button in self.buttons.items()
        }

    def _finish_drop(self, animation):
        if self.drop_animation is not animation:
            return

        self.drop_animation = None
        animation.deleteLater()
        self._restore_dragged_button()

        if self.drag_clone is not None:
            self.drag_clone.deleteLater()

        self.drag_clone = None
        self.active_tab_id = None
        self.hidden_button_effect = None

    def _restore_dragged_button(self):
        button = self.buttons.get(self.active_tab_id)
        if button is not None:
            button.setGraphicsEffect(None)
            button.unsetCursor()

    def _stop_sibling_animation(self):
        if self.sibling_animation is not None:
            animation = self.sibling_animation
            self.sibling_animation = None
            animation.stop()
            animation.deleteLater()

    def _clear_sibling_animation(self, animation):
        if self.sibling_animation is animation:
            self.sibling_animation = None
        animation.deleteLater()

    def _stop_drop_animation(self):
        if self.drop_animation is not None:
            animation = self.drop_animation
            self.drop_animation = None
            animation.stop()
            animation.deleteLater()

