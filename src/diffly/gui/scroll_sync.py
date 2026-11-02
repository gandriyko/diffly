"""Scroll synchronization shared by the Files and Merge comparison panes."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QScrollBar


def scroll_to_row(panes, row: int, skip=None):
    """Scroll each pane (except `skip`) so the shared visual row is on top."""
    for pane in panes:
        if pane is not skip:
            pane.editor.verticalScrollBar().setValue(pane.editor.block_for_visual_row(row))


def follow_scroll(source, value: int, panes) -> int:
    """Align `panes` with `source` after its scrollbar moved to `value`; return the visual row."""
    row = source.editor.visual_row(value) if value else 0
    scroll_to_row(panes, row, skip=source)
    return row


class HorizontalScrollSync:
    """One visible horizontal scrollbar driving the hidden bars of several panes.

    Shorter documents clamp at their own right edge.
    """

    def __init__(self, panes, bar: QScrollBar):
        self.panes = tuple(panes)
        self.bar = bar
        self._syncing = False
        for pane in self.panes:
            pane.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            pane.editor.horizontalScrollBar().valueChanged.connect(self.scroll)
            pane.editor.horizontalScrollBar().rangeChanged.connect(self.update_range)
        bar.valueChanged.connect(self.scroll)
        self.update_range()

    def scroll(self, value: int):
        if self._syncing:
            return
        self._syncing = True
        try:
            self.bar.setValue(value)
            for pane in self.panes:
                pane.editor.horizontalScrollBar().setValue(value)
        finally:
            self._syncing = False

    def update_range(self, *_):
        bars = tuple(pane.editor.horizontalScrollBar() for pane in self.panes)
        self.bar.setRange(0, max(bar.maximum() for bar in bars))
        self.bar.setPageStep(min(bar.pageStep() for bar in bars))
        self.bar.setSingleStep(bars[0].singleStep())
        self.bar.setVisible(self.bar.maximum() > 0)
        self.scroll(self.bar.value())

    def handle_wheel(self, event, on_bar: bool = False) -> bool:
        """Send horizontal trackpad gestures and Shift+wheel to the shared bar.

        Returns True when the event was consumed. Events on the bar itself
        without pixel deltas are left to the bar.
        """
        horizontal = (on_bar
                      or abs(event.pixelDelta().x()) > abs(event.pixelDelta().y())
                      or abs(event.angleDelta().x()) > abs(event.angleDelta().y())
                      or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        if not horizontal:
            return False
        if not event.pixelDelta().isNull():
            self.bar.setValue(self.bar.value() - (event.pixelDelta().x() or event.pixelDelta().y()))
            event.accept()
            return True
        if on_bar:
            return False
        QApplication.sendEvent(self.bar, event)
        return True
