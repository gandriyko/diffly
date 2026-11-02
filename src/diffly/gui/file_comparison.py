"""Two-pane file comparison: the Files tab."""

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                               QScrollBar, QVBoxLayout, QWidget)

from diffly.core.diff.engine import CompareOptions, DiffResult, compare, delete_hunk_text, transfer_text
from diffly.gui.diff_gutter import DiffGutter
from diffly.gui.diff_overview import DiffOverview
from diffly.gui.editor_widget import EditorWidget, encoding_button
from diffly.gui.find_panel import FindPanel
from diffly.gui.scroll_sync import HorizontalScrollSync, follow_scroll, scroll_to_row
from diffly.gui.seamless_splitter import SeamlessSplitter
from diffly.gui.syntax_controller import SyntaxController
from diffly.services.file_service import file_stamp, read_text_file, status_encoding_text, write_file

# Display name of the Ctrl+Alt shortcut prefix (Qt maps Ctrl to Command on macOS).
NAV_MODIFIER = "Command+Option" if sys.platform == "darwin" else "Ctrl+Alt"


class FileComparisonView(QWidget):
    """Left and right editors with diff alignment, gutter transfers and an overview.

    Mirrors MergeView: the window owns toolbars, menus and the status bar and
    listens to these signals; `footer` and `encoding_button` go in its status bar.
    """

    state_changed = Signal()      # diff, selection or busy state changed
    status_changed = Signal(str)  # transient status-bar message
    paths_changed = Signal()      # a pane was opened from or saved to a file

    def __init__(self, scheduler, compare_options: CompareOptions, parent=None, *,
                 line_numbers_visible=False, syntax_highlighting_enabled=True):
        super().__init__(parent)
        self.setObjectName("DifflyRoot")
        self.scheduler = scheduler
        self.compare_options = compare_options
        self.theme: dict = {}
        self.diff: DiffResult | None = None
        self.selected_hunk = -1
        self._updating_hunk_selection = False
        self._busy_files: set[int] = set()
        self._serial = 0
        self._last_active_side = "left"
        self._syncing_scroll = False

        self.left = EditorWidget(self, "Left")
        self.right = EditorWidget(self, "Right")
        for editor in (self.left, self.right):
            editor.editor.set_line_numbers_visible(line_numbers_visible)
            editor.editor.installEventFilter(self)
            editor.editor.viewport().installEventFilter(self)
            editor.editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            editor.edited.connect(self._documents_changed)
        self.left_title = QLabel(self)
        self.right_title = QLabel(self)
        for title in (self.left_title, self.right_title):
            title.setObjectName("PaneTitle")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)
        self.shared_scrollbar = QScrollBar(Qt.Orientation.Vertical, self)
        self.shared_scrollbar.hide()
        self.gutter = DiffGutter(self.left.editor, self.right.editor, self.shared_scrollbar, self)
        self.gutter.transfer_requested.connect(self.transfer)
        self.overview = DiffOverview(self.left.editor, self.right.editor, self.shared_scrollbar, self)
        # Same resizable pane layout as the Merge and Directories tabs.
        panes = []
        for title, editor, extra in ((self.left_title, self.left, None),
                                     (self.right_title, self.right, self.overview)):
            panel = QWidget(self)
            panel.setMinimumWidth(150)
            column = QVBoxLayout(panel)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(0)
            column.addWidget(title)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(0)
            row.addWidget(editor, 1)
            if extra is not None:
                row.addWidget(extra)
            column.addLayout(row, 1)
            panes.append(panel)
        splitter = SeamlessSplitter(Qt.Orientation.Horizontal, self)
        splitter.setHandleWidth(0)
        splitter.addWidget(panes[0])
        splitter.addWidget(self.gutter)
        splitter.addWidget(panes[1])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        splitter.setCollapsible(1, False)
        splitter.setSizes([450, self.gutter.width(), 450])
        layout.addWidget(splitter, 1)
        self.shared_horizontal_scrollbar = QScrollBar(Qt.Orientation.Horizontal, self)
        self.shared_horizontal_scrollbar.installEventFilter(self)
        layout.addWidget(self.shared_horizontal_scrollbar)
        self.find_panel = FindPanel(self)
        layout.addWidget(self.find_panel)

        self.left.editor.verticalScrollBar().valueChanged.connect(
            lambda value: self._sync_scroll(self.left, self.right, value))
        self.right.editor.verticalScrollBar().valueChanged.connect(
            lambda value: self._sync_scroll(self.right, self.left, value))
        for editor in (self.left, self.right):
            editor.editor.verticalScrollBar().rangeChanged.connect(self._update_shared_scroll_range)
            editor.editor.cursorPositionChanged.connect(lambda e=editor: self._cursor_hunk_changed(e))
        self.shared_scrollbar.valueChanged.connect(self._scroll_both)
        self._update_shared_scroll_range()
        self.horizontal_sync = HorizontalScrollSync((self.left, self.right), self.shared_horizontal_scrollbar)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(300)
        self.debounce.timeout.connect(self._request_diff)
        self.syntax = SyntaxController(scheduler, "files", (self.left, self.right),
                                       syntax_highlighting_enabled, self)
        scheduler.diff_ready.connect(self._accept_diff)
        scheduler.diff_failed.connect(lambda message: self.status_changed.emit(f"Comparison failed: {message}"))

        self.footer = QLabel(self)
        self.encoding_button = encoding_button(self, self._choose_encoding)
        self.encoding_button.setToolTip("Encoding of the active pane")
        self._refresh_titles()

    # -- State -----------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return bool(self._busy_files)

    @property
    def dirty(self) -> bool:
        return self.left.state.dirty or self.right.state.dirty

    def active_pane(self) -> EditorWidget:
        return self.left if self._last_active_side == "left" else self.right

    def focused_editor(self):
        if self.right.editor.hasFocus():
            return self.right.editor
        if self.left.editor.hasFocus():
            return self.left.editor
        return self.active_pane().editor

    def stop(self):
        self.debounce.stop()
        self.syntax.stop()

    # -- Appearance and settings ---------------------------------------------

    def set_appearance(self, theme: dict, font, tab_width: int, guide_width: int):
        self.theme = theme
        for editor in (self.left, self.right):
            editor.set_font(font, tab_width, guide_width)
            editor.set_theme(theme)
        self.overview.set_theme(theme)
        self.gutter.set_theme(theme)
        if self.diff is not None:
            self.left.show_hunks(self.diff.hunks, True, self.diff.left_spans)
            self.right.show_hunks(self.diff.hunks, False, self.diff.right_spans)
        self.horizontal_sync.update_range()
        self._refresh_stats()

    def set_line_numbers_visible(self, visible: bool):
        for editor in (self.left, self.right):
            editor.editor.set_line_numbers_visible(visible)
        self.horizontal_sync.update_range()
        self.gutter.update()

    def set_syntax_highlighting_enabled(self, enabled: bool):
        self.syntax.set_enabled(enabled)

    def set_compare_options(self, options: CompareOptions):
        self.compare_options = options
        self._documents_changed()

    def set_append(self, append: bool):
        self.gutter.set_append(append)

    # -- Titles and status ----------------------------------------------------

    def _refresh_titles(self):
        for editor, label in ((self.left, self.left_title), (self.right, self.right_title)):
            name = editor.state.path.name if editor.state.path else "Untitled"
            label.setText(f"{editor.name} · {name}{' •' if editor.state.dirty else ''}")
            label.setToolTip(str(editor.state.path) if editor.state.path else "")
        self._refresh_encoding_label()

    def window_title(self) -> str:
        if not (self.left.state.path or self.right.state.path):
            return "Diffly"
        left = self.left.state.path.name if self.left.state.path else "Untitled"
        right = self.right.state.path.name if self.right.state.path else "Untitled"
        return f"{left} ↔ {right} — Diffly"

    def _refresh_encoding_label(self):
        state = self.active_pane().state
        self.encoding_button.setText(status_encoding_text(state.encoding, state.bom, state.newline))

    def _refresh_stats(self):
        if self.diff is None or not self.theme:
            self.footer.setText("")
            return
        theme = self.theme
        stats = self.diff.stats
        self.footer.setText(
            f'<span style="color:{theme["overview_removed"]}">●</span> {stats.deleted} deleted  '
            f'<span style="color:{theme["overview_added"]}">●</span> {stats.added} added  '
            f'<span style="color:{theme["overview_replace"]}">●</span> {stats.changed} changed  '
            f'<span style="color:{theme["muted"]}">●</span> {stats.identical} identical'
        )

    # -- Diff -----------------------------------------------------------------

    def _documents_changed(self):
        self.diff = None
        self.selected_hunk = -1
        # Keep the old alignment and colors until the debounced diff is ready.
        # Transfer actions are disabled while diff is None.
        self.state_changed.emit()
        self._refresh_titles()
        self.debounce.start()

    def request_diff(self):
        self.debounce.stop()
        self._request_diff()

    def _request_diff(self):
        self.status_changed.emit("Comparing…")
        self.scheduler.submit_diff(
            self.left.text(), self.right.text(),
            self.left.state.revision, self.right.state.revision,
            self.compare_options,
        )

    def _accept_diff(self, result: DiffResult):
        if result.options != self.compare_options or (result.left_revision, result.right_revision) != (
            self.left.state.revision, self.right.state.revision
        ):
            return
        self.diff = result
        self.selected_hunk = -1
        self._refresh_stats()
        self.left.show_hunks(result.hunks, True, result.left_spans)
        self.right.show_hunks(result.hunks, False, result.right_spans)
        self._update_shared_scroll_range()
        self.overview.set_hunks(result.hunks)
        self.gutter.set_hunks(result.hunks)
        self._cursor_hunk_changed(self.active_pane())
        self.status_changed.emit(f"{len(result.hunks)} difference(s)")

    def _cursor_hunk_changed(self, editor: EditorWidget):
        if self._updating_hunk_selection or self.diff is None:
            return
        line = editor.editor.textCursor().blockNumber()
        self.selected_hunk = -1
        for index, hunk in enumerate(self.diff.hunks):
            start, end = (hunk.left_start, hunk.left_end) if editor is self.left else (hunk.right_start, hunk.right_end)
            if start <= line < end:
                self.selected_hunk = index
                break
        self.state_changed.emit()

    def select_hunk(self, row: int):
        if self.diff is None or not (0 <= row < len(self.diff.hunks)):
            return
        hunk = self.diff.hunks[row]
        left_column = next((span.start for span in self.diff.left_spans if span.line == hunk.left_start), 0)
        right_column = next((span.start for span in self.diff.right_spans if span.line == hunk.right_start), 0)
        self._updating_hunk_selection = True
        try:
            self.left.select_hunk(hunk.left_start, hunk.left_end, left_column)
            self.right.select_hunk(hunk.right_start, hunk.right_end, right_column)
        finally:
            self._updating_hunk_selection = False
        start_row = min(
            self.left.editor.visual_row(hunk.left_start),
            self.right.editor.visual_row(hunk.right_start),
        )
        self.shared_scrollbar.setValue(max(0, start_row - self.shared_scrollbar.pageStep() // 3))
        self.selected_hunk = row
        self.active_pane().editor.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.state_changed.emit()
        self.status_changed.emit(f"Difference {row + 1} of {len(self.diff.hunks)} · {NAV_MODIFIER}+← / → to apply")

    def navigate(self, step: int):
        if self.diff is None or not self.diff.hunks:
            return
        if self.selected_hunk < 0:
            row = 0 if step > 0 else len(self.diff.hunks) - 1
        else:
            row = (self.selected_hunk + step) % len(self.diff.hunks)
        self.select_hunk(row)

    def _current_hunk(self, row: int):
        """The hunk at `row` if the diff still matches both panes."""
        result = self.diff
        if result is None or not (0 <= row < len(result.hunks)):
            return None
        if (result.left_revision, result.right_revision) != (self.left.state.revision, self.right.state.revision):
            return None
        return result.hunks[row]

    def transfer(self, row: int, source_is_left: bool, append: bool = False):
        hunk = self._current_hunk(row)
        if hunk is None:
            return
        source = self.left if source_is_left else self.right
        target = self.right if source_is_left else self.left
        self._apply_in_place(target, transfer_text(source.text(), target.text(), hunk, source_is_left, append))
        self.status_changed.emit(f"Block {'appended' if append else 'copied'}; use Undo to revert")

    def delete_selected(self, row: int, target_is_left: bool | None = None):
        hunk = self._current_hunk(row)
        if hunk is None:
            return
        if target_is_left is None:
            target_is_left = self._last_active_side == "left"
        target = self.left if target_is_left else self.right
        new_text = delete_hunk_text(target.text(), hunk, target_is_left)
        if new_text != target.text():
            self._apply_in_place(target, new_text)
            self.status_changed.emit("Block deleted; use Undo to revert")

    def _apply_in_place(self, target: EditorWidget, new_text: str):
        """Edit one pane without moving the view, so the result can be checked where it was."""
        vertical = self.shared_scrollbar.value()
        horizontal = self.shared_horizontal_scrollbar.value()
        target.apply_text(new_text)
        left, right = self.left.text(), self.right.text()
        # ponytail: diff on the GUI thread so alignment settles before the next repaint;
        # above 2 MB the async path keeps the UI responsive and the view may shift once.
        if len(left) + len(right) <= 2_000_000:
            self.debounce.stop()
            self._accept_diff(compare(left, right, self.left.state.revision,
                                      self.right.state.revision, self.compare_options))
        self.shared_scrollbar.setValue(vertical)
        self._scroll_both(self.shared_scrollbar.value())
        self.horizontal_sync.scroll(horizontal)
        target.editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def paste(self):
        editor = self.focused_editor()
        editor.paste()
        editor.setFocus(Qt.FocusReason.OtherFocusReason)

    # -- Scrolling and input --------------------------------------------------

    def _sync_scroll(self, source: EditorWidget, target: EditorWidget, value: int):
        if self._syncing_scroll:
            return
        self._syncing_scroll = True
        try:
            self.shared_scrollbar.setValue(follow_scroll(source, value, (target,)))
        finally:
            self._syncing_scroll = False

    def _scroll_both(self, value: int):
        if self._syncing_scroll:
            return
        self._syncing_scroll = True
        try:
            scroll_to_row((self.left, self.right), value)
        finally:
            self._syncing_scroll = False

    def _update_shared_scroll_range(self, *_):
        editors = (self.left.editor, self.right.editor)
        bars = tuple(editor.verticalScrollBar() for editor in editors)
        self.shared_scrollbar.setRange(0, max(
            editor.visual_row(bar.maximum()) for editor, bar in zip(editors, bars)
        ))
        self.shared_scrollbar.setPageStep(max(bar.pageStep() for bar in bars))

    def eventFilter(self, watched, event):
        if (watched in (self.left.editor, self.right.editor)
                and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress)
                and event.modifiers() & ~Qt.KeyboardModifier.ShiftModifier
                == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            append = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            commands = {
                Qt.Key.Key_Up: lambda: self.navigate(-1),
                Qt.Key.Key_Down: lambda: self.navigate(1),
                Qt.Key.Key_Left: lambda: self.transfer(self.selected_hunk, False, append),
                Qt.Key.Key_Right: lambda: self.transfer(self.selected_hunk, True, append),
            }
            command = commands.get(event.key())
            if command is not None:
                if event.type() == QEvent.Type.KeyPress:
                    command()
                event.accept()
                return True
        if (event.type() == QEvent.Type.Wheel
                and watched in (self.left.editor.viewport(), self.right.editor.viewport(), self.shared_horizontal_scrollbar)):
            on_bar = watched is self.shared_horizontal_scrollbar
            if self.horizontal_sync.handle_wheel(event, on_bar):
                return True
            if on_bar:
                return False
            QApplication.sendEvent(self.shared_scrollbar, event)
            return True
        if event.type() == QEvent.Type.FocusIn and watched in (self.left.editor, self.right.editor):
            pane = self.left if watched is self.left.editor else self.right
            self._last_active_side = "left" if pane is self.left else "right"
            if self.find_panel.isVisible():
                self.find_panel.set_target(pane)
            self._refresh_encoding_label()
        return super().eventFilter(watched, event)

    # -- Files ----------------------------------------------------------------

    def confirm_discard(self, editor: EditorWidget) -> bool:
        if not editor.state.dirty:
            return True
        answer = QMessageBox.question(self, "Unsaved changes", "Discard unsaved changes in this pane?")
        return answer == QMessageBox.StandardButton.Yes

    def open_dialog(self, editor: EditorWidget):
        if id(editor) in self._busy_files or not self.confirm_discard(editor):
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Open text file")
        if filename:
            self.open_path(editor, Path(filename))

    def confirm_replace(self) -> bool:
        """Whether both panes may be replaced: not busy, unsaved edits discarded."""
        return not self.busy and all(self.confirm_discard(editor) for editor in (self.left, self.right))

    def reload(self):
        """Re-read open files from disk, or re-compare unsaved panes."""
        if self.busy:
            return
        loaded = [editor for editor in (self.left, self.right) if editor.state.path is not None]
        if not all(self.confirm_discard(editor) for editor in loaded):
            return
        if not loaded:
            self.request_diff()
            return
        for editor in loaded:
            self.open_path(editor, editor.state.path)

    def _choose_encoding(self, encoding: str | None):
        editor = self.active_pane()
        if id(editor) in self._busy_files:
            return
        if editor.state.path is None:
            editor.state.encoding = encoding or "utf-8"
            editor.state.bom = False
            self._refresh_encoding_label()
            return
        if not self.confirm_discard(editor):
            return
        self.open_path(editor, editor.state.path, encoding)

    def _start_file_task(self, editor: EditorWidget, message: str) -> int:
        self._busy_files.add(id(editor))
        self.state_changed.emit()
        self.status_changed.emit(message)
        self._serial += 1
        return self._serial

    def _finish_file_task(self, editor: EditorWidget):
        self._busy_files.discard(id(editor))
        self.state_changed.emit()

    def open_path(self, editor: EditorWidget, path: Path, encoding: str | None = None):
        if id(editor) in self._busy_files:
            return
        revision = editor.state.revision
        key = self._start_file_task(editor, f"Opening {path.name}…")

        def done(loaded):
            self._finish_file_task(editor)
            if editor.state.revision != revision:
                self.status_changed.emit("Open discarded because the pane changed")
                return
            editor.load_text(loaded.text)
            editor.state.path = path
            editor.state.stamp = loaded.stamp
            editor.state.newline = loaded.newline
            editor.state.encoding = loaded.encoding
            editor.state.bom = loaded.bom
            self.syntax.schedule(editor)
            self._refresh_titles()
            self.status_changed.emit(f"Opened {path.name}")
            self.paths_changed.emit()

        def failed(message):
            self._finish_file_task(editor)
            QMessageBox.critical(self, "Open failed", message)

        self.scheduler.submit_file(key, read_text_file, (path, encoding), done, failed)

    def save_active(self, save_as: bool = False):
        self.save(self.active_pane(), save_as)

    def save(self, editor: EditorWidget, save_as: bool = False):
        if id(editor) in self._busy_files:
            return
        path = editor.state.path
        if path is None or save_as:
            filename, _ = QFileDialog.getSaveFileName(self, "Save text file", str(path or ""))
            if not filename:
                return
            path = Path(filename)
            if path.exists() and path != editor.state.path:
                answer = QMessageBox.question(self, "Overwrite file", f"Replace {path.name}?")
                if answer != QMessageBox.StandardButton.Yes:
                    return
                expected = file_stamp(path)
            else:
                expected = editor.state.stamp if path == editor.state.path else None
        else:
            expected = editor.state.stamp
        text = editor.text()
        revision = editor.state.revision
        newline = editor.state.newline
        bom = editor.state.bom
        encoding = editor.state.encoding
        key = self._start_file_task(editor, f"Saving {path.name}…")

        def done(stamp):
            self._finish_file_task(editor)
            editor.state.path = path
            editor.state.stamp = stamp
            editor.state.saved_revision = revision
            self.syntax.schedule(editor)
            self._refresh_titles()
            self.status_changed.emit(f"Saved {path.name}")
            self.paths_changed.emit()

        def failed(message):
            self._finish_file_task(editor)
            QMessageBox.critical(self, "Save failed", message)

        self.scheduler.submit_file(key, write_file, (path, text, newline, bom, expected, encoding), done, failed)
