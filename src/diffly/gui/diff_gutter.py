"""Center strip between the panes with per-difference transfer arrows."""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

from diffly.gui.icons import toolbar_icon

BUTTON = 16


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
        left_editor.verticalScrollBar().valueChanged.connect(self.update)
        for editor in (left_editor, right_editor):
            editor.blockCountChanged.connect(self.update)

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

    def _band(self, hunk) -> tuple[float, float] | None:
        # Panes are aligned, so the longer side's real lines span the whole difference.
        left_count, right_count = hunk.left_end - hunk.left_start, hunk.right_end - hunk.right_start
        editor, start, count = (
            (self.left_editor, hunk.left_start, left_count) if left_count >= right_count
            else (self.right_editor, hunk.right_start, right_count)
        )
        top = editor.line_top(start)
        if top is None:
            return None
        offset = self.mapFromGlobal(editor.viewport().mapToGlobal(QPointF(0, 0))).y()
        return offset + top, offset + top + count * editor.line_height()

    def _visible(self):
        """Yield (row, hunk, top, bottom) for differences on screen."""
        for row, hunk in enumerate(self.hunks):
            band = self._band(hunk)
            if band is None:
                continue
            top, bottom = band
            if top > self.height():
                break
            if bottom >= 0:
                yield row, hunk, top, bottom

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
        width, middle = self.width(), self.width() / 2
        line_height = self.left_editor.line_height()
        for row, hunk, top, _bottom in self._visible():
            # Meld-style ribbon: each side's real lines start at the shared top,
            # the shorter side ends early and the bottom edge curves between them.
            left_bottom = top + (hunk.left_end - hunk.left_start) * line_height
            right_bottom = top + (hunk.right_end - hunk.right_start) * line_height
            path = QPainterPath(QPointF(0, top))
            path.lineTo(width, top)
            path.lineTo(width, right_bottom)
            path.cubicTo(middle, right_bottom, middle, left_bottom, 0, left_bottom)
            path.closeSubpath()
            color = QColor(c[{"insert": "overview_added", "delete": "overview_removed"}.get(hunk.tag, "overview_replace")])
            edge = QColor(color)
            edge.setAlpha(170)
            color.setAlpha(70)
            painter.setPen(QPen(edge, 1))
            painter.setBrush(color)
            painter.drawPath(path)
            for source_is_left, kind in ((True, "right"), (False, "left")):
                rect = self.button_rect(top, source_is_left)
                if self._hover == (row, source_is_left):
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(c["border"]))
                    painter.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 3, 3)
                self._icons[f"{prefix}_{kind}"].paint(painter, rect.toRect())

    def _button_at(self, point: QPointF) -> tuple[int, bool] | None:
        for row, _hunk, top, _bottom in self._visible():
            for source_is_left in (True, False):
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
