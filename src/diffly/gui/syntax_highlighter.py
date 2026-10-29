"""Applies precomputed syntax spans in small GUI-thread batches."""

from collections import defaultdict

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextBlockUserData, QTextCharFormat


class _BlockSyntax(QTextBlockUserData):
    """Travel with a text block when lines are inserted or removed above it."""

    def __init__(self, text, spans):
        super().__init__()
        self.text = text
        self.spans = spans

    def follow_edit(self, text):
        if self.text == text:
            return
        old = self.text
        prefix = 0
        while prefix < min(len(old), len(text)) and old[prefix] == text[prefix]:
            prefix += 1
        suffix = 0
        while (suffix < min(len(old), len(text)) - prefix
               and old[len(old) - suffix - 1] == text[len(text) - suffix - 1]):
            suffix += 1
        old_end, new_end = len(old) - suffix, len(text) - suffix
        delta = len(text) - len(old)
        spans = []
        for start, end, kind in self.spans:
            if end <= prefix:
                pass
            elif start >= old_end:
                start, end = start + delta, end + delta
            else:
                start = min(start, prefix)
                end = end + delta if end >= old_end else new_end
            if end > start:
                spans.append((start, end, kind))
        self.text = text
        self.spans = tuple(spans)


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
        self._queue_lines(range(self.document().blockCount()))

    def set_spans(self, spans):
        by_line = defaultdict(list)
        for line, start, end, kind in spans:
            by_line[line].append((start, end, kind))
        self._spans = dict(by_line)
        changed = []
        block = self.document().firstBlock()
        while block.isValid():
            line = block.blockNumber()
            spans = tuple(by_line.get(line, ()))
            cached = block.userData()
            if not isinstance(cached, _BlockSyntax) or cached.text != block.text() or cached.spans != spans:
                if spans or isinstance(cached, _BlockSyntax):
                    block.setUserData(_BlockSyntax(block.text(), spans))
                    changed.append(line)
            block = block.next()
        self._queue_lines(changed)

    def _queue_lines(self, lines):
        # Preserve work queued by an earlier result or theme change.
        self._pending_lines = sorted(set(lines) | set(self._pending_lines[self._next_line:]))
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
        cached = self.currentBlock().userData()
        if not isinstance(cached, _BlockSyntax):
            return
        # Qt rehighlights edited blocks immediately, before the worker replies.
        # Retain and move their colors instead of using stale line-number keys.
        cached.follow_edit(text)
        for start, end, kind in cached.spans:
            fmt = self._formats.get(kind)
            if fmt is not None:
                # Qt formatting offsets count UTF-16 units, not Python codepoints.
                qt_start = len(text[:start].encode("utf-16-le")) // 2
                qt_length = len(text[start:end].encode("utf-16-le")) // 2
                self.setFormat(qt_start, qt_length, fmt)
