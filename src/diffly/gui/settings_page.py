"""One place for appearance, comparison, and directory filtering preferences."""

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QIntValidator, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QSizePolicy, QStyleFactory, QVBoxLayout, QWidget,
)

from diffly.core.diff.engine import CompareOptions
from diffly.core.directories.filters import DEFAULT_PRESETS, PRESETS


class SettingsPage(QWidget):
    theme_changed = Signal(str)
    font_size_changed = Signal(int)
    indent_width_changed = Signal(int)
    comparison_changed = Signal(object)
    filters_changed = Signal(object, bool, object)

    def __init__(self, settings: dict, themes: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        content = QWidget(scroll)
        content.setObjectName("SettingsContent")
        scroll.setWidget(content)
        center_row = QHBoxLayout(content)
        center_row.setContentsMargins(24, 24, 24, 32)
        center_row.addStretch(1)
        column = QWidget(content)
        column.setMaximumWidth(640)
        column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        center_row.addWidget(column, 100)
        center_row.addStretch(1)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        title = QLabel("Settings", content)
        title.setObjectName("SettingsTitle")
        layout.addWidget(title)
        subtitle = QLabel("Changes apply immediately and are saved for future launches.", content)
        subtitle.setObjectName("SettingsHint")
        subtitle.setContentsMargins(0, 4, 0, 18)
        layout.addWidget(subtitle)

        appearance, appearance_layout = self._section(layout, "Appearance and editor")
        self.theme_choice = QComboBox(appearance)
        self.theme_choice.setStyle(QStyleFactory.create("Fusion"))
        for key, theme in themes.items():
            self.theme_choice.addItem(theme.get("name", key), key)
        self.theme_choice.setCurrentIndex(max(0, self.theme_choice.findData(settings.get("theme", "gruvbox"))))
        self._setting_row(appearance_layout, "Theme", self.theme_choice)
        self._committed_font_size = max(8, min(32, int(settings.get("font_size", 14))))
        self.font_size = QLineEdit(str(self._committed_font_size), appearance)
        self.font_size.setValidator(QIntValidator(8, 32, self.font_size))
        self.font_size.setToolTip("Editor font size in points (8–32)")
        self._setting_row(appearance_layout, "Editor font size (pt)", self.font_size)
        self.indent_width = QComboBox(appearance)
        self.indent_width.setStyle(QStyleFactory.create("Fusion"))
        self.indent_width.addItem("2 spaces", 2)
        self.indent_width.addItem("4 spaces", 4)
        self.indent_width.setCurrentIndex(0 if int(settings.get("tab_width", 4)) == 2 else 1)
        self._setting_row(appearance_layout, "Tab stops and guides", self.indent_width)

        comparison, comparison_layout = self._section(layout, "File comparison")
        self.compare_checks = {}
        for key, label in (
            ("ignore_trailing_whitespace", "Ignore trailing spaces and tabs"),
            ("ignore_whitespace", "Ignore all spaces and tabs"),
            ("ignore_case", "Ignore letter case"),
        ):
            check = QCheckBox(label, comparison)
            check.setChecked(bool(settings.get(key, False)))
            check.setMinimumHeight(30)
            comparison_layout.addWidget(check)
            self.compare_checks[key] = check
        note = QLabel("These rules change highlighting. Copying keeps the original text.", comparison)
        note.setObjectName("SettingsHint")
        note.setWordWrap(True)
        note.setContentsMargins(0, 6, 0, 0)
        comparison_layout.addWidget(note)

        directory, directory_layout = self._section(layout, "Directory filtering")
        self.gitignore = QCheckBox("Use .gitignore from selected and parent folders", directory)
        self.gitignore.setChecked(bool(settings.get("use_gitignore", True)))
        self.gitignore.setMinimumHeight(30)
        directory_layout.addWidget(self.gitignore)
        presets_title = QLabel("Built-in filters", directory)
        presets_title.setObjectName("SettingsSubheading")
        directory_layout.addWidget(presets_title)
        enabled = set(settings.get("ignore_presets", DEFAULT_PRESETS))
        self.preset_checks = {}
        for key, (label, patterns) in PRESETS.items():
            check = QCheckBox(label, directory)
            check.setChecked(key in enabled)
            check.setToolTip(", ".join(patterns))
            check.setMinimumHeight(28)
            directory_layout.addWidget(check)
            self.preset_checks[key] = check
        masks_title = QLabel("Custom masks", directory)
        masks_title.setObjectName("SettingsSubheading")
        directory_layout.addWidget(masks_title)
        self.patterns_list = QListWidget(directory)
        self.patterns_list.setFixedHeight(92)
        self.patterns_list.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        for pattern in settings.get("ignore_patterns", []):
            self._add_pattern_item(pattern)
        directory_layout.addWidget(self.patterns_list)
        mask_row = QHBoxLayout()
        self.pattern_input = QLineEdit(directory)
        self.pattern_input.setPlaceholderText("Mask, for example *.log or node_modules")
        mask_row.addWidget(self.pattern_input, 1)
        add_button = QPushButton("Add", directory)
        add_button.clicked.connect(self._add_pattern)
        mask_row.addWidget(add_button)
        remove_button = QPushButton("Remove selected", directory)
        remove_button.clicked.connect(self._remove_selected)
        mask_row.addWidget(remove_button)
        directory_layout.addLayout(mask_row)
        hint = QLabel("Double-click a mask to edit it. Masks apply to both directories.", directory)
        hint.setObjectName("SettingsHint")
        directory_layout.addWidget(hint)
        layout.addStretch(1)

        self.theme_choice.currentIndexChanged.connect(lambda: self.theme_changed.emit(self.theme_choice.currentData()))
        self.font_size.editingFinished.connect(self._commit_font_size)
        self.indent_width.currentIndexChanged.connect(lambda: self.indent_width_changed.emit(self.indent_width.currentData()))
        for check in self.compare_checks.values():
            check.toggled.connect(self._emit_comparison)
        self.gitignore.toggled.connect(self._emit_filters)
        for check in self.preset_checks.values():
            check.toggled.connect(self._emit_filters)
        self.patterns_list.itemChanged.connect(self._mask_edited)
        self.pattern_input.returnPressed.connect(self._add_pattern)

    @staticmethod
    def _section(layout: QVBoxLayout, title: str) -> tuple[QWidget, QVBoxLayout]:
        heading = QLabel(title)
        heading.setObjectName("SettingsSectionTitle")
        heading.setContentsMargins(0, 10, 0, 8)
        layout.addWidget(heading)
        card = QWidget()
        card.setObjectName("SettingsCard")
        body = QVBoxLayout(card)
        body.setContentsMargins(18, 12, 18, 12)
        body.setSpacing(4)
        layout.addWidget(card)
        return card, body

    @staticmethod
    def _setting_row(layout: QVBoxLayout, title: str, control: QWidget) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 2)
        label = QLabel(title)
        label.setObjectName("SettingsRowLabel")
        row.addWidget(label)
        row.addStretch(1)
        control.setFixedWidth(220)
        row.addWidget(control)
        layout.addLayout(row)
        line = QFrame()
        line.setObjectName("SettingsDivider")
        line.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(line)

    def comparison_options(self) -> CompareOptions:
        return CompareOptions(**{key: check.isChecked() for key, check in self.compare_checks.items()})

    def set_comparison_options(self, options: CompareOptions) -> None:
        for key, check in self.compare_checks.items():
            with QSignalBlocker(check):
                check.setChecked(getattr(options, key))

    def set_indent_width(self, width: int) -> None:
        with QSignalBlocker(self.indent_width):
            self.indent_width.setCurrentIndex(0 if width == 2 else 1)

    def align_control_heights(self) -> None:
        controls = (self.theme_choice, self.font_size, self.indent_width)
        height = max(control.sizeHint().height() for control in controls)
        for control in controls:
            control.setFixedHeight(height)

    def set_choice_theme(self, theme: dict) -> None:
        palette = QPalette()
        for role, color in (
            (QPalette.ColorRole.Button, "surface"),
            (QPalette.ColorRole.Base, "background"),
            (QPalette.ColorRole.Window, "surface"),
            (QPalette.ColorRole.Text, "text"),
            (QPalette.ColorRole.ButtonText, "text"),
            (QPalette.ColorRole.WindowText, "text"),
            (QPalette.ColorRole.Highlight, "selection"),
            (QPalette.ColorRole.HighlightedText, "text"),
        ):
            palette.setColor(role, QColor(theme[color]))
        for control in (self.theme_choice, self.indent_width):
            control.setPalette(palette)

    def _commit_font_size(self) -> None:
        text = self.font_size.text().strip()
        size = int(text) if text.isdecimal() else None
        if size is None or not 8 <= size <= 32:
            self.font_size.setText(str(self._committed_font_size))
            return
        self.font_size.setText(str(size))
        if size != self._committed_font_size:
            self._committed_font_size = size
            self.font_size_changed.emit(size)

    def patterns(self) -> list[str]:
        return list(dict.fromkeys(
            text for index in range(self.patterns_list.count())
            if (text := self.patterns_list.item(index).text().strip())
        ))

    def enabled_presets(self) -> tuple[str, ...]:
        return tuple(key for key, check in self.preset_checks.items() if check.isChecked())

    def _emit_comparison(self):
        self.comparison_changed.emit(self.comparison_options())

    def _emit_filters(self):
        self.filters_changed.emit(self.patterns(), self.gitignore.isChecked(), self.enabled_presets())

    def _add_pattern_item(self, pattern: str):
        item = QListWidgetItem(pattern, self.patterns_list)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)

    def _add_pattern(self):
        pattern = self.pattern_input.text().strip()
        if pattern and pattern not in self.patterns():
            self._add_pattern_item(pattern)
            self._emit_filters()
        self.pattern_input.clear()

    def _remove_selected(self):
        selected = self.patterns_list.selectedItems()
        if not selected:
            return
        for item in selected:
            self.patterns_list.takeItem(self.patterns_list.row(item))
        self._emit_filters()

    def _mask_edited(self, item):
        text = item.text().strip()
        if not text or sum(self.patterns_list.item(i).text().strip() == text
                           for i in range(self.patterns_list.count())) > 1:
            self.patterns_list.takeItem(self.patterns_list.row(item))
        self._emit_filters()
