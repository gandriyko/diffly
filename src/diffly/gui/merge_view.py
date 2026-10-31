"""Four-pane file merge and conflict resolution."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QPen, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QTextEdit, QToolButton,
                               QVBoxLayout, QWidget, QMenu)

from diffly.core.documents.syntax import language_for
from diffly.core.merge.three_way import MergeResult
from diffly.gui.diff_gutter import draw_ribbon
from diffly.gui.editor_widget import EditorWidget
from diffly.gui.icons import toolbar_icon
from diffly.gui.seamless_splitter import SeamlessSplitter
from diffly.services.file_service import file_stamp, read_merge_file, write_file


def _merge_language(path: Path | None, text: str) -> str | None:
    sample = text[:512]
    language = language_for(path, sample)
    if language is None and path is not None and path.suffix.lower() == ".txt":
        language = language_for(None, sample)
    return language


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


class _ConflictOverview(QWidget):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self._drag_offset = None
        self.setFixedWidth(18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Drag to scroll all three merge panes")
        scrollbar = view.result.editor.verticalScrollBar()
        scrollbar.valueChanged.connect(self.update)
        scrollbar.rangeChanged.connect(self.update)

    def _viewport_rect(self) -> tuple[int, int]:
        scrollbar = self.view.result.editor.verticalScrollBar()
        total = max(1, scrollbar.maximum() + scrollbar.pageStep())
        top = round(self.height() * scrollbar.value() / total)
        size = max(12, round(self.height() * scrollbar.pageStep() / total))
        return top, min(self.height() - top, size)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self.view.result.theme.get("surface", "#333333")))
        painter.fillRect(4, 0, self.width() - 8, self.height(),
                         QColor(self.view.result.theme.get("background", "#282828")))
        height = max(1, self.height())
        count = max(1, self.view.result.editor.document().blockCount())
        for region in self.view._regions:
            cursor = QTextCursor(self.view.result.editor.document())
            cursor.setPosition(min(region.start, self.view.result.editor.document().characterCount() - 1))
            y = round(cursor.blockNumber() / count * height)
            conflict = (self.view._conflicts[region.record.conflict_id - 1]
                        if region.record.conflict_id is not None else None)
            if conflict is None:
                color = QColor(self.view.result.theme.get("overview_added", "#63a879"))
            else:
                color = QColor(self.view.result.theme.get(
                    "overview_removed" if conflict.state.endswith("unresolved") else "overview_added", "#d05b55"))
            painter.fillRect(2, min(y, height - 5), 14 if conflict and
                             region.record.conflict_id - 1 == self.view._selected else 10, 5, color)
        scrollbar = self.view.result.editor.verticalScrollBar()
        if scrollbar.maximum() > 0:
            top, size = self._viewport_rect()
            color = QColor(self.view.result.theme.get("accent", "#fabd2f"))
            color.setAlpha(190)
            painter.setPen(QPen(color, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(3, top, self.width() - 6, max(1, size - 1))

    def _scroll_to(self, y: float):
        scrollbar = self.view.result.editor.verticalScrollBar()
        total = max(1, scrollbar.maximum() + scrollbar.pageStep())
        top = max(0, min(self.height(), y - (self._drag_offset or 0)))
        scrollbar.setValue(round(top / max(1, self.height()) * total))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            top, size = self._viewport_rect()
            y = event.position().y()
            self._drag_offset = y - top if top <= y <= top + size else size / 2
            self._scroll_to(y)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None:
            self._scroll_to(event.position().y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        QApplication.sendEvent(self.view.result.editor.verticalScrollBar(), event)
        event.accept()


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
            button = QPushButton(self)
            button.setObjectName("MergeGutterButton")
            button.setFixedSize(26, 20)
            button.setIconSize(QSize(16, 16))
            button.setToolTip(f"Use {self.side.title()} for this change")
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.installEventFilter(self.view)
            button.clicked.connect(lambda checked=False, row=index: self.view.use_region(row, self.side))
            self.buttons.append(button)
        self.update_icons()
        self.update_position()

    def update_icons(self):
        kind = "apply_right" if self.side == "left" else "apply_left"
        icon = toolbar_icon(kind, self.view.result.theme.get("text", "#ebdbb2"))
        for button in self.buttons:
            button.setIcon(icon)

    def update_position(self, *_):
        for region, button in zip(self.view._regions, self.buttons):
            if not self.view.region_action_available(region, self.side):
                button.hide()
                continue
            source = self.view.sources[1 if self.side == "left" else 2]
            first, last = getattr(region.record, f"{self.side}_range")
            y = self._range_top(source, first, last)
            if y < 0 or y + button.height() > self.height():
                button.hide()
            else:
                button.move(2, y)
                button.show()
        self.update()

    def _line_y(self, editor: EditorWidget, line: int) -> float:
        line = min(line, editor.editor.document().blockCount() - 1)
        top = editor.editor.line_top(line)
        offset = self.mapFromGlobal(editor.editor.viewport().mapToGlobal(QPoint(0, 0))).y()
        return offset + top

    def _range_top(self, editor: EditorWidget, first: int, last: int) -> float:
        top = self._line_y(editor, first)
        if first == last:
            gap = sum(count for line, count in editor.editor.placeholder_ranges if line == first)
            top -= gap * editor.editor.line_height()
        return top

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(self.view.result.theme.get("surface", "#333333")))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        source = self.view.sources[1 if self.side == "left" else 2]
        width = self.width()
        for region in self.view._regions:
            if not self.view.region_action_available(region, self.side):
                continue
            first, last = getattr(region.record, f"{self.side}_range")
            source_top = self._range_top(source, first, last)
            source_bottom = (source_top if first == last else
                             self._line_y(source, last - 1) + source.editor.line_height())
            result_cursor = QTextCursor(self.view.result.editor.document())
            result_cursor.setPosition(min(region.start, self.view.result.editor.document().characterCount() - 1))
            result_line = result_cursor.blockNumber()
            result_cursor.setPosition(min(max(region.start, region.end - 1),
                                          self.view.result.editor.document().characterCount() - 1))
            result_end_line = max(result_line, result_cursor.blockNumber())
            result_top = self._range_top(self.view.result, result_line,
                                         result_line if region.start == region.end else result_end_line + 1)
            result_bottom = (result_top if region.start == region.end else
                             self._line_y(self.view.result, result_end_line) + self.view.result.editor.line_height())
            if self.side == "left":
                left_top, left_bottom = source_top, source_bottom
                right_top, right_bottom = result_top, result_bottom
            else:
                left_top, left_bottom = result_top, result_bottom
                right_top, right_bottom = source_top, source_bottom
            if region.record.conflict_id is not None:
                conflict = self.view._conflicts[region.record.conflict_id - 1]
                unresolved = conflict.state.endswith("unresolved")
                color_key = "diff_removed" if unresolved else "diff_added"
                edge_key = "overview_removed" if unresolved else "overview_added"
            else:
                color_key = "selection"
                edge_key = "overview_removed" if self.side == "left" else "overview_added"
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
        self._history: list[tuple] = []
        self._history_index = -1
        self._choice = False
        self._loaded = None
        self._merged: MergeResult | None = None
        self._syntax_timer = QTimer(self)
        self._syntax_timer.setSingleShot(True)
        self._syntax_timer.setInterval(180)
        self._syntax_timer.timeout.connect(lambda: self._request_syntax(3))
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
        self.encoding_button = QToolButton(self)
        self.encoding_button.setObjectName("EncodingButton")
        self.encoding_button.setAutoRaise(True)
        self.encoding_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.encoding_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        encoding_menu = QMenu(self.encoding_button)
        encoding_menu.addAction("Auto (Left)", lambda: self._set_encoding(self._loaded[1].encoding if self._loaded else "utf-8"))
        for label, encoding in (("UTF-8", "utf-8"), ("UTF-16 LE", "utf-16-le"),
                                ("UTF-16 BE", "utf-16-be"), ("Windows-1251", "cp1251"),
                                ("Windows-1252", "cp1252"), ("ISO-8859-1", "iso8859-1"),
                                ("Windows-1250", "cp1250"), ("Shift JIS", "shift_jis")):
            encoding_menu.addAction(label, lambda checked=False, value=encoding: self._set_encoding(value))
        self.encoding_button.setMenu(encoding_menu)
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
            editor.editor.installEventFilter(self)
        for editor in self.sources:
            editor.editor.setReadOnly(True)
            editor.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
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
        self.footer = QLabel("0 conflicts · 0 unresolved")
        self.result.editor.document().contentsChange.connect(self._result_changed)
        for pane in (self.sources[1], self.result, self.sources[2]):
            pane.editor.verticalScrollBar().valueChanged.connect(
                lambda value, source=pane: self._sync_scroll(source, value))
        self.result.edited.connect(self._update_status)
        self.result.edited.connect(self._syntax_timer.start)
        self._update_status()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            operation = None
            if event.matches(QKeySequence.StandardKey.Undo):
                operation = self.result.editor.undo
            elif event.matches(QKeySequence.StandardKey.Redo):
                operation = self.result.editor.redo
            if operation is not None:
                if event.type() == QEvent.Type.KeyPress:
                    operation()
                event.accept()
                return True
        return super().eventFilter(watched, event)

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
            row = source.editor.visual_row(value) if value else 0
            for pane in (self.sources[1], self.result, self.sources[2]):
                if pane is not source:
                    pane.editor.verticalScrollBar().setValue(
                        pane.editor.block_for_visual_row(row))
        finally:
            self._syncing_scroll = False

    def set_appearance(self, theme: dict, font, tab_width: int, guide_width: int):
        for editor in (*self.sources, self.result):
            editor.set_theme(theme)
            editor.set_font(font, tab_width, guide_width)
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
        if enabled:
            for index in range(4):
                self._request_syntax(index)
        else:
            self._syntax_timer.stop()
            for editor in (*self.sources, self.result):
                editor.highlighter.set_spans(())

    def confirm_discard(self) -> bool:
        if not self.result.state.dirty:
            return True
        return QMessageBox.question(self, "Unsaved result",
                                    "Discard unsaved changes to the merge result?") == QMessageBox.StandardButton.Yes

    def open_paths(self, base: Path | None, left: Path, right: Path,
                   output: Path | None = None, confirmed: bool = False):
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

            self.scheduler.submit_file(("merge-read", serial, index), read_merge_file,
                                       (path, None), done, failed)
        if read_output:
            self.scheduler.submit_file(("merge-read", serial, 3), read_merge_file,
                                       (output, None), lambda loaded: done(loaded, 3),
                                       lambda message: failed(message, 3))

    def _install_sources(self, paths, output, loaded, existing=None):
        self._loading = True
        try:
            for editor, path, item, label, title in zip(
                    self.sources, paths, loaded, self.titles, ("Base", "Left", "Right")):
                editor.load_text(item.text)
                editor.state.path = path
                editor.state.stamp = item.stamp
                editor.set_syntax_language(_merge_language(path, item.text))
                label.setText(f"{title} · {path.name if path else '(empty)'}")
                label.setToolTip(str(path) if path else "No common base for this conflict")
            for index in range(3):
                self._request_syntax(index)
            self._paths = paths
            self._git_existing = existing
            self.result.state.path = output
            self.result.state.stamp = (existing.stamp if existing is not None else
                                       file_stamp(output) if output and output.exists() else None)
            self.result.state.encoding = loaded[1].encoding
            self.result.state.bom = loaded[1].bom
            self.result.state.newline = loaded[1].newline
            self.result.set_syntax_language(_merge_language(paths[1], loaded[1].text))
        finally:
            self._loading = False
        self.paths_changed.emit()
        self._submit_merge()

    def _submit_merge(self):
        self.scheduler.submit_merge(*(editor.text() for editor in self.sources),
                                    tuple(editor.state.revision for editor in self.sources))

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
        self._request_syntax(3)
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

    def _request_syntax(self, index: int):
        if not self.syntax_highlighting_enabled:
            return
        editor = (*self.sources, self.result)[index]
        if editor.syntax_language is not None:
            self.scheduler.submit_syntax(f"merge-{index}", editor.text(),
                                         editor.syntax_language, editor.state.revision)

    def accept_syntax(self, key: str, revision: int, language: str, spans):
        try:
            index = int(key.removeprefix("merge-"))
            editor = (*self.sources, self.result)[index]
        except (ValueError, IndexError):
            return
        if (self.syntax_highlighting_enabled and editor.state.revision == revision
                and editor.syntax_language == language):
            editor.highlighter.set_spans(spans)

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
        self._selected = (self._selected + direction) % len(self._conflicts)
        conflict = self._conflicts[self._selected]
        for editor, line_range in zip(self.sources, (conflict.record.base_range,
                                                      conflict.record.left_range,
                                                      conflict.record.right_range)):
            editor.select_hunk(*line_range)
        cursor = QTextCursor(self.result.editor.document())
        cursor.setPosition(conflict.start)
        self.result.editor.setTextCursor(cursor)
        self.result.editor.ensureCursorVisible()
        self._update_status()
        self._paint_conflicts()

    def _paint_conflicts(self):
        self._paint_timer.stop()
        self._update_placeholders()
        for side, (editor, field) in enumerate(zip(self.sources, ("base_range", "left_range", "right_range"))):
            selections = []
            if self._merged is not None and side in (1, 2):
                color = QColor(editor.theme.get("diff_removed" if side == 1 else "diff_added", "#6c648b"))
                ranges = self._merged.left_changes if side == 1 else self._merged.right_changes
                for start, end in ranges:
                    for line in range(start, end):
                        block = editor.editor.document().findBlockByNumber(line)
                        if not block.isValid():
                            continue
                        selection = QTextEdit.ExtraSelection()
                        selection.cursor = QTextCursor(block)
                        selection.format.setBackground(color)
                        selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
                        selections.append(selection)
            for conflict in self._conflicts:
                start, end = getattr(conflict.record, field)
                if start == end:
                    continue
                key = "diff_removed" if conflict.state.endswith("unresolved") else "diff_added"
                color = QColor(editor.theme.get(key, "#593a35"))
                for line in range(start, end):
                    block = editor.editor.document().findBlockByNumber(line)
                    if not block.isValid():
                        break
                    selection = QTextEdit.ExtraSelection()
                    selection.cursor = QTextCursor(block)
                    selection.format.setBackground(color)
                    selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
                    selections.append(selection)
            if side in (1, 2):
                side_name = "left" if side == 1 else "right"
                for region in self._regions:
                    if region.record.conflict_id is not None or not self.region_action_available(region, side_name):
                        continue
                    start, end = getattr(region.record, field)
                    for line in range(start, end):
                        block = editor.editor.document().findBlockByNumber(line)
                        if not block.isValid():
                            break
                        selection = QTextEdit.ExtraSelection()
                        selection.cursor = QTextCursor(block)
                        selection.format.setBackground(QColor(editor.theme.get("selection", "#504945")))
                        selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
                        selections.append(selection)
            editor.editor.setExtraSelections(selections)
            editor.editor.set_merge_conflict_ranges(tuple(
                (*getattr(conflict.record, field), conflict.state.endswith("unresolved"))
                for conflict in self._conflicts
            ))
        selections = []
        for region in self._regions:
            if region.record.conflict_id is not None:
                continue
            cursor = QTextCursor(self.result.editor.document())
            cursor.setPosition(min(region.start, self.result.editor.document().characterCount() - 1))
            cursor.setPosition(min(region.end, self.result.editor.document().characterCount() - 1),
                               QTextCursor.MoveMode.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format.setBackground(QColor(self.result.theme.get("selection", "#504945")))
            selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
            selections.append(selection)
        for conflict in self._conflicts:
            key = "diff_removed" if conflict.state.endswith("unresolved") else "diff_added"
            color = QColor(self.result.theme.get(key, "#593a35"))
            start = self.result.editor.document().findBlock(conflict.start).blockNumber()
            end = self.result.editor.document().findBlock(max(conflict.start, conflict.end - 1)).blockNumber()
            for line in range(start, end + 1):
                block = self.result.editor.document().findBlockByNumber(line)
                if not block.isValid():
                    break
                selection = QTextEdit.ExtraSelection()
                selection.cursor = QTextCursor(block)
                selection.format.setBackground(color)
                selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
                selections.append(selection)
        self.result.editor.setExtraSelections(selections)
        self.result.editor.set_merge_conflict_ranges(tuple(
            (self.result.editor.document().findBlock(conflict.start).blockNumber(),
             self.result.editor.document().findBlock(max(conflict.start, conflict.end - 1)).blockNumber() + 1,
             conflict.state.endswith("unresolved"))
            for conflict in self._conflicts
        ))
        self.overview.update()
        self.left_gutter.update_position()
        self.right_gutter.update_position()

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
            f'<span style="color:{theme.get("overview_removed", "#cc241d")}">●</span> '
            f'{count} conflict{"s" if count != 1 else ""}  '
            f'<span style="color:{theme.get("overview_replace", "#d79921")}">●</span> {unresolved} unresolved'
            + (f"  ·  conflict {self._selected + 1}: {selected.state}" if selected else "")
        )
        path = self.result.state.path
        dirty = " •" if self.result.state.dirty else ""
        newline = {"\n": "LF", "\r\n": "CRLF", "\r": "CR"}.get(self.result.state.newline, "LF")
        self.result_title.setText(
            f"Result · {path.name if path else 'Untitled'}{dirty} · "
            f"{self.result.state.encoding.upper()} {newline}"
            f"{' BOM' if self.result.state.bom else ''}"
        )
        self.result_title.setToolTip(str(path) if path else "Result has not been saved")
        self.encoding_button.setText(
            f"Encoding: {self.result.state.encoding.upper()}"
            f"{' BOM' if self.result.state.bom else ''}"
        )
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

    def _set_encoding(self, encoding: str):
        self.result.state.encoding = encoding
        self._update_status()

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
