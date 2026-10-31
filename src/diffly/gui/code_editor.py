"""Plain text editor with line numbers, indent guides, and diff alignment."""

from bisect import bisect_right

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QPlainTextDocumentLayout, QPlainTextEdit, QWidget


class _AlignedDocumentLayout(QPlainTextDocumentLayout):
    """Reserve display space for missing lines without changing document text."""

    def __init__(self, document):
        super().__init__(document)
        self.before_first = 0.0
        self.after: dict[int, float] = {}
        self.line_height = 0.0

    def blockBoundingRect(self, block):
        if block.blockNumber() == 0 and block.layout().lineCount():
            line = block.layout().lineAt(0)
            position = line.position()
            line.setPosition(QPointF(position.x(), self.before_first))
        rect = super().blockBoundingRect(block)
        extra = self.after.get(block.blockNumber(), 0)
        if extra:
            rect.setHeight(rect.height() + extra)
        return rect

    def set_gaps(self, ranges: tuple[tuple[int, int], ...], line_height: float):
        before_first = 0.0
        after: dict[int, float] = {}
        for start, count in ranges:
            if start == 0:
                before_first += count * line_height
            else:
                after[start - 1] = after.get(start - 1, 0) + count * line_height
        if (before_first, after) == (self.before_first, self.after):
            self.line_height = line_height
            return
        self.before_first, self.after = before_first, after
        self.line_height = line_height
        document = self.document()
        document.markContentsDirty(0, document.characterCount())


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
        self.line_numbers_visible = True
        self.indent_width = 4
        self.tab_width = 4
        self.placeholder_ranges: tuple[tuple[int, int], ...] = ()
        self.merge_conflict_ranges: tuple[tuple[int, int, bool], ...] = ()
        self._gap_starts: tuple[int, ...] = ()
        self._visual_gap_starts: tuple[int, ...] = ()
        self._gap_totals: tuple[int, ...] = ()
        self._aligned_layout = _AlignedDocumentLayout(self.document())
        self.document().setDocumentLayout(self._aligned_layout)
        self.side_area = _SideArea(self)
        self.blockCountChanged.connect(self._update_side_width)
        self.updateRequest.connect(self._update_side_area)
        self.cursorPositionChanged.connect(self.side_area.update)
        self.verticalScrollBar().valueChanged.connect(self.side_area.update)
        self._update_side_width()

    def side_width(self) -> int:
        if not self.line_numbers_visible:
            return 0
        digits = max(2, len(str(self.blockCount())))
        return 8 + self.fontMetrics().horizontalAdvance("9") * digits + 7

    def set_line_numbers_visible(self, visible: bool):
        if self.line_numbers_visible == visible:
            return
        self.line_numbers_visible = visible
        self.side_area.setVisible(visible)
        self._update_side_width()

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

    def set_placeholder_ranges(self, ranges: tuple[tuple[int, int], ...]):
        combined: dict[int, int] = {}
        for start, count in ranges:
            combined[start] = combined.get(start, 0) + count
        self.placeholder_ranges = tuple(sorted(combined.items()))
        starts = []
        visual_starts = []
        totals = []
        total = 0
        for start, count in self.placeholder_ranges:
            starts.append(start)
            visual_starts.append(start + total)
            total += count
            totals.append(total)
        self._gap_starts = tuple(starts)
        self._visual_gap_starts = tuple(visual_starts)
        self._gap_totals = tuple(totals)
        block = self.document().firstBlock()
        self._aligned_layout.ensureBlockLayout(block)
        line_height = (block.layout().lineAt(0).height() if block.layout().lineCount()
                       else self.fontMetrics().height())
        self._aligned_layout.set_gaps(self.placeholder_ranges, line_height)
        self.viewport().update()
        self.side_area.update()

    def set_merge_conflict_ranges(self, ranges: tuple[tuple[int, int, bool], ...]):
        self.merge_conflict_ranges = ranges
        self.viewport().update()

    def visual_row(self, block_number: int) -> int:
        """Position of a real block in the aligned pair of documents."""
        index = bisect_right(self._gap_starts, block_number) - 1
        return block_number + (self._gap_totals[index] if index >= 0 else 0)

    def line_height(self) -> float:
        return self._aligned_layout.line_height or self.fontMetrics().height()

    def line_top(self, block_number: int) -> float | None:
        """Viewport y of a real line's text, below any placeholder gap before it."""
        block = self.document().findBlockByNumber(block_number)
        if not block.isValid():
            return None
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        return top + (self._aligned_layout.before_first if block_number == 0 else 0)

    def block_for_visual_row(self, row: int) -> int:
        index = bisect_right(self._visual_gap_starts, row) - 1
        if index < 0:
            return max(0, row)
        start, count = self.placeholder_ranges[index]
        if row < self._visual_gap_starts[index] + count:
            return max(0, start - 1)
        return max(0, row - self._gap_totals[index])

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.theme:
            return
        if self.syntax_language is not None:
            self._paint_indent_guides(event)
        self._paint_placeholders(event)
        self._paint_merge_conflict_borders(event)

    def _paint_merge_conflict_borders(self, event):
        if not self.merge_conflict_ranges:
            return
        painter = QPainter(self.viewport())
        painter.setClipRect(event.rect())
        width = self.viewport().width()
        for start, end, unresolved in self.merge_conflict_ranges:
            if start >= end:
                continue
            top = self.line_top(start)
            last = self.line_top(end - 1)
            if top is None or last is None:
                continue
            color = QColor(self.theme["overview_removed" if unresolved else "overview_added"])
            painter.setPen(QPen(color, 1))
            painter.drawLine(0, round(top), width, round(top))
            bottom = round(last + self.line_height())
            painter.drawLine(0, bottom, width, bottom)

    def _paint_placeholders(self, event):
        if not self.placeholder_ranges:
            return
        painter = QPainter(self.viewport())
        painter.setClipRect(event.rect())
        line_height = self._aligned_layout.line_height
        color = QColor(self.theme["muted"])
        color.setAlpha(65)
        painter.setPen(QPen(color, 1))
        width = self.viewport().width()
        for start, count in self.placeholder_ranges:
            if start == 0:
                block = self.document().firstBlock()
                top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
            else:
                previous = self.document().findBlockByNumber(start - 1)
                if not previous.isValid():
                    continue
                top = self.blockBoundingGeometry(previous).translated(self.contentOffset()).bottom() - count * line_height
            bottom = top + count * line_height
            area = QRectF(0, top, width, bottom - top)
            if not area.intersects(QRectF(event.rect())):
                continue
            painter.save()
            painter.setClipRect(area, Qt.ClipOperation.IntersectClip)
            painter.fillRect(area, QColor(self.theme["background"]))
            visible_top = max(top, event.rect().top())
            visible_bottom = min(bottom, event.rect().bottom())
            origin = int(top) - width
            y = origin + int((visible_top - width - origin) // 10) * 10
            while y <= visible_bottom:
                painter.drawLine(0, y, width, y + width)
                y += 10
            painter.restore()

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
                text_top = top + (self._aligned_layout.before_first if block.blockNumber() == 0 else 0)
                text_height = height - self._aligned_layout.after.get(block.blockNumber(), 0)
                if block.blockNumber() == 0:
                    text_height -= self._aligned_layout.before_first
                if block.layout().lineCount():
                    text_height = min(text_height, block.layout().lineAt(0).height())
                columns = 0
                for character in block.text():
                    if character == " ":
                        columns += 1
                    elif character == "\t":
                        columns += self.tab_width - columns % self.tab_width
                    else:
                        break
                # Put each guide inside the whitespace cell preceding its
                # indent stop, never on the first non-whitespace character.
                levels = min(columns // self.indent_width, 32)
                for level in range(1, levels + 1):
                    x = round(self.contentOffset().x() + (level * self.indent_width - 0.5) * space_width)
                    if event.rect().left() <= x <= event.rect().right():
                        painter.drawLine(x, round(text_top), x, round(text_top + text_height))
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
                text_top = top + (self._aligned_layout.before_first if number == 0 else 0)
                text_height = block_height - self._aligned_layout.after.get(number, 0)
                if number == 0:
                    text_height -= self._aligned_layout.before_first
                painter.setPen(QColor(c["accent"] if number == self.textCursor().blockNumber() else c["muted"]))
                painter.drawText(
                    2, round(text_top), self.side_width() - 9,
                    round(text_height), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    str(number + 1),
                )
            block = block.next()
            number += 1
            top += block_height
