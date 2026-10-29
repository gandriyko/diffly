"""One right-edge overview for diff positions and synchronized scrolling."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget


class DiffOverview(QWidget):
    def __init__(self, left_editor, right_editor, scroll_controller, parent=None):
        super().__init__(parent)
        self.left_editor = left_editor
        self.right_editor = right_editor
        self.scroll_controller = scroll_controller
        self.theme: dict = {}
        self.hunks = ()
        self._drag_offset = None
        self.setFixedWidth(18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        scroll_controller.valueChanged.connect(self.update)
        scroll_controller.rangeChanged.connect(self.update)
        for editor in (left_editor, right_editor):
            editor.blockCountChanged.connect(self.update)

    def sizeHint(self):
        return QSize(18, 0)

    def set_theme(self, theme: dict):
        self.theme = theme
        self.update()

    def set_hunks(self, hunks):
        self.hunks = hunks
        self.update()

    def _total_lines(self) -> int:
        return max(1, self.left_editor.blockCount(), self.right_editor.blockCount())

    def _viewport_rect(self) -> tuple[int, int]:
        total = max(1, self.scroll_controller.maximum() + self.scroll_controller.pageStep())
        top = round(self.height() * self.scroll_controller.value() / total)
        size = max(12, round(self.height() * self.scroll_controller.pageStep() / total))
        return top, min(self.height() - top, size)

    def paintEvent(self, event):
        if not self.theme:
            return
        c = self.theme
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(c["surface"]))
        track_x, track_w = 4, self.width() - 8
        painter.fillRect(track_x, 0, track_w, self.height(), QColor(c["background"]))
        total = self._total_lines()
        for hunk in self.hunks:
            start = max(hunk.left_start, hunk.right_start)
            end = max(hunk.left_end, hunk.right_end, start + 1)
            top = round(self.height() * start / total)
            bottom = round(self.height() * end / total)
            color_key = {
                "insert": "overview_added",
                "delete": "overview_removed",
            }.get(hunk.tag, "overview_replace")
            painter.fillRect(track_x, top, track_w, max(3, bottom - top), QColor(c[color_key]))
        if self.scroll_controller.maximum() > 0:
            top, size = self._viewport_rect()
            outline = QColor(c["accent"])
            outline.setAlpha(190)
            painter.setPen(QPen(outline, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(track_x - 1, top, track_w + 1, max(1, size - 1))

    def _scroll_to(self, y: float):
        top = max(0, min(self.height(), y - (self._drag_offset or 0)))
        total = max(1, self.scroll_controller.maximum() + self.scroll_controller.pageStep())
        target = round(top / max(1, self.height()) * total)
        self.scroll_controller.setValue(target)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            top, size = self._viewport_rect()
            y = event.position().y()
            self._drag_offset = y - top if top <= y <= top + size else size / 2
            self._scroll_to(y)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None:
            self._scroll_to(event.position().y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        QApplication.sendEvent(self.scroll_controller, event)
        event.accept()
