"""Two synchronized trees for comparing directory contents."""

from pathlib import Path
from threading import Event

from PySide6.QtCore import QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QSplitter, QStyle, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from diffly.core.directories.compare import DirectoryEntry, compare_directories
from diffly.core.directories.filters import DEFAULT_PRESETS


STATUS_LABELS = {
    "same": "Same", "different": "Different", "left_only": "Left only",
    "right_only": "Right only", "type_changed": "Type changed",
}


class DirectoryComparisonView(QWidget):
    comparison_ready = Signal(str, str)
    stats_changed = Signal(object)
    message_changed = Signal(str)
    open_files = Signal(object, object)
    selection_changed = Signal()

    def __init__(self, scheduler, patterns: list[str], parent=None, use_gitignore: bool = True,
                 enabled_presets: tuple[str, ...] = DEFAULT_PRESETS):
        super().__init__(parent)
        self.scheduler = scheduler
        self.patterns = list(patterns)
        self.use_gitignore = use_gitignore
        self.enabled_presets = enabled_presets
        self._generation = 0
        self._cancel: Event | None = None
        self._entries: dict[str, DirectoryEntry] = {}
        self._items: dict[str, tuple[QTreeWidgetItem, QTreeWidgetItem]] = {}
        self._theme: dict = {}
        self._selected_path: str | None = None
        self._navigable_paths: list[str] = []
        self._syncing_selection = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)
        self.left_path = QLineEdit(self)
        self.right_path = QLineEdit(self)
        self.message = "Choose two directories to compare"

        self.left_tree = self._make_tree("Left")
        self.right_tree = self._make_tree("Right")
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.left_pane = self._make_pane(self.left_path, self.left_tree, "left")
        self.right_pane = self._make_pane(self.right_path, self.right_tree, "right")
        splitter.addWidget(self.left_pane)
        splitter.addWidget(self.right_pane)
        splitter.setSizes([1, 1])
        layout.addWidget(splitter, 1)
        self.left_tree.itemExpanded.connect(lambda item: self._mirror_expansion(item, self.right_tree, True))
        self.right_tree.itemExpanded.connect(lambda item: self._mirror_expansion(item, self.left_tree, True))
        self.left_tree.itemCollapsed.connect(lambda item: self._mirror_expansion(item, self.right_tree, False))
        self.right_tree.itemCollapsed.connect(lambda item: self._mirror_expansion(item, self.left_tree, False))
        self.left_tree.verticalScrollBar().valueChanged.connect(self.right_tree.verticalScrollBar().setValue)
        self.right_tree.verticalScrollBar().valueChanged.connect(self.left_tree.verticalScrollBar().setValue)
        self.left_tree.itemDoubleClicked.connect(self._open_item)
        self.right_tree.itemDoubleClicked.connect(self._open_item)
        self.left_tree.currentItemChanged.connect(lambda item, previous: self._select_item(item, self.left_tree))
        self.right_tree.currentItemChanged.connect(lambda item, previous: self._select_item(item, self.right_tree))

    def _make_pane(self, field: QLineEdit, tree: QTreeWidget, side: str) -> QWidget:
        pane = QWidget(self)
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.setSpacing(5)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        field.setPlaceholderText(f"{side.title()} directory…")
        field.setToolTip(f"Path to the {side} directory")
        field.textChanged.connect(self._paths_edited)
        field.returnPressed.connect(self.compare)
        row.addWidget(field, 1)
        browse = QToolButton(pane)
        browse.setObjectName("DirectoryBrowseButton")
        browse.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        browse.setIconSize(QSize(18, 18))
        browse.setToolTip(f"Choose {side} directory")
        browse.setAccessibleName(f"Choose {side} directory")
        browse.setFixedSize(32, 30)
        browse.clicked.connect(lambda checked=False, target=field: self._browse(target))
        row.addWidget(browse)
        pane_layout.addLayout(row)
        pane_layout.addWidget(tree, 1)
        return pane

    def _make_tree(self, side: str) -> QTreeWidget:
        tree = QTreeWidget(self)
        tree.setObjectName(f"{side}DirectoryTree")
        tree.setHeaderLabels((side, "Result"))
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.setColumnWidth(0, 230)
        return tree

    def _browse(self, field: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose directory", field.text() or str(Path.home()))
        if path:
            field.setText(path)
            if self.left_path.text().strip() and self.right_path.text().strip():
                self.compare()

    def _paths_edited(self, *_):
        self.cancel()
        self.left_tree.clear()
        self.right_tree.clear()
        self._items.clear()
        self._entries.clear()
        self._navigable_paths.clear()
        self._selected_path = None
        self.selection_changed.emit()
        self.stats_changed.emit(None)
        self._set_message("Press Enter in a directory path or use Refresh to compare")

    def _set_message(self, message: str):
        self.message = message
        self.message_changed.emit(message)

    def set_patterns(self, patterns: list[str]):
        self.set_filters(patterns, self.use_gitignore, self.enabled_presets)

    def set_gitignore_enabled(self, enabled: bool):
        self.set_filters(self.patterns, enabled, self.enabled_presets)

    def set_filters(self, patterns: list[str], use_gitignore: bool,
                    enabled_presets: tuple[str, ...] = DEFAULT_PRESETS):
        if (self.patterns == list(patterns) and self.use_gitignore == use_gitignore
                and self.enabled_presets == enabled_presets):
            return
        self.patterns = list(patterns)
        self.use_gitignore = use_gitignore
        self.enabled_presets = enabled_presets
        if self.left_path.text().strip() and self.right_path.text().strip():
            self.compare()

    def set_theme(self, theme: dict):
        self._theme = theme
        if self._entries:
            self._color_items()

    def cancel(self):
        self._generation += 1
        if self._cancel is not None:
            self._cancel.set()
            self._cancel = None

    def compare(self):
        left_text = self.left_path.text().strip()
        right_text = self.right_path.text().strip()
        if not left_text or not right_text:
            self._set_message("Choose both directories first")
            return
        previous_selection = self._selected_path
        self.cancel()
        generation = self._generation
        cancel = Event()
        self._cancel = cancel
        self.left_tree.clear()
        self.right_tree.clear()
        self._items.clear()
        self._entries.clear()
        self._navigable_paths.clear()
        self._selected_path = None
        self.selection_changed.emit()
        self.stats_changed.emit(None)
        self._set_message("Comparing directories…")

        def done(entries):
            if generation != self._generation:
                return
            self._cancel = None
            self._populate(entries, previous_selection)
            self.comparison_ready.emit(left_text, right_text)

        def failed(message):
            if generation != self._generation:
                return
            self._cancel = None
            self._set_message(f"Comparison failed: {message}")

        self.scheduler.submit_file(
            ("directory", generation), compare_directories,
            (Path(left_text), Path(right_text), tuple(self.patterns), cancel,
             self.use_gitignore, self.enabled_presets), done, failed,
        )

    def _populate(self, entries: tuple[DirectoryEntry, ...], previous_selection: str | None = None):
        self.left_tree.clear()
        self.right_tree.clear()
        self._entries = {entry.relative_path: entry for entry in entries}
        self._navigable_paths = [entry.relative_path for entry in entries if entry.status != "same" and
                                 "file" in (entry.left_kind, entry.right_kind)]
        self._items.clear()
        style = QApplication.style()
        for entry in entries:
            relative = entry.relative_path
            parent = relative.rsplit("/", 1)[0] if "/" in relative else None
            name = relative.rsplit("/", 1)[-1]
            pair = []
            for side, tree in (("left", self.left_tree), ("right", self.right_tree)):
                exists = getattr(entry, f"{side}_path") is not None
                item = QTreeWidgetItem([name if exists else f"{name}  (missing)", STATUS_LABELS[entry.status]])
                item.setData(0, Qt.ItemDataRole.UserRole, relative)
                item.setToolTip(0, relative)
                side_kind = getattr(entry, f"{side}_kind")
                if side_kind in ("directory", "file"):
                    icon = style.standardIcon(
                        QStyle.StandardPixmap.SP_DirIcon if side_kind == "directory" else QStyle.StandardPixmap.SP_FileIcon
                    )
                    item.setIcon(0, icon)
                if parent is None:
                    tree.addTopLevelItem(item)
                else:
                    parent_pair = self._items[parent]
                    parent_pair[0 if side == "left" else 1].addChild(item)
                pair.append(item)
            self._items[relative] = (pair[0], pair[1])
        self._color_items()
        for tree in (self.left_tree, self.right_tree):
            tree.expandToDepth(0)
        counts = {status: 0 for status in STATUS_LABELS}
        changed_folders = 0
        for entry in entries:
            if entry.kind == "directory" and entry.status != "type_changed":
                if entry.status != "same":
                    changed_folders += 1
            else:
                counts[entry.status] += 1
        self.stats_changed.emit({"different": counts["different"] + counts["type_changed"],
                                 "left_only": counts["left_only"],
                                 "right_only": counts["right_only"],
                                 "same": counts["same"],
                                 "changed_folders": changed_folders})
        self._set_message("Double-click a file present on both sides to compare it")
        chosen = previous_selection if previous_selection in self._items else (
            self._navigable_paths[0] if self._navigable_paths else None
        )
        self.select_path(chosen)

    def _select_item(self, item: QTreeWidgetItem | None, source: QTreeWidget):
        if self._syncing_selection:
            return
        relative = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        self._selected_path = relative
        pair = self._items.get(relative)
        if pair is not None:
            other = self.right_tree if source is self.left_tree else self.left_tree
            other_item = pair[1] if source is self.left_tree else pair[0]
            self._syncing_selection = True
            try:
                other.setCurrentItem(other_item)
            finally:
                self._syncing_selection = False
        self.selection_changed.emit()

    def select_path(self, relative: str | None):
        pair = self._items.get(relative)
        if pair is None:
            self._selected_path = None
            self.selection_changed.emit()
            return
        item = pair[0]
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self.left_tree.setCurrentItem(item)
        self.left_tree.scrollToItem(item)
        self.right_tree.scrollToItem(pair[1])

    def selected_entry(self) -> DirectoryEntry | None:
        return self._entries.get(self._selected_path)

    def can_copy(self, to_left: bool) -> bool:
        entry = self.selected_entry()
        if entry is None or entry.status == "same":
            return False
        source_kind = entry.right_kind if to_left else entry.left_kind
        target_kind = entry.left_kind if to_left else entry.right_kind
        return ((source_kind == "file" and target_kind in (None, "file"))
                or (source_kind == "directory" and target_kind in (None, "directory", "file")))

    def has_navigable_files(self) -> bool:
        return bool(self._navigable_paths)

    def navigate(self, step: int):
        if not self._navigable_paths:
            return
        try:
            current = self._navigable_paths.index(self._selected_path)
        except ValueError:
            current = -1 if step > 0 else 0
        self.select_path(self._navigable_paths[(current + step) % len(self._navigable_paths)])

    def _color_items(self):
        colors = {
            "different": self._theme.get("diff_replace", "#554a35"),
            "type_changed": self._theme.get("diff_replace", "#554a35"),
            "left_only": self._theme.get("diff_removed", "#593a35"),
            "right_only": self._theme.get("diff_added", "#344b3b"),
        }
        muted = QColor(self._theme.get("muted", "#888888"))
        for relative, pair in self._items.items():
            entry = self._entries[relative]
            for side, item in zip(("left", "right"), pair):
                if entry.status in colors:
                    brush = QBrush(QColor(colors[entry.status]))
                    item.setBackground(0, brush)
                    item.setBackground(1, brush)
                if getattr(entry, f"{side}_path") is None:
                    item.setForeground(0, QBrush(muted))

    def _mirror_expansion(self, item: QTreeWidgetItem, other: QTreeWidget, expanded: bool):
        relative = item.data(0, Qt.ItemDataRole.UserRole)
        pair = self._items.get(relative)
        if pair is None:
            return
        other_item = pair[0] if other is self.left_tree else pair[1]
        with QSignalBlocker(other):
            other_item.setExpanded(expanded)

    def _open_item(self, item: QTreeWidgetItem, _column: int):
        entry = self._entries.get(item.data(0, Qt.ItemDataRole.UserRole))
        if entry is not None and entry.left_kind == entry.right_kind == "file":
            self.open_files.emit(entry.left_path, entry.right_path)
