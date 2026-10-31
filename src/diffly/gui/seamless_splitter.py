"""Splitter whose handles do not draw separator lines."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QSplitterHandle


class _SeamlessSplitterHandle(QSplitterHandle):
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, _event):
        pass


class SeamlessSplitter(QSplitter):
    def createHandle(self):
        return _SeamlessSplitterHandle(self.orientation(), self)
