"""Center strip between the panes with per-difference transfer arrows."""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

from diffly.gui.icons import toolbar_icon

BUTTON = 16


def ribbon_path(width: float, left_top: float, left_bottom: float,
                right_top: float, right_bottom: float) -> QPainterPath:
    middle = width / 2
    path = QPainterPath(QPointF(0, left_top))
    path.cubicTo(middle, left_top, middle, right_top, width, right_top)
    path.lineTo(width, right_bottom)
    path.cubicTo(middle, right_bottom, middle, left_bottom, 0, left_bottom)
    path.closeSubpath()
    return path


class DiffGutter(QWidget):
    # row, source_is_left, append
    transfer_requested = Signal(int, bool, bool)

    def __init__(self, left_editor, right_editor, scroll_controller, parent=None):
        super().__init__(parent)
        self.left_editor = left_editor
        self.right_editor = right_editor
        self.scroll_controller = scroll_controller
        self.theme: dict = {}
        self.hunks = ()
        self.append = False
        self._icons = {}
        self._hover: tuple[int, bool] | None = None
        self.setFixedWidth(2 * BUTTON + 24)
        self.setMouseTracking(True)
        for editor in (left_editor, right_editor):
            editor.verticalScrollBar().valueChanged.connect(self.update)
            editor.blockCountChanged.connect(self.update)
            editor.updateRequest.connect(self._content_updated)

    def _content_updated(self, _rect, dy: int):
        if dy:
            self.update()

    def sizeHint(self):
        return QSize(self.width(), 0)

    def set_theme(self, theme: dict):
        self.theme = theme
        self._icons = {
            kind: toolbar_icon(kind, theme["text"])
            for kind in ("apply_left", "apply_right", "append_left", "append_right")
        }
        self.update()

    def set_hunks(self, hunks):
        self.hunks = hunks
        self._hover = None
        self.update()

    def set_append(self, append: bool):
        self.append = append
        self.update()

    def _edge(self, editor, start: int, end: int) -> tuple[float, float] | None:
        count = editor.document().blockCount()
        if start < count:
            top = editor.line_top(start)
            if start == end:
                top -= sum(gap for line, gap in editor.placeholder_ranges if line == start) * editor.line_height()
        else:
            last = editor.line_top(count - 1)
            top = last + editor.line_height() if last is not None else None
        if top is None:
            return None
        bottom = (editor.line_top(min(end - 1, count - 1)) + editor.line_height()
                  if end > start else top)
        offset = self.mapFromGlobal(editor.viewport().mapToGlobal(QPointF(0, 0))).y()
        return offset + top, offset + bottom

    def _edges(self, hunk) -> tuple[float, float, float, float] | None:
        left = self._edge(self.left_editor, hunk.left_start, hunk.left_end)
        right = self._edge(self.right_editor, hunk.right_start, hunk.right_end)
        if left is None or right is None:
            return None
        return *left, *right

    def _band(self, hunk) -> tuple[float, float] | None:
        edges = self._edges(hunk)
        if edges is None:
            return None
        left_top, left_bottom, right_top, right_bottom = edges
        return min(left_top, right_top), max(left_bottom, right_bottom)

    def _visible(self):
        """Yield visible differences with the actual edges of both panes."""
        for row, hunk in enumerate(self.hunks):
            edges = self._edges(hunk)
            if edges is None:
                continue
            left_top, left_bottom, right_top, right_bottom = edges
            if min(left_top, right_top) > self.height():
                break
            if max(left_bottom, right_bottom) >= 0:
                yield row, hunk, *edges

    def button_rect(self, top: float, source_is_left: bool) -> QRectF:
        x = 3 if source_is_left else self.width() - BUTTON - 3
        return QRectF(x, top + (self.left_editor.line_height() - BUTTON) / 2, BUTTON, BUTTON)

    def paintEvent(self, event):
        if not self.theme:
            return
        c = self.theme
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(c["surface"]))
        prefix = "append" if self.append else "apply"
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        for row, hunk, left_top, left_bottom, right_top, right_bottom in self._visible():
            path = ribbon_path(width, left_top, left_bottom, right_top, right_bottom)
            color = QColor(c[{"insert": "overview_added", "delete": "overview_removed"}.get(hunk.tag, "overview_replace")])
            edge = QColor(color)
            edge.setAlpha(170)
            color.setAlpha(70)
            painter.setPen(QPen(edge, 1))
            painter.setBrush(color)
            painter.drawPath(path)
            for source_is_left, kind in ((True, "right"), (False, "left")):
                rect = self.button_rect(left_top if source_is_left else right_top, source_is_left)
                if self._hover == (row, source_is_left):
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(c["border"]))
                    painter.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 3, 3)
                self._icons[f"{prefix}_{kind}"].paint(painter, rect.toRect())

    def _button_at(self, point: QPointF) -> tuple[int, bool] | None:
        for row, _hunk, left_top, _left_bottom, right_top, _right_bottom in self._visible():
            for source_is_left, top in ((True, left_top), (False, right_top)):
                if self.button_rect(top, source_is_left).contains(point):
                    return row, source_is_left
        return None

    def mouseMoveEvent(self, event):
        hover = self._button_at(event.position())
        if hover != self._hover:
            self._hover = hover
            self.setCursor(Qt.CursorShape.PointingHandCursor if hover else Qt.CursorShape.ArrowCursor)
            if hover is None:
                self.setToolTip("")
            else:
                direction = "left to right" if hover[1] else "right to left"
                self.setToolTip(f"Copy block {direction} (Shift: append below instead of replacing)")
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover = None
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        hit = self._button_at(event.position()) if event.button() == Qt.MouseButton.LeftButton else None
        if hit is None:
            super().mousePressEvent(event)
            return
        self.transfer_requested.emit(hit[0], hit[1], bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        event.accept()

    def wheelEvent(self, event):
        QApplication.sendEvent(self.scroll_controller, event)
        event.accept()
