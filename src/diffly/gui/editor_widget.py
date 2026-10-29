from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QVBoxLayout, QWidget

from diffly.core.diff.engine import Hunk, TextSpan
from diffly.core.documents.model import DocumentState
from diffly.gui.code_editor import CodeEditor
from diffly.gui.syntax_highlighter import SyntaxHighlighter


class EditorWidget(QWidget):
    edited = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = DocumentState()
        self.theme: dict = {}
        self.editor = CodeEditor(parent=self)
        self.highlighter = SyntaxHighlighter(self.editor.document())
        self.syntax_language = None
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setTabStopDistance(self.editor.fontMetrics().horizontalAdvance(" ") * 4)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.editor)
        self.editor.document().contentsChange.connect(self._on_content_change)

    def _on_content_change(self, _position: int, removed: int, added: int):
        if not (removed or added):
            return
        self.state.revision += 1
        self.edited.emit()

    def load_text(self, text: str):
        self.editor.setPlainText(text)
        self.state.saved_revision = self.state.revision

    def text(self) -> str:
        return self.editor.toPlainText()

    def set_font(self, font, tab_width: int, indent_guide_width: int = 4):
        self.editor.setFont(font)
        self.editor.setTabStopDistance(self.editor.fontMetrics().horizontalAdvance(" ") * tab_width)
        self.editor.tab_width = tab_width
        self.editor.indent_width = indent_guide_width
        self.editor._update_side_width()
        self.editor.set_placeholder_ranges(self.editor.placeholder_ranges)
        self.editor.viewport().update()

    def set_syntax_language(self, language: str | None):
        self.syntax_language = language
        self.editor.set_syntax_language(language)

    def set_theme(self, theme: dict):
        self.theme = theme
        self.editor.set_theme(theme)
        self.highlighter.set_theme(theme)

    def apply_text(self, text: str):
        cursor = self.editor.textCursor()
        old_position = cursor.position()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)
        cursor.endEditBlock()
        cursor.setPosition(min(old_position, len(text)))
        self.editor.setTextCursor(cursor)

    def show_hunks(self, hunks: tuple[Hunk, ...], left: bool, spans: tuple[TextSpan, ...] = ()):
        selections = []
        placeholders = []
        for hunk in hunks:
            start, end = (hunk.left_start, hunk.left_end) if left else (hunk.right_start, hunk.right_end)
            other_start, other_end = (
                (hunk.right_start, hunk.right_end) if left
                else (hunk.left_start, hunk.left_end)
            )
            missing = (other_end - other_start) - (end - start)
            if missing > 0:
                placeholders.append((end, missing))
            if start == end:
                continue
            key = "diff_replace" if hunk.tag == "replace" else (
                "diff_removed" if left else "diff_added"
            )
            color = QColor(self.theme.get(key, "#fff1b8"))
            for line in range(start, end):
                block = self.editor.document().findBlockByNumber(line)
                if not block.isValid():
                    break
                selection = QTextEdit.ExtraSelection()
                selection.cursor = QTextCursor(block)
                selection.format = QTextCharFormat()
                selection.format.setBackground(color)
                selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
                selections.append(selection)
        for span in spans:
            block = self.editor.document().findBlockByNumber(span.line)
            if not block.isValid():
                continue
            line_text = block.text()
            if span.end > len(line_text):
                continue
            start = block.position() + len(line_text[:span.start].encode("utf-16-le")) // 2
            end = block.position() + len(line_text[:span.end].encode("utf-16-le")) // 2
            selection = QTextEdit.ExtraSelection()
            selection.cursor = QTextCursor(self.editor.document())
            selection.cursor.setPosition(start)
            selection.cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            selection.format = QTextCharFormat()
            selection.format.setBackground(QColor(self.theme.get("diff_text", "#d38b44")))
            selections.append(selection)
        self.editor.setExtraSelections(selections)
        self.editor.set_placeholder_ranges(tuple(placeholders))

    def select_hunk(self, start: int, end: int, column: int = 0):
        document = self.editor.document()
        block = document.findBlockByNumber(min(start, document.blockCount() - 1))
        if block.isValid():
            cursor = QTextCursor(block)
            if start >= document.blockCount():
                column = len(block.text())
            column = min(column, len(block.text()))
            offset = len(block.text()[:column].encode("utf-16-le")) // 2
            cursor.setPosition(block.position() + offset)
            self.editor.setTextCursor(cursor)
            self.editor.centerCursor()
