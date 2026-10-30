"""Find and replace panel shown above the status bar."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor, QTextDocument
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QToolButton, QVBoxLayout, QWidget)


class FindPanel(QWidget):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.target = window.left
        self.setObjectName("FindPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(5)

        find_row = QHBoxLayout()
        find_row.setSpacing(6)
        self.target_label = QLabel("Left", self)
        self.target_label.setObjectName("FindTarget")
        self.target_label.setFixedWidth(42)
        find_row.addWidget(self.target_label)
        self.query = QLineEdit(self)
        self.query.setPlaceholderText("Find in current editor")
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self.find_next)
        self.query.textChanged.connect(lambda: self._message(""))
        find_row.addWidget(self.query, 1)
        self.previous_button = QToolButton(self)
        self.previous_button.setObjectName("FindPanelButton")
        self.previous_button.setText("↑")
        self.previous_button.setToolTip("Previous match (Shift+F3)")
        self.previous_button.setAccessibleName("Previous match")
        self.previous_button.clicked.connect(self.find_previous)
        find_row.addWidget(self.previous_button)
        self.next_button = QToolButton(self)
        self.next_button.setObjectName("FindPanelButton")
        self.next_button.setText("↓")
        self.next_button.setToolTip("Next match (F3)")
        self.next_button.setAccessibleName("Next match")
        self.next_button.clicked.connect(self.find_next)
        find_row.addWidget(self.next_button)
        self.match_case = QCheckBox("Aa", self)
        self.match_case.setToolTip("Match case")
        self.match_case.setAccessibleName("Match case")
        find_row.addWidget(self.match_case)
        self.message = QLabel(self)
        self.message.setObjectName("FindMessage")
        self.message.hide()
        find_row.addWidget(self.message)
        close_button = QToolButton(self)
        close_button.setObjectName("FindPanelButton")
        close_button.setText("×")
        close_button.setToolTip("Close search (Esc)")
        close_button.setAccessibleName("Close search")
        close_button.clicked.connect(self.hide_search)
        find_row.addWidget(close_button)
        layout.addLayout(find_row)

        self.replace_row = QWidget(self)
        replace_layout = QHBoxLayout(self.replace_row)
        replace_layout.setContentsMargins(0, 0, 0, 0)
        replace_layout.setSpacing(6)
        spacer = QWidget(self.replace_row)
        spacer.setFixedWidth(42)
        replace_layout.addWidget(spacer)
        self.replacement = QLineEdit(self.replace_row)
        self.replacement.setPlaceholderText("Replace with")
        replace_layout.addWidget(self.replacement, 1)
        self.replace_button = QPushButton("Replace", self.replace_row)
        self.replace_button.clicked.connect(self.replace_one)
        replace_layout.addWidget(self.replace_button)
        self.replace_all_button = QPushButton("Replace All", self.replace_row)
        self.replace_all_button.clicked.connect(self.replace_all)
        replace_layout.addWidget(self.replace_all_button)
        layout.addWidget(self.replace_row)
        shortcut = QShortcut(QKeySequence.Cancel, self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.hide_search)
        self.hide()

    def set_target(self, pane):
        if self.target is pane:
            return
        self.target = pane
        self.target_label.setText("Left" if pane is self.window.left else "Right")
        self._message("")

    def show_search(self, replace: bool = False):
        self.set_target(self.window._active_pane())
        self.replace_row.setVisible(replace)
        self._message("")
        selected = self.target.editor.textCursor().selectedText()
        if selected and "\u2029" not in selected and len(selected) <= 200:
            self.query.setText(selected)
        self.show()
        self.query.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.query.selectAll()

    def hide_search(self):
        self.hide()
        self.target.editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def _message(self, message: str):
        self.message.setText(message)
        self.message.setVisible(bool(message))

    def _flags(self, backwards=False):
        flags = QTextDocument.FindFlag(0)
        if self.match_case.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if backwards:
            flags |= QTextDocument.FindFlag.FindBackward
        return flags

    def _find(self, backwards=False):
        term = self.query.text()
        if not term:
            self._message("Enter text")
            return False
        document = self.target.editor.document()
        flags = self._flags(backwards)
        found = document.find(term, self.target.editor.textCursor(), flags)
        if found.isNull():
            start = QTextCursor(document)
            if backwards:
                start.movePosition(QTextCursor.MoveOperation.End)
            found = document.find(term, start, flags)
        if found.isNull():
            self._message("No matches")
            return False
        self.target.editor.setTextCursor(found)
        self.target.editor.ensureCursorVisible()
        self._message("")
        return True

    def find_next(self):
        return self._find()

    def find_previous(self):
        return self._find(backwards=True)

    def replace_one(self):
        term = self.query.text()
        if not term:
            self._message("Enter text")
            return
        cursor = self.target.editor.textCursor()
        selected = cursor.selectedText().replace("\u2029", "\n")
        matches = selected == term if self.match_case.isChecked() else selected.casefold() == term.casefold()
        if not matches:
            if not self._find():
                return
            cursor = self.target.editor.textCursor()
        cursor.insertText(self.replacement.text())
        self.target.editor.setTextCursor(cursor)
        self._message("Replaced")

    def replace_all(self):
        term = self.query.text()
        if not term:
            self._message("Enter text")
            return
        document = self.target.editor.document()
        cursor = QTextCursor(document)
        count = 0
        cursor.beginEditBlock()
        while True:
            found = document.find(term, cursor, self._flags())
            if found.isNull():
                break
            found.insertText(self.replacement.text())
            cursor.setPosition(found.position())
            count += 1
        cursor.endEditBlock()
        self._message(f"{count} replaced" if count else "No matches")
