"""Plain text editor with line numbers and subtle indent guides."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QPlainTextEdit, QWidget


class _SideArea(QWidget):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.side_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_side_area(event)


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.theme: dict = {}
        self.syntax_language: str | None = None
        self.indent_width = 4
        self.tab_width = 4
        self.side_area = _SideArea(self)
        self.blockCountChanged.connect(self._update_side_width)
        self.updateRequest.connect(self._update_side_area)
        self.cursorPositionChanged.connect(self.side_area.update)
        self.verticalScrollBar().valueChanged.connect(self.side_area.update)
        self._update_side_width()

    def side_width(self) -> int:
        digits = max(2, len(str(self.blockCount())))
        return 8 + self.fontMetrics().horizontalAdvance("9") * digits + 7

    def _update_side_width(self, *_):
        self.setViewportMargins(self.side_width(), 0, 0, 0)
        self._place_side_area()

    def _place_side_area(self):
        rect = self.contentsRect()
        self.side_area.setGeometry(rect.left(), rect.top(), self.side_width(), rect.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_side_area()

    def _update_side_area(self, rect, dy):
        if dy:
            self.side_area.scroll(0, dy)
        else:
            self.side_area.update(0, rect.y(), self.side_area.width(), rect.height())

    def set_theme(self, theme: dict):
        self.theme = theme
        self.side_area.update()
        self.viewport().update()

    def set_syntax_language(self, language: str | None):
        if self.syntax_language != language:
            self.syntax_language = language
            self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.syntax_language is None or not self.theme:
            return
        self._paint_indent_guides(event)

    def _paint_indent_guides(self, event):
        painter = QPainter(self.viewport())
        painter.setClipRect(event.rect())
        color = QColor(self.theme["muted"])
        color.setAlpha(42)
        painter.setPen(QPen(color, 1, Qt.PenStyle.DotLine))
        space_width = self.fontMetrics().horizontalAdvance(" ")
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        while block.isValid() and top <= event.rect().bottom():
            height = self.blockBoundingRect(block).height()
            if block.isVisible() and top + height >= event.rect().top():
                columns = 0
                for character in block.text():
                    if character == " ":
                        columns += 1
                    elif character == "\t":
                        columns += self.tab_width - columns % self.tab_width
                    else:
                        break
                # Draw on exact indent stops, only while that stop is still
                # whitespace. A guide at 3.5 columns looks like three spaces.
                levels = min((columns - 1) // self.indent_width, 32)
                for level in range(1, levels + 1):
                    x = round(self.contentOffset().x() + level * self.indent_width * space_width)
                    if event.rect().left() <= x <= event.rect().right():
                        painter.drawLine(x, round(top), x, round(top + height))
            block = block.next()
            top += height

    def paint_side_area(self, event):
        if not self.theme:
            return
        painter = QPainter(self.side_area)
        c = self.theme
        painter.fillRect(event.rect(), QColor(c["background"]))
        height = self.side_area.height()
        divider = QColor(c["border"])
        divider.setAlpha(90)
        painter.setPen(QPen(divider, 1))
        painter.drawLine(self.side_width() - 1, 0, self.side_width() - 1, height)
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        while block.isValid() and top <= event.rect().bottom():
            block_height = round(self.blockBoundingRect(block).height())
            if block.isVisible() and top + block_height >= event.rect().top():
                painter.setPen(QColor(c["accent"] if number == self.textCursor().blockNumber() else c["muted"]))
                painter.drawText(
                    2, top, self.side_width() - 9,
                    block_height, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    str(number + 1),
                )
            block = block.next()
            number += 1
            top += block_height
