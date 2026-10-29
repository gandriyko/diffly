"""Applies precomputed syntax spans in small GUI-thread batches."""

from collections import defaultdict

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat


class SyntaxHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self._spans = {}
        self._formats = {}
        self._pending_lines = []
        self._next_line = 0
        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.timeout.connect(self._apply_batch)

    def set_theme(self, theme: dict):
        self._formats = {}
        for kind in ("keyword", "string", "number", "comment", "key", "function"):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(theme[f"syntax_{kind}"]))
            self._formats[kind] = fmt
        self._queue_lines(sorted(self._spans))

    def set_spans(self, spans):
        by_line = defaultdict(list)
        for line, start, end, kind in spans:
            by_line[line].append((start, end, kind))
        old_lines = set(self._spans)
        self._spans = dict(by_line)
        self._queue_lines(sorted(old_lines | set(by_line)))

    def _queue_lines(self, lines):
        self._pending_lines = lines
        self._next_line = 0
        self._batch_timer.stop()
        self._apply_batch()

    def _apply_batch(self):
        document = self.document()
        if document is None:
            return
        end = min(self._next_line + 80, len(self._pending_lines))
        for line in self._pending_lines[self._next_line:end]:
            block = document.findBlockByNumber(line)
            if block.isValid():
                self.rehighlightBlock(block)
        self._next_line = end
        if end < len(self._pending_lines):
            self._batch_timer.start(0)

    def highlightBlock(self, text: str):
        line = self.currentBlock().blockNumber()
        for start, end, kind in self._spans.get(line, ()):
            fmt = self._formats.get(kind)
            if fmt is not None:
                # Qt formatting offsets count UTF-16 units, not Python codepoints.
                qt_start = len(text[:start].encode("utf-16-le")) // 2
                qt_length = len(text[start:end].encode("utf-16-le")) // 2
                self.setFormat(qt_start, qt_length, fmt)
