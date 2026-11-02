"""Four-pane file merge and conflict resolution."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QTextCursor
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QScrollBar, QVBoxLayout, QWidget)

from diffly.core.diff.engine import CompareOptions
from diffly.core.merge.three_way import MergeResult
from diffly.gui.diff_gutter import BUTTON, draw_ribbon, line_edges
from diffly.gui.diff_overview import DiffOverview
from diffly.gui.editor_widget import EditorWidget, encoding_button
from diffly.gui.icons import toolbar_icon
from diffly.gui.scroll_sync import HorizontalScrollSync, follow_scroll, scroll_to_row
from diffly.gui.seamless_splitter import SeamlessSplitter
from diffly.gui.syntax_controller import SyntaxController
from diffly.services.file_service import file_stamp, read_text_file, status_encoding_text, write_file




# (pane background, overview/edge) theme keys, shared with the Files tab.
DIFF_KEYS = {
    "insert": ("diff_added", "overview_added"),
    "delete": ("diff_removed", "overview_removed"),
    "replace": ("diff_replace", "overview_replace"),
}


def _region_tag(record) -> str:
    if record.base_range[0] == record.base_range[1]:
        return "insert"
    if record.result_range[0] == record.result_range[1]:
        return "delete"
    return "replace"


def _conflict_keys(conflict) -> tuple[str, str]:
    return DIFF_KEYS["delete" if conflict.state.endswith("unresolved") else "insert"]


@dataclass
class _ConflictState:
    record: object
    start: int
    end: int
    state: str = "unresolved"


@dataclass
class _RegionState:
    record: object
    start: int
    end: int
    applied: bool = False


class _ConflictOverview(DiffOverview):
    """The Files overview, drawn from merge regions in Result line units."""

    def __init__(self, view):
        editor = view.result.editor
        super().__init__(editor, editor, editor.verticalScrollBar(), view)
        self.view = view
        self.setToolTip("Drag to scroll all three merge panes")

    def _total_lines(self) -> int:
        return max(1, self.left_editor.blockCount())

    def _markers(self):
        for region in self.view._regions:
            if region.record.conflict_id is not None:
                key = _conflict_keys(self.view._conflicts[region.record.conflict_id - 1])[1]
            else:
                key = DIFF_KEYS[_region_tag(region.record)][1]
            yield *self.view._result_lines(region), key


class _ApplyGutter(QWidget):
    def __init__(self, view, side: str):
        super().__init__(view)
        self.view = view
        self.side = side
        self.setFixedWidth(30)
        self.buttons: list[QPushButton] = []
        view.result.editor.verticalScrollBar().valueChanged.connect(self.update_position)
        source = view.sources[1 if side == "left" else 2]
        source.editor.verticalScrollBar().valueChanged.connect(self.update_position)
        for editor in (source.editor, view.result.editor):
            editor.updateRequest.connect(self._content_updated)

    def _content_updated(self, _rect, dy: int):
        if dy:
            self.update_position()

    @property
    def button(self):
        return self.buttons[0] if self.buttons else None

    def set_regions(self):
        for button in self.buttons:
            button.deleteLater()
        self.buttons = []
        for index, region in enumerate(self.view._regions):
            # Same 16px arrow, hover box and cursor as the Files gutter.
            button = QPushButton(self)
            button.setObjectName("MergeGutterButton")
            button.setFixedSize(BUTTON + 2, BUTTON + 2)
            button.setIconSize(QSize(BUTTON, BUTTON))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(f"Use {self.side.title()} for this change")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.installEventFilter(self.view)
            button.clicked.connect(lambda checked=False, row=index: self.view.use_region(row, self.side))
            self.buttons.append(button)
        self.update_icons()
        self.update_position()

    def update_icons(self):
        kind = "apply_right" if self.side == "left" else "apply_left"
        if not self.view.result.theme:
            return
        icon = toolbar_icon(kind, self.view.result.theme["text"])
        for button in self.buttons:
            button.setIcon(icon)

    def update_position(self, *_):
        for region, button in zip(self.view._regions, self.buttons):
            if not self.view.region_action_available(region, self.side):
                button.hide()
                continue
            source = self.view.sources[1 if self.side == "left" else 2]
            edges = line_edges(source.editor, *getattr(region.record, f"{self.side}_range"), self)
            y = None if edges is None else round(edges[0] + (source.editor.line_height() - button.height()) / 2)
            if y is None or y < 0 or y + button.height() > self.height():
                button.hide()
            else:
                button.move(2 if self.side == "left" else self.width() - button.width() - 2, y)
                button.show()
        self.update()

    def _result_edges(self, region) -> tuple[float, float] | None:
        first, end = self.view._result_lines(region)
        return line_edges(self.view.result.editor, first, first if region.start == region.end else end, self)

    def paintEvent(self, event):
        painter = QPainter(self)
        if not self.view.result.theme:
            return
        painter.fillRect(event.rect(), QColor(self.view.result.theme["surface"]))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        source = self.view.sources[1 if self.side == "left" else 2]
        width = self.width()
        for region in self.view._regions:
            if not self.view.region_action_available(region, self.side):
                continue
            source_edges = line_edges(source.editor, *getattr(region.record, f"{self.side}_range"), self)
            result_edges = self._result_edges(region)
            if source_edges is None or result_edges is None:
                continue
            source_top, source_bottom = source_edges
            result_top, result_bottom = result_edges
            if self.side == "left":
                left_top, left_bottom = source_top, source_bottom
                right_top, right_bottom = result_top, result_bottom
            else:
                left_top, left_bottom = result_top, result_bottom
                right_top, right_bottom = source_top, source_bottom
            if region.record.conflict_id is not None:
                color_key, edge_key = _conflict_keys(self.view._conflicts[region.record.conflict_id - 1])
            else:
                color_key, edge_key = DIFF_KEYS[_region_tag(region.record)]
            color = QColor(self.view.result.theme[color_key])
            edge = QColor(self.view.result.theme[edge_key])
            edge.setAlpha(170)
            draw_ribbon(painter, width, left_top, left_bottom, right_top, right_bottom, color, edge)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_position()

    def wheelEvent(self, event):
        QApplication.sendEvent(self.view.result.editor.verticalScrollBar(), event)
        event.accept()


class MergeView(QWidget):
    paths_changed = Signal()
    status_changed = Signal(str)
    state_changed = Signal()

    def __init__(self, scheduler, parent=None, *, line_numbers_visible=False,
                 syntax_highlighting_enabled=True, git_mergetool=False):
        super().__init__(parent)
        self.scheduler = scheduler
        self.git_mergetool = git_mergetool
        self._git_output: Path | None = None
        self._git_existing = None
        self._git_existing_prompted = False
        self._git_saved = False
        self.line_numbers_visible = line_numbers_visible
        self.syntax_highlighting_enabled = syntax_highlighting_enabled
        self.sources = tuple(EditorWidget(self) for _ in range(3))
        self.result = EditorWidget(self)
        self._paths: tuple[Path, Path, Path] | None = None
        self._conflicts: list[_ConflictState] = []
        self._regions: list[_RegionState] = []
        self._selected = -1
        self._serial = 0
        self._busy = False
        self._loading = False
        self._syncing_scroll = False
        self._navigated = False
        self._navigating = False
        self.compare_options = CompareOptions()
        self._history: list[tuple] = []
        self._history_index = -1
        self._choice = False
        self._loaded = None
        self._merged: MergeResult | None = None
        self.syntax = SyntaxController(scheduler, "merge", (*self.sources, self.result),
                                       syntax_highlighting_enabled, self)
        self._paint_timer = QTimer(self)
        self._paint_timer.setSingleShot(True)
        self._paint_timer.setInterval(300)
        self._paint_timer.timeout.connect(self._paint_conflicts)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        self.buttons = {}

        def add_button(key, text, callback, tooltip=None):
            button = QPushButton(text, self)
            button.clicked.connect(callback)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            if tooltip:
                button.setToolTip(tooltip)
            self.buttons[key] = button

        add_button("Previous conflict", "↑", lambda: self.navigate(-1), "Previous conflict (Shift+F7)")
        add_button("Next conflict", "↓", lambda: self.navigate(1), "Next conflict (F7)")
        add_button("Use Base", "Use Base", lambda: self.use("base"))
        add_button("Mark Resolved", "Mark Resolved", self.mark_resolved)
        self.buttons["Recreate Result"] = QAction("Recreate Merge Result", self)
        self.buttons["Recreate Result"].triggered.connect(self.recreate)
        self.buttons["Reload Sources"] = QAction("Reload Merge Sources", self)
        self.buttons["Reload Sources"].triggered.connect(self.reload_sources)
        add_button("Save Result", "Save Result", self.save)
        self.encoding_button = encoding_button(self, self._set_encoding)
        for key in ("Previous conflict", "Next conflict", "Use Base", "Mark Resolved", "Save Result"):
            self.buttons[key].hide()
        self.encoding_button.hide()

        def make_panel(title, editor):
            panel = QWidget(self)
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(0, 0, 0, 0)
            label = QLabel(title, panel)
            label.setObjectName("PaneTitle")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)
            return panel, layout, label

        base_panel, base_layout, base_title = make_panel("Base", self.sources[0])
        base_layout.addWidget(self.sources[0], 1)
        left_panel, left_layout, left_title = make_panel("Left", self.sources[1])
        left_layout.addWidget(self.sources[1], 1)
        center_panel, center_layout, self.result_title = make_panel("Result · Untitled", self.result)
        right_panel, right_layout, right_title = make_panel("Right", self.sources[2])
        self.titles = (base_title, left_title, right_title)
        for editor in (*self.sources, self.result):
            editor.editor.document().setDocumentMargin(4)
            editor.editor.set_line_numbers_visible(self.line_numbers_visible)
            editor.editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            editor.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            editor.editor.installEventFilter(self)
            editor.editor.viewport().installEventFilter(self)
        for editor in self.sources:
            editor.editor.setReadOnly(True)
        center_layout.addWidget(self.result, 1)
        right_row = QHBoxLayout()
        right_row.setContentsMargins(0, 0, 0, 0)
        right_row.setSpacing(0)
        right_row.addWidget(self.sources[2], 1)
        self.overview = _ConflictOverview(self)
        right_row.addWidget(self.overview)
        right_layout.addLayout(right_row, 1)
        top = SeamlessSplitter(Qt.Orientation.Horizontal, self)
        top.setHandleWidth(0)
        left_panel.setMinimumWidth(150)
        center_panel.setMinimumWidth(220)
        right_panel.setMinimumWidth(150)
        top.addWidget(left_panel)
        self.left_gutter = _ApplyGutter(self, "left")
        top.addWidget(self.left_gutter)
        top.addWidget(center_panel)
        self.right_gutter = _ApplyGutter(self, "right")
        top.addWidget(self.right_gutter)
        top.addWidget(right_panel)
        top.setSizes([290, 34, 400, 34, 290])
        base_panel.hide()
        outer.addWidget(top, 1)
        # One horizontal scrollbar for all visible panes, as in the Files tab.
        self.horizontal_scrollbar = QScrollBar(Qt.Orientation.Horizontal, self)
        outer.addWidget(self.horizontal_scrollbar)
        self.panes = (self.sources[1], self.result, self.sources[2])
        self.horizontal_sync = HorizontalScrollSync(self.panes, self.horizontal_scrollbar)
        self.footer = QLabel("0 conflicts · 0 unresolved")
        self.result.editor.document().contentsChange.connect(self._result_changed)
        for pane in self.panes:
            pane.editor.verticalScrollBar().valueChanged.connect(
                lambda value, source=pane: self._sync_scroll(source, value))
            pane.editor.cursorPositionChanged.connect(
                lambda source=pane: self._cursor_conflict_changed(source))
        self.result.edited.connect(self._update_status)
        self._update_status()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            operation = None
            if event.matches(QKeySequence.StandardKey.Undo):
                operation = self.result.editor.undo
            elif event.matches(QKeySequence.StandardKey.Redo):
                operation = self.result.editor.redo
            elif (event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
                  and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down)):
                # Same difference navigation keys as the Files tab.
                operation = lambda: self.navigate(-1 if event.key() == Qt.Key.Key_Up else 1)
            if operation is not None:
                if event.type() == QEvent.Type.KeyPress:
                    operation()
                event.accept()
                return True
        if event.type() == QEvent.Type.Wheel and self.horizontal_sync.handle_wheel(event):
            return True
        return super().eventFilter(watched, event)

    def _cursor_conflict_changed(self, pane: EditorWidget):
        # Select the conflict under the cursor so Use Left/Right act on it.
        # Moves outside any conflict keep the current selection.
        if self._loading or self._busy or self._choice or self._navigating:
            return
        cursor = pane.editor.textCursor()
        for index, conflict in enumerate(self._conflicts):
            if pane is self.result:
                inside = conflict.start <= cursor.position() <= conflict.end
            else:
                start, end = (conflict.record.left_range if pane is self.sources[1]
                              else conflict.record.right_range)
                inside = start <= cursor.blockNumber() < end
            if inside:
                if index != self._selected:
                    self._selected = index
                    self._navigated = True
                    self._update_status()
                    self._paint_conflicts()
                return

    @property
    def paths(self):
        return self._paths

    @property
    def busy(self):
        return self._busy

    def _sync_scroll(self, source: EditorWidget, value: int):
        if self._syncing_scroll or self._loading:
            return
        self._syncing_scroll = True
        try:
            follow_scroll(source, value, self.panes)
        finally:
            self._syncing_scroll = False

    def set_appearance(self, theme: dict, font, tab_width: int, guide_width: int):
        for editor in (*self.sources, self.result):
            editor.set_theme(theme)
            editor.set_font(font, tab_width, guide_width)
        self.overview.set_theme(theme)
        self.left_gutter.update_icons()
        self.right_gutter.update_icons()
        self._update_status()
        self._paint_conflicts()

    def set_line_numbers_visible(self, visible: bool):
        if self.line_numbers_visible == visible:
            return
        self.line_numbers_visible = visible
        for editor in (*self.sources, self.result):
            editor.editor.set_line_numbers_visible(visible)

    def set_syntax_highlighting_enabled(self, enabled: bool):
        if self.syntax_highlighting_enabled == enabled:
            return
        self.syntax_highlighting_enabled = enabled
        self.syntax.set_enabled(enabled)

    def confirm_discard(self) -> bool:
        if not self.result.state.dirty:
            return True
        return QMessageBox.question(self, "Unsaved result",
                                    "Discard unsaved changes to the merge result?") == QMessageBox.StandardButton.Yes

    def open_paths(self, base: Path | None, left: Path, right: Path,
                   output: Path | None = None, confirmed: bool = False,
                   encoding: str | None = None):
        paths = tuple(path.expanduser().absolute() if path is not None else None
                      for path in (base, left, right))
        if self._busy or (not confirmed and not self.confirm_discard()):
            return
        if output is not None:
            output = output.expanduser().absolute()
            if output.resolve() in {path.resolve() for path in paths if path is not None}:
                QMessageBox.warning(self, "Invalid output", "Result path must differ from all three source paths.")
                return
        self._serial += 1
        serial = self._serial
        self._busy = True
        self._git_output = output if self.git_mergetool else None
        self._git_existing = None
        self._git_existing_prompted = False
        self._git_saved = False
        read_output = bool(self.git_mergetool and output and output.exists())
        self._loaded = [None] * (4 if read_output else 3)
        self.status_changed.emit("Opening merge sources…")
        self._update_status()

        for index, path in enumerate(paths):
            def done(loaded, i=index):
                if serial != self._serial:
                    return
                self._loaded[i] = loaded
                if all(item is not None for item in self._loaded):
                    existing = self._loaded[3] if len(self._loaded) == 4 else None
                    self._install_sources(paths, output, self._loaded[:3], existing)

            def failed(message, i=index):
                if serial != self._serial:
                    return
                self._serial += 1
                self._busy = False
                self._update_status()
                failed_path = output if i == 3 else paths[i]
                QMessageBox.critical(self, "Open failed", f"{failed_path}: {message}")

            self.scheduler.submit_file(("merge-read", serial, index), read_text_file,
                                       (path, encoding), done, failed)
        if read_output:
            self.scheduler.submit_file(("merge-read", serial, 3), read_text_file,
                                       (output, encoding), lambda loaded: done(loaded, 3),
                                       lambda message: failed(message, 3))

    def _install_sources(self, paths, output, loaded, existing=None):
        self._loading = True
        try:
            for editor, path, item, label, title in zip(
                    self.sources, paths, loaded, self.titles, ("Base", "Left", "Right")):
                editor.load_text(item.text)
                editor.state.path = path
                editor.state.stamp = item.stamp
                label.setText(f"{title} · {path.name if path else '(empty)'}")
                label.setToolTip(str(path) if path else "No common base for this conflict")
                self.syntax.request(editor)
            self._paths = paths
            self._git_existing = existing
            self.result.state.path = output
            self.result.state.stamp = (existing.stamp if existing is not None else
                                       file_stamp(output) if output and output.exists() else None)
            self.result.state.encoding = loaded[1].encoding
            self.result.state.bom = loaded[1].bom
            self.result.state.newline = loaded[1].newline
            self.result.syntax_path = paths[1]
        finally:
            self._loading = False
        self.paths_changed.emit()
        self._submit_merge()

    def _submit_merge(self):
        self.scheduler.submit_merge(*(editor.text() for editor in self.sources),
                                    tuple(editor.state.revision for editor in self.sources),
                                    self.compare_options)

    def set_compare_options(self, options: CompareOptions):
        self.compare_options = options
        # Re-merging replaces the Result, so edited results wait for Recreate.
        if self._paths is not None and not self._busy and not self.result.state.dirty:
            self._busy = True
            self._submit_merge()

    def accept_merge(self, merged: MergeResult):
        if merged.revisions != tuple(editor.state.revision for editor in self.sources):
            return
        use_existing = False
        if (self.git_mergetool and not self._git_existing_prompted and
                self._git_existing is not None and self._git_existing.text != merged.text):
            self._git_existing_prompted = True
            choice = QMessageBox.question(
                self, "Existing Git merge result",
                "The working-tree result differs from Diffly's generated merge.\n\n"
                "Yes: open the existing file and keep its edits.\n"
                "No: start a fresh merge from Base, Local, and Remote.\n"
                "Cancel: leave the Git merge unresolved.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                self._busy = False
                window = self.window()
                window.git_merge_aborted = True
                QTimer.singleShot(0, window.close)
                return
            use_existing = choice == QMessageBox.StandardButton.Yes
        self._loading = True
        try:
            self.result.load_text(self._git_existing.text if use_existing else merged.text)
        finally:
            self._loading = False
        self._conflicts = []
        self._merged = None if use_existing else merged
        self.syntax.request(self.result)
        lines = merged.text.splitlines(keepends=True)
        for record in (() if use_existing else merged.conflicts):
            start = sum(len(line.encode("utf-16-le")) // 2 for line in lines[:record.result_range[0]])
            end = sum(len(line.encode("utf-16-le")) // 2 for line in lines[:record.result_range[1]])
            self._conflicts.append(_ConflictState(record, start, end))
        self._regions = []
        for record in (() if use_existing else merged.regions):
            start = sum(len(line.encode("utf-16-le")) // 2 for line in lines[:record.result_range[0]])
            end = sum(len(line.encode("utf-16-le")) // 2 for line in lines[:record.result_range[1]])
            self._regions.append(_RegionState(record, start, end))
        self._selected = 0 if self._conflicts else -1
        self._navigated = False
        self._busy = False
        self.left_gutter.set_regions()
        self.right_gutter.set_regions()
        self._history = []
        self._history_index = -1
        self._save_snapshot()
        self._git_saved = False
        self._update_status()
        self._paint_conflicts()
        self.status_changed.emit("")

    def merge_failed(self, message: str):
        self._busy = False
        self._update_status()
        QMessageBox.critical(self, "Merge failed", message)

    def _snapshot(self):
        return (self.result.text(),
                tuple((c.start, c.end, c.state) for c in self._conflicts),
                tuple((r.start, r.end, r.applied) for r in self._regions))

    def _save_snapshot(self):
        self._history = self._history[:self._history_index + 1]
        self._history.append(self._snapshot())
        self._history_index = len(self._history) - 1

    def _result_changed(self, position: int, removed: int, added: int):
        if self._loading or not (removed or added):
            return
        current = self.result.text()
        # Qt groups consecutive typing into one undo command, while
        # contentsChange records each character. Find the nearest matching
        # snapshot in either direction, not just the adjacent snapshot.
        candidates = (*range(self._history_index - 1, -1, -1),
                      *range(self._history_index + 1, len(self._history)))
        for index in candidates:
            if 0 <= index < len(self._history) and self._history[index][0] == current:
                self._history_index = index
                for conflict, (start, end, state) in zip(self._conflicts, self._history[index][1]):
                    conflict.start, conflict.end, conflict.state = start, end, state
                for region, (start, end, applied) in zip(self._regions, self._history[index][2]):
                    region.start, region.end, region.applied = start, end, applied
                self._update_status()
                self._paint_conflicts()
                return
        delta = added - removed
        change_end = position + removed
        for conflict in self._conflicts:
            if change_end < conflict.start or (change_end == conflict.start and removed):
                conflict.start += delta
                conflict.end += delta
            elif position > conflict.end or (position == conflict.end and not removed):
                continue
            else:
                conflict.end = max(conflict.start, conflict.end + delta)
                if not self._choice:
                    conflict.state = "edited, unresolved"
        for region in self._regions:
            if change_end < region.start or (change_end == region.start and removed):
                region.start += delta
                region.end += delta
            elif position > region.end or (position == region.end and not removed):
                continue
            else:
                region.end = max(region.start, region.end + delta)
                if not self._choice:
                    region.applied = False
        self._save_snapshot()
        self._update_status()
        if not self._choice:
            self._paint_timer.start()

    def _text_for_record(self, record, side: str) -> str:
        index = {"base": 0, "left": 1, "right": 2}[side]
        start, end = getattr(record, f"{side}_range")
        return "".join(self.sources[index].text().splitlines(keepends=True)[start:end])


    def _text_for(self, conflict: _ConflictState, side: str) -> str:
        return self._text_for_record(conflict.record, side)

    def region_action_available(self, region: _RegionState, side: str) -> bool:
        if region.record.conflict_id is not None:
            conflict = self._conflicts[region.record.conflict_id - 1]
            return conflict.state.endswith("unresolved")
        if region.applied:
            return False
        cursor = QTextCursor(self.result.editor.document())
        cursor.setPosition(min(region.start, self.result.editor.document().characterCount() - 1))
        cursor.setPosition(min(region.end, self.result.editor.document().characterCount() - 1),
                           QTextCursor.MoveMode.KeepAnchor)
        current = cursor.selectedText().replace("\u2029", "\n")
        return current != self._text_for_record(region.record, side)

    def use(self, side: str):
        if not (0 <= self._selected < len(self._conflicts)):
            return
        conflict = self._conflicts[self._selected]
        replacement = self._text_for(conflict, side)
        cursor = QTextCursor(self.result.editor.document())
        cursor.setPosition(conflict.start)
        cursor.setPosition(conflict.end, QTextCursor.MoveMode.KeepAnchor)
        self._choice = True
        conflict.state = f"resolved with {side.title()}"
        try:
            cursor.beginEditBlock()
            cursor.insertText(replacement)
            cursor.endEditBlock()
        finally:
            self._choice = False
        self._update_status()
        self._paint_conflicts()

    def use_region(self, index: int, side: str):
        if self._busy or not (0 <= index < len(self._regions)):
            return
        region = self._regions[index]
        if not self.region_action_available(region, side):
            return
        if region.record.conflict_id is not None:
            self._selected = region.record.conflict_id - 1
            self.use(side)
            return
        replacement = self._text_for_record(region.record, side)
        cursor = QTextCursor(self.result.editor.document())
        cursor.setPosition(region.start)
        cursor.setPosition(region.end, QTextCursor.MoveMode.KeepAnchor)
        self._choice = True
        region.applied = True
        try:
            cursor.beginEditBlock()
            cursor.insertText(replacement)
            cursor.endEditBlock()
        finally:
            self._choice = False
        self._update_status()
        self._paint_conflicts()

    def mark_resolved(self):
        if 0 <= self._selected < len(self._conflicts):
            conflict = self._conflicts[self._selected]
            if conflict.state == "edited, unresolved":
                conflict.state = "resolved manually"
                self._save_snapshot()
                self._update_status()
                self._paint_conflicts()

    def navigate(self, direction: int):
        if not self._conflicts:
            return
        # The first conflict is preselected on load; the first F7 shows it.
        if self._navigated or direction < 0:
            self._selected = (self._selected + direction) % len(self._conflicts)
        self._navigated = True
        conflict = self._conflicts[self._selected]
        self._navigating = True
        try:
            for editor, line_range in zip(self.sources, (conflict.record.base_range,
                                                          conflict.record.left_range,
                                                          conflict.record.right_range)):
                editor.select_hunk(*line_range)
            cursor = QTextCursor(self.result.editor.document())
            cursor.setPosition(conflict.start)
            self.result.editor.setTextCursor(cursor)
        finally:
            self._navigating = False
        # Place the conflict a third of the way down, like the Files tab.
        start_row = min(pane.editor.visual_row(line) for pane, line in zip(
            self.panes, (conflict.record.left_range[0], cursor.blockNumber(), conflict.record.right_range[0])))
        row = max(0, start_row - self.result.editor.verticalScrollBar().pageStep() // 3)
        self._syncing_scroll = True
        try:
            scroll_to_row(self.panes, row)
        finally:
            self._syncing_scroll = False
        self._update_status()
        self._paint_conflicts()

    def _paint_conflicts(self):
        self._paint_timer.stop()
        self._update_placeholders()
        # Colors follow the Files tab: replace = yellow, insert = green,
        # delete = red. Conflicts are red until resolved, then green.
        for side, (editor, field) in enumerate(zip(self.sources, ("base_range", "left_range", "right_range"))):
            selections = []
            if self._merged is not None and side in (1, 2):
                changes = self._merged.left_changes if side == 1 else self._merged.right_changes
                for start, end, tag in changes:
                    selections += editor.line_selections(start, end, DIFF_KEYS[tag][0])
            if side in (1, 2):
                side_name = "left" if side == 1 else "right"
                for region in self._regions:
                    if region.record.conflict_id is not None or not self.region_action_available(region, side_name):
                        continue
                    selections += editor.line_selections(*getattr(region.record, field),
                                                         DIFF_KEYS[_region_tag(region.record)][0])
            for conflict in self._conflicts:
                selections += editor.line_selections(*getattr(conflict.record, field),
                                                     _conflict_keys(conflict)[0])
            if self._merged is not None and side in (1, 2):
                selections += editor.span_selections(
                    self._merged.left_spans if side == 1 else self._merged.right_spans)
            editor.editor.setExtraSelections(selections)
            editor.editor.set_merge_conflict_ranges(tuple(
                (*getattr(conflict.record, field), conflict.state.endswith("unresolved"))
                for conflict in self._conflicts
            ))
        selections = []
        for region in self._regions:
            if region.record.conflict_id is None and region.start != region.end:
                selections += self.result.line_selections(*self._result_lines(region),
                                                          DIFF_KEYS[_region_tag(region.record)][0])
        for conflict in self._conflicts:
            selections += self.result.line_selections(*self._result_lines(conflict),
                                                      _conflict_keys(conflict)[0])
        self.result.editor.setExtraSelections(selections)
        self.result.editor.set_merge_conflict_ranges(tuple(
            (*self._result_lines(conflict), conflict.state.endswith("unresolved"))
            for conflict in self._conflicts
        ))
        self.overview.update()
        self.left_gutter.update_position()
        self.right_gutter.update_position()

    def _result_lines(self, item) -> tuple[int, int]:
        """Result line range [first, end) for a conflict or region."""
        document = self.result.editor.document()
        last = document.characterCount() - 1
        first = document.findBlock(min(item.start, last)).blockNumber()
        return first, document.findBlock(min(max(item.start, item.end - 1), last)).blockNumber() + 1

    def _update_placeholders(self):
        """Align the next shared line after regions with different line counts."""
        gaps: dict[int, list[tuple[int, int]]] = {1: [], 2: [], 3: []}
        document = self.result.editor.document()
        for region in self._regions:
            cursor = QTextCursor(document)
            cursor.setPosition(min(region.start, document.characterCount() - 1))
            result_start = cursor.blockNumber()
            cursor.setPosition(min(region.end, document.characterCount() - 1),
                               QTextCursor.MoveMode.KeepAnchor)
            result_count = len(cursor.selectedText().replace("\u2029", "\n").splitlines(keepends=True))
            left_start, left_end = region.record.left_range
            right_start, right_end = region.record.right_range
            left_count = left_end - left_start
            right_count = right_end - right_start
            height = max(left_count, result_count, right_count)
            if left_count < height:
                gaps[1].append((left_end, height - left_count))
            if right_count < height:
                gaps[2].append((right_end, height - right_count))
            if result_count < height:
                gaps[3].append((result_start + result_count, height - result_count))
        for index, editor in ((1, self.sources[1]), (2, self.sources[2]), (3, self.result)):
            ranges = tuple(gaps[index])
            if editor.editor.placeholder_ranges != ranges:
                editor.editor.set_placeholder_ranges(ranges)

    def _update_status(self):
        count = len(self._conflicts)
        unresolved = sum(c.state.endswith("unresolved") for c in self._conflicts)
        selected = self._conflicts[self._selected] if 0 <= self._selected < count else None
        theme = self.result.theme
        self.footer.setText(
            f'<span style="color:{theme.get("overview_removed", "")}">●</span> '
            f'{count} conflict{"s" if count != 1 else ""}  '
            f'<span style="color:{theme.get("overview_replace", "")}">●</span> {unresolved} unresolved'
            + (f"  ·  conflict {self._selected + 1}: {selected.state}" if selected else "")
        )
        path = self.result.state.path
        dirty = " •" if self.result.state.dirty else ""
        self.result_title.setText(f"Result · {path.name if path else 'Untitled'}{dirty}")
        self.result_title.setToolTip(str(path) if path else "Result has not been saved")
        self.encoding_button.setText(status_encoding_text(
            self.result.state.encoding, self.result.state.bom, self.result.state.newline))
        self.encoding_button.setToolTip(
            f"Result encoding: {self.result.state.encoding}; "
            f"newline: {repr(self.result.state.newline)}; "
            f"{'with BOM' if self.result.state.bom else 'without BOM'}"
        )
        for title in ("Previous conflict", "Next conflict"):
            self.buttons[title].setEnabled(count > 0 and not self._busy)
        self.buttons["Use Base"].setEnabled(selected is not None and not self._busy)
        for gutter in (self.left_gutter, self.right_gutter):
            for button in gutter.buttons:
                button.setEnabled(not self._busy)
        self.buttons["Mark Resolved"].setEnabled(selected is not None and
                                                  selected.state == "edited, unresolved")
        self.buttons["Recreate Result"].setEnabled(self._paths is not None and not self._busy)
        self.buttons["Reload Sources"].setEnabled(self._paths is not None and not self._busy)
        self.buttons["Save Result"].setEnabled(self._paths is not None and not self._busy)
        self.encoding_button.setEnabled(self._paths is not None and not self._busy)
        self.state_changed.emit()

    def _set_encoding(self, encoding: str | None):
        # Re-decode the sources like the Files tab; the Result is saved with it.
        if self._paths is None or self._busy or not self.confirm_discard():
            return
        self.open_paths(*self._paths, self.result.state.path, confirmed=True, encoding=encoding)

    def reload_sources(self):
        if self._paths is None or self._busy or not self.confirm_discard():
            return
        self.open_paths(*self._paths, self.result.state.path, confirmed=True)

    def recreate(self):
        if self._paths is None or self._busy:
            return
        if self.result.state.dirty and not self.confirm_discard():
            return
        self._busy = True
        self._submit_merge()

    def save(self, save_as: bool = False):
        if self._paths is None or self._busy:
            return
        path = self._git_output if self.git_mergetool else self.result.state.path
        if self.git_mergetool:
            save_as = False
        if path is None or save_as:
            left = self._paths[1]
            default = left.with_name(f"{left.stem}.merged{left.suffix}")
            filename, _ = QFileDialog.getSaveFileName(self, "Save merge result", str(path or default))
            if not filename:
                return
            path = Path(filename).expanduser().absolute()
        if path.resolve() in {source.resolve() for source in self._paths if source is not None}:
            QMessageBox.warning(self, "Invalid output", "Result path must differ from all three source paths.")
            return
        unresolved = sum(c.state.endswith("unresolved") for c in self._conflicts)
        if unresolved and QMessageBox.question(self, "Unresolved conflicts",
                                               f"Save with {unresolved} unresolved conflict(s)? "
                                               "The Result contains your current text for those conflicts.") != QMessageBox.StandardButton.Yes:
            return
        if path == self.result.state.path:
            expected = self.result.state.stamp
        elif path.exists():
            if QMessageBox.question(self, "Overwrite file", f"Replace {path.name}?") != QMessageBox.StandardButton.Yes:
                return
            expected = file_stamp(path)
        else:
            expected = None
        revision = self.result.state.revision
        self._busy = True
        self._update_status()

        def done(stamp):
            self._busy = False
            self.result.state.path = path
            self.result.state.stamp = stamp
            self.result.state.saved_revision = revision
            self._git_saved = bool(self.git_mergetool and path == self._git_output)
            self._update_status()
            self.paths_changed.emit()
            self.status_changed.emit(f"Saved {path.name}")

        def failed(message):
            self._busy = False
            self._update_status()
            QMessageBox.critical(self, "Save failed", message)

        self.scheduler.submit_file(("merge-save", id(self)), write_file,
                                   (path, self.result.text(), self.result.state.newline,
                                    self.result.state.bom, expected, self.result.state.encoding),
                                   done, failed)

    def git_merge_complete(self) -> bool:
        if (not self.git_mergetool or not self._git_saved or self._busy or self._git_output is None or
                self.result.state.path != self._git_output or self.result.state.dirty or
                any(conflict.state.endswith("unresolved") for conflict in self._conflicts)):
            return False
        if any(line in ("<<<<<<<", ">>>>>>>") or line.startswith(("<<<<<<< ", ">>>>>>> "))
               for line in self.result.text().splitlines()):
            return False
        try:
            return file_stamp(self._git_output) == self.result.state.stamp
        except OSError:
            return False
