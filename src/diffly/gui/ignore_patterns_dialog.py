"""Editor for directory comparison ignore masks."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)


class IgnorePatternsDialog(QDialog):
    def __init__(self, patterns: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Directory ignore masks")
        self.resize(440, 350)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Ignore matching files and folders in both directories."))
        layout.addWidget(QLabel("Examples: *.log, node_modules, build/*"))
        self.patterns_list = QListWidget(self)
        self.patterns_list.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        layout.addWidget(self.patterns_list)
        for pattern in patterns:
            self._add_item(pattern)

        controls = QHBoxLayout()
        self.pattern_input = QLineEdit(self)
        self.pattern_input.setPlaceholderText("Mask, for example *.tmp")
        self.pattern_input.returnPressed.connect(self._add_pattern)
        controls.addWidget(self.pattern_input)
        add_button = QPushButton("Add", self)
        add_button.clicked.connect(self._add_pattern)
        controls.addWidget(add_button)
        edit_button = QPushButton("Edit", self)
        edit_button.clicked.connect(self._edit_selected)
        controls.addWidget(edit_button)
        remove_button = QPushButton("Remove", self)
        remove_button.clicked.connect(self._remove_selected)
        controls.addWidget(remove_button)
        layout.addLayout(controls)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, parent=self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_item(self, pattern: str):
        item = QListWidgetItem(pattern, self.patterns_list)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)

    def _add_pattern(self):
        pattern = self.pattern_input.text().strip()
        if pattern and pattern not in self.patterns():
            self._add_item(pattern)
        self.pattern_input.clear()

    def _remove_selected(self):
        for item in self.patterns_list.selectedItems():
            self.patterns_list.takeItem(self.patterns_list.row(item))

    def _edit_selected(self):
        item = self.patterns_list.currentItem()
        if item is not None:
            self.patterns_list.editItem(item)

    def patterns(self) -> list[str]:
        return list(dict.fromkeys(
            text for index in range(self.patterns_list.count())
            if (text := self.patterns_list.item(index).text().strip())
        ))
