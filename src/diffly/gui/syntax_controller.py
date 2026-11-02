"""Background syntax highlighting shared by the Files and Merge panes."""

from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from diffly.core.documents.syntax import language_for

# ponytail: larger documents stay plain; raise once tokenizing is incremental.
MAX_HIGHLIGHT_CHARS = 2_000_000


def detect_language(path: Path | None, text: str) -> str | None:
    """Language by file name, then by content for .txt files and unnamed text."""
    sample = text[:512]
    language = language_for(path, sample)
    if language is None and path is not None and path.suffix.lower() == ".txt":
        language = language_for(None, sample)
    return language


class SyntaxController(QObject):
    """Debounce edits, tokenize in the scheduler's worker and apply fresh results.

    A pane's language comes from its path, or from `syntax_path` (for an
    unsaved merge Result, the Left file) when it has none.
    """

    def __init__(self, scheduler, prefix: str, panes, enabled: bool, parent=None):
        super().__init__(parent)
        self.scheduler = scheduler
        self.enabled = enabled
        self._keys = {pane: f"{prefix}-{index}" for index, pane in enumerate(panes)}
        self._panes = {key: pane for pane, key in self._keys.items()}
        self._timers = {}
        for pane in panes:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(180)
            timer.timeout.connect(lambda p=pane: self.request(p))
            pane.edited.connect(timer.start)
            self._timers[pane] = timer
        scheduler.syntax_ready.connect(self.accept)

    def schedule(self, pane):
        """Re-detect and re-tokenize soon, e.g. after a pane got a new path."""
        self._timers[pane].start()

    def request(self, pane):
        text = pane.text()
        pane.set_syntax_language(detect_language(pane.state.path or pane.syntax_path, text))
        if not self.enabled or pane.syntax_language is None or len(text) > MAX_HIGHLIGHT_CHARS:
            pane.highlighter.set_spans(())
            return
        self.scheduler.submit_syntax(self._keys[pane], text, pane.syntax_language, pane.state.revision)

    def accept(self, result):
        key, revision, language, spans = result
        pane = self._panes.get(key)
        if (pane is not None and self.enabled and pane.state.revision == revision
                and pane.syntax_language == language):
            pane.highlighter.set_spans(spans)

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        for pane in self._keys:
            if enabled:
                self.request(pane)
            else:
                self._timers[pane].stop()
                pane.highlighter.set_spans(())

    def stop(self):
        for timer in self._timers.values():
            timer.stop()
