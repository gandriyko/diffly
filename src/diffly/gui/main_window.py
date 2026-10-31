import os
import stat
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, QTimer, Qt
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QMenu,
    QInputDialog,
    QScrollBar,
    QTabWidget,
    QToolBar,
    QToolButton,
    QWidget,
)

from diffly.core.diff.engine import CompareOptions, DiffResult, compare, delete_hunk_text, transfer_text
from diffly.core.directories.filters import DEFAULT_PRESETS, PRESETS
from diffly.core.documents.syntax import language_for
from diffly.gui.diff_gutter import DiffGutter
from diffly.gui.diff_overview import DiffOverview
from diffly.gui.directory_comparison import DirectoryComparisonView
from diffly.gui.editor_widget import EditorWidget
from diffly.gui.find_panel import FindPanel
from diffly.gui.icons import toolbar_icon
from diffly.gui.settings_page import SettingsPage
from diffly.gui.linux_titlebar import LinuxTitleBar
from diffly.gui.merge_view import MergeView
from diffly.gui.theme import (
    apply_theme, editor_font, load_config, save_comparison_options, save_directory_filters,
    save_editor_display_options, save_font_size, save_indent_width, save_selected_theme,
)
from diffly.platform.macos import install_inline_toolbar
from diffly.services.file_service import file_stamp, read_file, write_file
from diffly.services.directory_service import (
    copy_directory_checked, copy_file_checked, directory_stamp, path_stamp, regular_file_stamp,
)
from diffly.services.task_scheduler import TaskScheduler
from diffly.services.session_store import load_sessions, remember, save_sessions


ENCODINGS = (("UTF-8", "utf-8"), ("UTF-16 LE", "utf-16-le"),
             ("UTF-16 BE", "utf-16-be"), ("Windows-1251", "cp1251"),
             ("Windows-1252", "cp1252"), ("ISO-8859-1", "iso8859-1"),
             ("Windows-1250", "cp1250"), ("Shift JIS", "shift_jis"))


def _suppress_dialogs():
    """DIFFLY_NO_DIALOGS=1: never block on a modal dialog in tests or scripted runs.

    Questions answer Yes, notices go to stderr, file pickers cancel.
    """
    def notice(_parent, title, text, *_args, **_kwargs):
        print(f"[diffly] {title}: {text}", file=sys.stderr)
        return QMessageBox.StandardButton.Ok

    def question(_parent, title, text, *_args, **_kwargs):
        print(f"[diffly] {title}: {text} -> Yes", file=sys.stderr)
        return QMessageBox.StandardButton.Yes

    def message_box_exec(box):
        print(f"[diffly] {box.windowTitle()}: {box.text()} -> Yes", file=sys.stderr)
        return QMessageBox.StandardButton.Yes

    QMessageBox.information = QMessageBox.warning = QMessageBox.critical = staticmethod(notice)
    QMessageBox.question = staticmethod(question)
    QMessageBox.exec = message_box_exec
    QFileDialog.getOpenFileName = QFileDialog.getSaveFileName = staticmethod(lambda *_args, **_kwargs: ("", ""))
    QFileDialog.getExistingDirectory = staticmethod(lambda *_args, **_kwargs: "")


def _shift_held() -> bool:
    return bool(QGuiApplication.queryKeyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)


class _ShiftWatcher(QObject):
    """App-wide Shift tracking, so toolbar icons switch to append while Shift is down."""

    def __init__(self, callback, parent):
        super().__init__(parent)
        self._callback = callback

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and event.key() == Qt.Key.Key_Shift:
            self._callback(event.type() == QEvent.Type.KeyPress)
        elif event.type() == QEvent.Type.ApplicationStateChange:
            # Shift may be released while another app is focused.
            self._callback(_shift_held())
        return False


class MainWindow(QMainWindow):
    def __init__(self, merge_enabled: bool = False, git_mergetool: bool = False):
        super().__init__()
        self.merge_enabled = merge_enabled
        self.git_mergetool = git_mergetool
        self.git_merge_success = False
        self.git_merge_aborted = False
        if os.environ.get("DIFFLY_NO_DIALOGS"):
            _suppress_dialogs()
        self.setWindowTitle("Diffly")
        self.resize(1100, 720)
        self._linux_titlebar = None
        if sys.platform.startswith("linux"):
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
            self._linux_titlebar = LinuxTitleBar(self)
            self.setMenuWidget(self._linux_titlebar)
        self.scheduler = TaskScheduler(self)
        self.scheduler.diff_ready.connect(self._accept_diff)
        self.scheduler.diff_failed.connect(self._diff_error)
        self.scheduler.syntax_ready.connect(self._accept_syntax)
        self.scheduler.merge_ready.connect(self._accept_merge)
        self.scheduler.merge_failed.connect(self._merge_error)
        self.config = load_config()
        try:
            self.sessions = load_sessions()
        except (OSError, ValueError) as exc:
            self.sessions = {"saved": [], "recent": []}
            self._sessions_error = str(exc)
        else:
            self._sessions_error = None
        self.compare_options = CompareOptions(**{
            key: bool(self.config.settings.get(key, False)) for key in
            ("ignore_whitespace", "ignore_trailing_whitespace", "ignore_case")
        })
        self.toolbar = None
        self.left = EditorWidget()
        self.right = EditorWidget()
        self.syntax_highlighting_enabled = bool(self.config.settings.get("syntax_highlighting", True))
        for editor in (self.left, self.right):
            editor.editor.set_line_numbers_visible(bool(
                self.config.settings.get("show_line_numbers", False)))
        self.left.editor.installEventFilter(self)
        self.right.editor.installEventFilter(self)
        self.left.editor.viewport().installEventFilter(self)
        self.right.editor.viewport().installEventFilter(self)
        for editor in (self.left, self.right):
            editor.editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            editor.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._last_active_side = "left"
        self.left.edited.connect(lambda: self._documents_changed(self.left))
        self.right.edited.connect(lambda: self._documents_changed(self.right))
        self._diff: DiffResult | None = None
        self._selected_hunk = -1
        self._updating_hunk_selection = False
        self._busy_files: set[int] = set()
        self._directory_copy_busy = False
        self._skip_copy_confirmation = False
        self._serial = 0
        self._native_toolbar_attempts = 0
        self._native_toolbar_bridge = None
        self._merge_native_toolbar_bridge = None
        self._append_mode = False
        QApplication.instance().installEventFilter(_ShiftWatcher(self._set_append_mode, self))

        self.left_title = QLabel()
        self.right_title = QLabel()
        self.left_title.setObjectName("PaneTitle")
        self.right_title.setObjectName("PaneTitle")
        root = QWidget()
        root.setObjectName("DifflyRoot")
        layout = QGridLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(0)
        layout.setVerticalSpacing(0)
        layout.addWidget(self.left_title, 0, 0)
        layout.addWidget(self.right_title, 0, 2)
        layout.addWidget(self.left, 1, 0)
        layout.addWidget(self.right, 1, 2)
        self.shared_scrollbar = QScrollBar(Qt.Orientation.Vertical, root)
        self.shared_scrollbar.hide()
        self.gutter = DiffGutter(self.left.editor, self.right.editor, self.shared_scrollbar, root)
        self.gutter.transfer_requested.connect(self._transfer)
        layout.addWidget(self.gutter, 1, 1)
        self.overview = DiffOverview(self.left.editor, self.right.editor, self.shared_scrollbar, root)
        layout.addWidget(self.overview, 1, 3)
        self.shared_horizontal_scrollbar = QScrollBar(Qt.Orientation.Horizontal, root)
        self.shared_horizontal_scrollbar.installEventFilter(self)
        layout.addWidget(self.shared_horizontal_scrollbar, 2, 0, 1, 3)
        self.find_panel = FindPanel(self, root)
        layout.addWidget(self.find_panel, 3, 0, 1, 4)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(2, 1)
        layout.setRowStretch(1, 1)
        self.file_comparison = root
        self.directory_comparison = DirectoryComparisonView(
            self.scheduler, list(self.config.settings.get("ignore_patterns", [])), self,
            bool(self.config.settings.get("use_gitignore", True)),
            tuple(key for key in self.config.settings.get("ignore_presets", DEFAULT_PRESETS) if key in PRESETS),
        )
        self._directory_stats = None
        self.directory_comparison.open_files.connect(self._open_directory_files)
        self.directory_comparison.comparison_ready.connect(self._remember_directory)
        self.directory_comparison.stats_changed.connect(self._set_directory_stats)
        self.directory_comparison.message_changed.connect(self._directory_message_changed)
        self.directory_comparison.selection_changed.connect(self._refresh_toolbar)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("ComparisonTabs")
        self.tabs.addTab(self.directory_comparison, "Directories")
        self.tabs.addTab(root, "Files")
        self.merge_view = MergeView(
            self.scheduler, self,
            line_numbers_visible=bool(self.config.settings.get("show_line_numbers", False)),
            syntax_highlighting_enabled=self.syntax_highlighting_enabled,
            git_mergetool=git_mergetool,
        )
        self.merge_find_panel = FindPanel(self, self.merge_view)
        self.merge_find_panel.set_target(self.merge_view.result)
        self.merge_view.layout().insertWidget(1, self.merge_find_panel)
        if merge_enabled:
            self.tabs.addTab(self.merge_view, "Merge")
        else:
            self.merge_view.hide()
        self.merge_view.paths_changed.connect(self._remember_merge)
        self.merge_view.status_changed.connect(self.statusBar().showMessage)
        self.statusBar().addPermanentWidget(self.merge_view.footer)
        self.statusBar().addPermanentWidget(self.merge_view.encoding_button)
        self.settings_page = SettingsPage(self.config.settings, self.config.themes, self)
        self.tabs.addTab(self.settings_page, "Settings")
        self.settings_page.theme_changed.connect(self._choose_theme)
        self.settings_page.font_size_changed.connect(self._set_font_size)
        self.settings_page.indent_width_changed.connect(self._set_indent_width)
        self.settings_page.editor_display_changed.connect(self._set_editor_display_options)
        self.settings_page.comparison_changed.connect(self._set_comparison_options)
        self.settings_page.filters_changed.connect(self._set_directory_filters)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)

        self._syncing_scroll = False
        self.left.editor.verticalScrollBar().valueChanged.connect(lambda value: self._sync_scroll(self.left, self.right, value))
        self.right.editor.verticalScrollBar().valueChanged.connect(lambda value: self._sync_scroll(self.right, self.left, value))
        for editor in (self.left, self.right):
            editor.editor.verticalScrollBar().rangeChanged.connect(self._update_shared_scroll_range)
        self.shared_scrollbar.valueChanged.connect(self._scroll_both)
        self._update_shared_scroll_range()
        self._syncing_horizontal_scroll = False
        for editor in (self.left, self.right):
            bar = editor.editor.horizontalScrollBar()
            bar.valueChanged.connect(self._scroll_both_horizontal)
            bar.rangeChanged.connect(self._update_horizontal_scroll_range)
        self.shared_horizontal_scrollbar.valueChanged.connect(self._scroll_both_horizontal)
        self._update_horizontal_scroll_range()
        self._make_menu()
        self._make_toolbar()
        self._make_merge_toolbar()
        self.merge_view.state_changed.connect(self._refresh_merge_toolbar)
        self._recent_timer = QTimer(self)
        self._recent_timer.setSingleShot(True)
        self._recent_timer.setInterval(700)
        self._recent_timer.timeout.connect(self._remember_files)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(300)
        self.debounce.timeout.connect(self._request_diff)
        self._syntax_timers = {}
        for side, editor in (("left", self.left), ("right", self.right)):
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(180)
            timer.timeout.connect(lambda s=side, e=editor: self._request_syntax(s, e))
            self._syntax_timers[editor] = timer
        self.summary_label = QLabel()
        self.directory_summary_label = QLabel()
        self.encoding_label = QToolButton()
        self.encoding_label.setObjectName("EncodingButton")
        self.encoding_label.setAutoRaise(True)
        self.encoding_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.encoding_label.setToolTip("Encoding of the active pane")
        self.encoding_label.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        encoding_menu = QMenu(self.encoding_label)
        encoding_menu.addAction("Auto", lambda: self._choose_encoding(None))
        for label, encoding in ENCODINGS:
            encoding_menu.addAction(label, lambda checked=False, value=encoding: self._choose_encoding(value))
        self.encoding_label.setMenu(encoding_menu)
        self.statusBar().addPermanentWidget(self.summary_label)
        self.statusBar().addPermanentWidget(self.directory_summary_label)
        self.statusBar().addPermanentWidget(self.encoding_label)
        self._apply_theme(self.config.settings.get("theme", "gruvbox"))
        self._refresh_titles()
        self._refresh_toolbar()
        self.left.editor.cursorPositionChanged.connect(lambda: self._cursor_hunk_changed(self.left))
        self.right.editor.cursorPositionChanged.connect(lambda: self._cursor_hunk_changed(self.right))
        self.statusBar().showMessage("Open a file in each pane to compare")
        self._tab_changed(self.tabs.currentIndex())

    def _make_menu(self):
        menu_bar = self._linux_titlebar.menu_bar if self._linux_titlebar is not None else self.menuBar()
        file_menu = menu_bar.addMenu("File")
        for label, shortcut, callback in (
            ("Open Left…", "Ctrl+O", lambda: self._open(self.left)),
            ("Open Right…", "Ctrl+Shift+O", lambda: self._open(self.right)),
            ("Save", "Ctrl+S", self._save_active),
            ("Save As…", None, lambda: self._save_active(True)),
        ):
            action = QAction(label, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            file_menu.addAction(action)
        file_menu.addSeparator()
        file_menu.addAction("Save Comparison…", self._save_comparison)
        self.saved_menu = file_menu.addMenu("Saved Comparisons")
        self.recent_menu = file_menu.addMenu("Recent Comparisons")
        self._refresh_session_menus()
        file_menu.addSeparator()
        self.merge_recreate_action = file_menu.addAction("Recreate Merge Result", self.merge_view.recreate)
        self.merge_reload_action = file_menu.addAction("Reload Merge Sources", self.merge_view.reload_sources)
        edit_menu = menu_bar.addMenu("Edit")
        for label, shortcut, operation in (
            ("Undo", "Ctrl+Z", "undo"),
            ("Redo", "Ctrl+Shift+Z", "redo"),
        ):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda checked=False, op=operation: getattr(self.focused_editor(), op)())
            edit_menu.addAction(action)
        edit_menu.addSeparator()
        self.merge_actions = []
        for label, shortcut, callback in (
            ("Previous conflict", "Shift+F7", lambda: self.merge_view.navigate(-1)),
            ("Next conflict", "F7", lambda: self.merge_view.navigate(1)),
            ("Use Left", "Ctrl+Alt+1", lambda: self.merge_view.use("left")),
            ("Use Right", "Ctrl+Alt+2", lambda: self.merge_view.use("right")),
            ("Use Base", "Ctrl+Alt+3", lambda: self.merge_view.use("base")),
            ("Mark Resolved", "Ctrl+Alt+0", self.merge_view.mark_resolved),
        ):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            edit_menu.addAction(action)
            self.merge_actions.append(action)
        edit_menu.addSeparator()
        self.find_actions = []
        for label, shortcut, callback in (
            ("Find…", "Ctrl+F", lambda: self._show_find(False)),
            ("Replace…", "Ctrl+H", lambda: self._show_find(True)),
            ("Find Next", "F3", lambda: self._repeat_find(False)),
            ("Find Previous", "Shift+F3", lambda: self._repeat_find(True)),
        ):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            edit_menu.addAction(action)
            self.find_actions.append(action)
        edit_menu.addSeparator()
        for label, shortcut, callback in (
            ("Next Difference", "Ctrl+Alt+Down", lambda: self._navigate(1)),
            ("Previous Difference", "Ctrl+Alt+Up", lambda: self._navigate(-1)),
            ("Apply Right to Left", "Ctrl+Alt+Left", lambda: self._transfer(self._selected_hunk, False)),
            ("Apply Left to Right", "Ctrl+Alt+Right", lambda: self._transfer(self._selected_hunk, True)),
            ("Append Right Below Left", "Ctrl+Alt+Shift+Left", lambda: self._transfer(self._selected_hunk, False, True)),
            ("Append Left Below Right", "Ctrl+Alt+Shift+Right", lambda: self._transfer(self._selected_hunk, True, True)),
            ("Delete Selected Block", "Ctrl+Alt+Delete", lambda: self._delete_selected(self._selected_hunk)),
        ):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            edit_menu.addAction(action)

        settings_menu = menu_bar.addMenu("Settings")
        settings_action = settings_menu.addAction("Open Settings")
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(lambda: self.tabs.setCurrentWidget(self.settings_page))

    def _show_find(self, replace: bool):
        panel, target = self._active_find_target()
        if panel is None:
            return
        panel.show_search(replace, target)

    def _repeat_find(self, backwards: bool):
        panel, target = self._active_find_target()
        if panel is None:
            return
        if not panel.query.text():
            self._show_find(False)
            return
        panel.set_target(target)
        if backwards:
            found = panel.find_previous()
        else:
            found = panel.find_next()
        if not found:
            self.statusBar().showMessage("No matches in current editor")

    def _active_find_target(self):
        if self.tabs.currentWidget() is self.file_comparison:
            return self.find_panel, self._active_pane()
        if self.tabs.currentWidget() is self.merge_view:
            return self.merge_find_panel, self.merge_view.result
        return None, None

    def _set_comparison_options(self, options: CompareOptions):
        self.compare_options = options
        self._documents_changed(self.left)
        values = {key: getattr(options, key) for key in
                  ("ignore_whitespace", "ignore_trailing_whitespace", "ignore_case")}
        self.config.settings.update(values)
        try:
            save_comparison_options(options)
        except OSError as exc:
            self.statusBar().showMessage(f"Comparison settings applied but not saved: {exc}")

    def _comparison_entry(self):
        if self.tabs.currentWidget() is self.merge_view:
            if self.git_mergetool:
                return None
            if self.merge_view.paths is None:
                return None
            base, left, right = self.merge_view.paths
            entry = {"kind": "merge", "base": str(base), "left": str(left), "right": str(right)}
            if self.merge_view.result.state.path is not None:
                entry["result"] = str(self.merge_view.result.state.path)
            return entry
        if self.tabs.currentWidget() is self.directory_comparison:
            left = self.directory_comparison.left_path.text().strip()
            right = self.directory_comparison.right_path.text().strip()
            kind = "directories"
        else:
            left = str(self.left.state.path or "")
            right = str(self.right.state.path or "")
            kind = "files"
        if not left or not right:
            return None
        return {"kind": kind, "left": str(Path(left).expanduser().absolute()),
                "right": str(Path(right).expanduser().absolute()),
                "options": {key: getattr(self.compare_options, key) for key in
                            ("ignore_whitespace", "ignore_trailing_whitespace", "ignore_case")}}

    def _persist_sessions(self):
        if self._sessions_error is not None:
            self.statusBar().showMessage(f"Sessions file could not be read: {self._sessions_error}")
            return
        try:
            save_sessions(self.sessions)
        except OSError as exc:
            self.statusBar().showMessage(f"Could not save comparisons: {exc}")
        else:
            self._refresh_session_menus()

    def _refresh_session_menus(self):
        for menu, key in ((self.saved_menu, "saved"), (self.recent_menu, "recent")):
            menu.clear()
            visible_entries = [entry for entry in self.sessions[key]
                               if self.merge_enabled or entry["kind"] != "merge"]
            for entry in visible_entries:
                label = entry.get("name") or (f'{Path(entry["base"]).name}: {Path(entry["left"]).name} + {Path(entry["right"]).name}'
                                              if entry["kind"] == "merge" else
                                              f'{Path(entry["left"]).name} ↔ {Path(entry["right"]).name}')
                action = menu.addAction(label)
                action.setToolTip(f'{entry.get("base", "")} {entry["left"]} ↔ {entry["right"]}')
                action.triggered.connect(lambda checked=False, item=entry.copy(): self._open_comparison(item))
            if not visible_entries:
                empty = menu.addAction("No comparisons")
                empty.setEnabled(False)
        if any(self.merge_enabled or entry["kind"] != "merge" for entry in self.sessions["saved"]):
            self.saved_menu.addSeparator()
            self.saved_menu.addAction("Remove Saved Comparison…", self._remove_saved_comparison)
        if any(self.merge_enabled or entry["kind"] != "merge" for entry in self.sessions["recent"]):
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("Clear Recent Comparisons", self._clear_recent)

    def _save_comparison(self):
        entry = self._comparison_entry()
        if entry is None:
            QMessageBox.information(self, "Save Comparison", "Open files, directories, or a merge first.")
            return
        name, accepted = QInputDialog.getText(self, "Save Comparison", "Name")
        name = name.strip()
        if not accepted or not name:
            return
        entry["name"] = name
        self.sessions["saved"] = [item for item in self.sessions["saved"] if item.get("name") != name]
        self.sessions["saved"].insert(0, entry)
        self._persist_sessions()

    def _remove_saved_comparison(self):
        names = [entry["name"] for entry in self.sessions["saved"]
                 if self.merge_enabled or entry["kind"] != "merge"]
        name, accepted = QInputDialog.getItem(self, "Remove Saved Comparison", "Comparison", names, 0, False)
        if accepted:
            self.sessions["saved"] = [entry for entry in self.sessions["saved"] if entry["name"] != name]
            self._persist_sessions()

    def _clear_recent(self):
        self.sessions["recent"] = []
        self._persist_sessions()

    def _remember_files(self):
        if self._busy_files or self.left.state.path is None or self.right.state.path is None:
            return
        entry = {"kind": "files", "left": str(self.left.state.path.absolute()),
                 "right": str(self.right.state.path.absolute()),
                 "options": {key: getattr(self.compare_options, key) for key in
                             ("ignore_whitespace", "ignore_trailing_whitespace", "ignore_case")}}
        remember(self.sessions, entry)
        self._persist_sessions()

    def _remember_directory(self, left: str, right: str):
        entry = {"kind": "directories", "left": str(Path(left).expanduser().absolute()),
                 "right": str(Path(right).expanduser().absolute())}
        remember(self.sessions, entry)
        self._persist_sessions()

    def _remember_merge(self):
        entry = self._comparison_entry() if self.tabs.currentWidget() is self.merge_view else None
        if entry is not None:
            remember(self.sessions, entry)
            self._persist_sessions()

    def _open_comparison(self, entry: dict):
        if self._busy_files or self._directory_copy_busy:
            return
        paths = ((Path(entry["base"]), Path(entry["left"]), Path(entry["right"]))
                 if entry["kind"] == "merge" else (Path(entry["left"]), Path(entry["right"])))
        if not all(path.exists() for path in paths):
            QMessageBox.warning(self, "Open Comparison", "One or both paths no longer exist.")
            return
        if entry["kind"] == "merge":
            if not self.merge_enabled:
                return
            self.tabs.setCurrentWidget(self.merge_view)
            self.merge_view.open_paths(*paths, Path(entry["result"]) if entry.get("result") else None)
        elif entry["kind"] == "files":
            if not all(self._confirm_discard(editor) for editor in (self.left, self.right)):
                return
            self.tabs.setCurrentWidget(self.file_comparison)
            options = entry.get("options", {})
            self.compare_options = CompareOptions(**{
                key: bool(options.get(key, False)) for key in
                ("ignore_whitespace", "ignore_trailing_whitespace", "ignore_case")
            })
            self.settings_page.set_comparison_options(self.compare_options)
            self.open_path(self.left, paths[0])
            self.open_path(self.right, paths[1])
        else:
            self.tabs.setCurrentWidget(self.directory_comparison)
            self.directory_comparison.left_path.setText(str(paths[0]))
            self.directory_comparison.right_path.setText(str(paths[1]))
            self.directory_comparison.compare()

    def _make_toolbar(self):
        modifier = "Command+Option" if sys.platform == "darwin" else "Ctrl+Alt"
        toolbar = QToolBar("Diff controls", self._linux_titlebar or self)
        toolbar.setObjectName("DifflyToolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        if self._linux_titlebar is None:
            self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        else:
            self._linux_titlebar.add_toolbar(toolbar)

        self._toolbar_icons = {}

        def add(label, icon_kind, tip, callback):
            action = QAction(label, self)
            action.setToolTip(tip)
            action.triggered.connect(callback)
            toolbar.addAction(action)
            self._toolbar_icons[action] = icon_kind
            return action

        self.refresh_action = add("Refresh", "refresh", "Refresh directory comparison", self._refresh_current)
        spacer = QWidget(toolbar)
        spacer.setFixedWidth(12)
        toolbar.addWidget(spacer)
        self.previous_action = add("Previous", "up", f"Previous difference ({modifier}+Up)", lambda: self._navigate(-1))
        self.next_action = add("Next", "down", f"Next difference ({modifier}+Down)", lambda: self._navigate(1))
        spacer = QWidget(toolbar)
        spacer.setFixedWidth(12)
        toolbar.addWidget(spacer)
        self.apply_left_action = add("Apply ←", "apply_left", f"Copy selected right block to left ({modifier}+Left)", lambda: self._transfer(self._selected_hunk, False, _shift_held()))
        self.apply_right_action = add("Apply →", "apply_right", f"Copy selected left block to right ({modifier}+Right)", lambda: self._transfer(self._selected_hunk, True, _shift_held()))
        spacer = QWidget(toolbar)
        spacer.setFixedWidth(12)
        toolbar.addWidget(spacer)
        self.paste_action = add("Paste", "paste", "Paste clipboard into the active editor", self._paste_current)
        # Keep side-specific delete actions available to callers without
        # showing destructive controls in the compact toolbar.
        self.delete_left_action = QAction("Delete L", self)
        self.delete_left_action.triggered.connect(lambda: self._delete_selected(self._selected_hunk, True))
        self.delete_right_action = QAction("Delete R", self)
        self.delete_right_action.triggered.connect(lambda: self._delete_selected(self._selected_hunk, False))
        self.toolbar = toolbar
        toolbar.installEventFilter(self)

    def _make_merge_toolbar(self):
        toolbar = QToolBar("Merge controls", self._linux_titlebar or self)
        toolbar.setObjectName("DifflyToolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        if self._linux_titlebar is None:
            self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        else:
            self._linux_titlebar.add_toolbar(toolbar)
        self._merge_toolbar_icons = {}

        def add(label, kind, callback, tip):
            action = QAction(label, self)
            action.setToolTip(tip)
            action.triggered.connect(callback)
            toolbar.addAction(action)
            self._merge_toolbar_icons[action] = kind
            return action

        self.merge_previous_action = add("Previous conflict", "up", lambda: self.merge_view.navigate(-1),
                                         "Previous conflict (Shift+F7)")
        self.merge_next_action = add("Next conflict", "down", lambda: self.merge_view.navigate(1),
                                     "Next conflict (F7)")
        toolbar.addSeparator()
        self.merge_use_left_action = add("Use Left", "apply_right", lambda: self.merge_view.use("left"),
                                         "Use Left for selected conflict (Ctrl+Alt+1)")
        self.merge_use_right_action = add("Use Right", "apply_left", lambda: self.merge_view.use("right"),
                                          "Use Right for selected conflict (Ctrl+Alt+2)")
        self.merge_toolbar = toolbar
        toolbar.hide()
        self._refresh_merge_toolbar()

    def _refresh_merge_toolbar(self):
        if not hasattr(self, "merge_toolbar"):
            return
        view = self.merge_view
        for action, key in ((self.merge_previous_action, "Previous conflict"),
                            (self.merge_next_action, "Next conflict")):
            action.setEnabled(view.buttons[key].isEnabled())
        selected = view._selected >= 0 and not view.busy
        self.merge_use_left_action.setEnabled(selected)
        self.merge_use_right_action.setEnabled(selected)
        active = self.tabs.currentWidget() is view
        self.merge_recreate_action.setEnabled(active and view.buttons["Recreate Result"].isEnabled())
        self.merge_reload_action.setEnabled(active and view.buttons["Reload Sources"].isEnabled())
        if getattr(self, "_merge_native_toolbar_bridge", None) is not None:
            self._merge_native_toolbar_bridge.sync()

    def _refresh_toolbar(self):
        if self.tabs.currentWidget() in (self.settings_page, self.merge_view):
            for action in (self.refresh_action, self.previous_action, self.next_action,
                           self.apply_left_action, self.apply_right_action, self.paste_action,
                           self.delete_left_action, self.delete_right_action):
                action.setEnabled(False)
            if self._native_toolbar_bridge is not None:
                self._native_toolbar_bridge.sync()
            return
        files_active = self.tabs.currentWidget() is self.file_comparison
        if not files_active:
            view = self.directory_comparison
            active = not self._directory_copy_busy
            self.refresh_action.setEnabled(active and bool(view.left_path.text().strip()) and bool(view.right_path.text().strip()))
            self.refresh_action.setToolTip("Refresh directory comparison")
            self.previous_action.setEnabled(active and view.has_navigable_files())
            self.next_action.setEnabled(active and view.has_navigable_files())
            self.apply_left_action.setEnabled(active and view.can_copy(True))
            self.apply_right_action.setEnabled(active and view.can_copy(False))
            self.paste_action.setEnabled(False)
            self.delete_left_action.setEnabled(False)
            self.delete_right_action.setEnabled(False)
            modifier = "Command+Option" if sys.platform == "darwin" else "Ctrl+Alt"
            self.previous_action.setToolTip(f"Previous different file ({modifier}+Up)")
            self.next_action.setToolTip(f"Next different file ({modifier}+Down)")
            entry = view.selected_entry()
            left_kind = "folder" if entry is not None and entry.right_kind == "directory" else "file"
            right_kind = "folder" if entry is not None and entry.left_kind == "directory" else "file"
            self.apply_left_action.setToolTip(f"Copy selected {left_kind} from right to left ({modifier}+Left)")
            self.apply_right_action.setToolTip(f"Copy selected {right_kind} from left to right ({modifier}+Right)")
            self._set_transfer_icons(False)
            if self._native_toolbar_bridge is not None:
                self._native_toolbar_bridge.sync()
            return
        has_hunks = files_active and self._diff is not None and bool(self._diff.hunks)
        self.refresh_action.setEnabled(not self._busy_files)
        self.refresh_action.setToolTip("Reload open files from disk and compare")
        selected = has_hunks and 0 <= self._selected_hunk < len(self._diff.hunks)
        self.previous_action.setEnabled(has_hunks)
        self.next_action.setEnabled(has_hunks)
        self.paste_action.setEnabled(files_active)
        for action in (self.apply_left_action, self.apply_right_action, self.delete_left_action, self.delete_right_action):
            action.setEnabled(selected)
        modifier = "Command+Option" if sys.platform == "darwin" else "Ctrl+Alt"
        self.previous_action.setToolTip(f"Previous difference ({modifier}+Up)")
        self.next_action.setToolTip(f"Next difference ({modifier}+Down)")
        if self._append_mode:
            self.apply_left_action.setToolTip(f"Append selected right block below left ({modifier}+Shift+Left)")
            self.apply_right_action.setToolTip(f"Append selected left block below right ({modifier}+Shift+Right)")
        else:
            self.apply_left_action.setToolTip(f"Copy selected right block to left ({modifier}+Left, Shift to append below)")
            self.apply_right_action.setToolTip(f"Copy selected left block to right ({modifier}+Right, Shift to append below)")
        self._set_transfer_icons(self._append_mode)
        if self._native_toolbar_bridge is not None:
            self._native_toolbar_bridge.sync()

    def _set_append_mode(self, on: bool):
        if on != self._append_mode:
            self._append_mode = on
            self.gutter.set_append(on)
            self._refresh_toolbar()

    def _set_transfer_icons(self, append: bool):
        for action, identifier, kind in (
            (self.apply_left_action, "diffly.apply_left", "left"),
            (self.apply_right_action, "diffly.apply_right", "right"),
        ):
            kind = f"{'append' if append else 'apply'}_{kind}"
            if self._toolbar_icons[action] == kind:
                continue
            self._toolbar_icons[action] = kind
            action.setIcon(toolbar_icon(kind, self.config.themes[self._active_theme_name]["text"]))
            if self._native_toolbar_bridge is not None:
                self._native_toolbar_bridge.set_symbol(identifier, kind)

    def _apply_theme(self, name: str):
        if name not in self.config.themes:
            name = "gruvbox"
        self._active_theme_name = name
        theme = self.config.themes[name]
        apply_theme(QApplication.instance(), theme)
        self.settings_page.set_choice_theme(theme)
        self.settings_page.align_control_heights()
        self._refresh_stats()
        self._refresh_directory_stats()
        for action, kind in self._toolbar_icons.items():
            action.setIcon(toolbar_icon(kind, theme["text"]))
        for action, kind in self._merge_toolbar_icons.items():
            action.setIcon(toolbar_icon(kind, theme["text"]))
        font = editor_font(self.config.settings)
        tab_width = max(1, int(self.config.settings.get("tab_width", 4)))
        indent_guide_width = max(1, int(self.config.settings.get("indent_guide_width", 4)))
        for editor in (self.left, self.right):
            editor.set_font(font, tab_width, indent_guide_width)
            editor.set_theme(theme)
        self.merge_view.set_appearance(theme, font, tab_width, indent_guide_width)
        self.overview.set_theme(theme)
        self.gutter.set_theme(theme)
        self.directory_comparison.set_theme(theme)
        if self._native_toolbar_bridge is not None:
            self._native_toolbar_bridge.set_theme(theme)
        if self._diff is not None:
            self.left.show_hunks(self._diff.hunks, True, self._diff.left_spans)
            self.right.show_hunks(self._diff.hunks, False, self._diff.right_spans)

    def _choose_theme(self, name: str):
        self._apply_theme(name)
        self.config.settings["theme"] = name
        try:
            save_selected_theme(name)
        except OSError as exc:
            self.statusBar().showMessage(f"Theme applied but not saved: {exc}")

    def _set_editor_display_options(self, line_numbers: bool, syntax_highlighting: bool):
        self.merge_view.set_line_numbers_visible(line_numbers)
        self.merge_view.set_syntax_highlighting_enabled(syntax_highlighting)
        for editor in (self.left, self.right):
            editor.editor.set_line_numbers_visible(line_numbers)
        self.syntax_highlighting_enabled = syntax_highlighting
        if syntax_highlighting:
            for side, editor in (("left", self.left), ("right", self.right)):
                self._request_syntax(side, editor)
        else:
            for timer in self._syntax_timers.values():
                timer.stop()
            for editor in (self.left, self.right):
                editor.highlighter.set_spans(())
        self._update_horizontal_scroll_range()
        self.gutter.update()
        self.config.settings["show_line_numbers"] = line_numbers
        self.config.settings["syntax_highlighting"] = syntax_highlighting
        try:
            save_editor_display_options(line_numbers, syntax_highlighting)
        except OSError as exc:
            self.statusBar().showMessage(f"Editor display options applied but not saved: {exc}")

    def _set_font_size(self, size: int):
        self.config.settings["font_size"] = size
        self._apply_theme(self._active_theme_name)
        try:
            save_font_size(size)
        except OSError as exc:
            self.statusBar().showMessage(f"Font size applied but not saved: {exc}")

    def _set_directory_filters(self, patterns: list[str], use_gitignore: bool,
                               enabled_presets: tuple[str, ...]):
        self.config.settings["ignore_patterns"] = patterns
        self.config.settings["use_gitignore"] = use_gitignore
        self.config.settings["ignore_presets"] = list(enabled_presets)
        self.directory_comparison.set_filters(patterns, use_gitignore, enabled_presets)
        try:
            save_directory_filters(patterns, use_gitignore, enabled_presets)
        except OSError as exc:
            self.statusBar().showMessage(f"Directory filters applied but not saved: {exc}")

    def _open_directory_files(self, left: Path, right: Path):
        if any(id(editor) in self._busy_files for editor in (self.left, self.right)):
            return
        if not all(self._confirm_discard(editor) for editor in (self.left, self.right)):
            return
        self.tabs.setCurrentWidget(self.file_comparison)
        self.open_path(self.left, left)
        self.open_path(self.right, right)

    def _refresh_current(self):
        if self.tabs.currentWidget() is self.directory_comparison:
            if not self._directory_copy_busy:
                self.directory_comparison.compare()
            return
        if self._busy_files:
            return
        loaded = [editor for editor in (self.left, self.right) if editor.state.path is not None]
        if not all(self._confirm_discard(editor) for editor in loaded):
            return
        if not loaded:
            self.debounce.stop()
            self._request_diff()
            return
        for editor in loaded:
            self.open_path(editor, editor.state.path)

    def _tab_changed(self, index: int):
        files_active = self.tabs.widget(index) is self.file_comparison
        directories_active = self.tabs.widget(index) is self.directory_comparison
        merge_active = self.tabs.widget(index) is self.merge_view
        if not files_active:
            self.find_panel.hide()
        if not merge_active:
            self.merge_find_panel.hide()
        for action in self.find_actions:
            action.setEnabled(files_active or merge_active)
        for action in self.merge_actions:
            action.setEnabled(merge_active)
        for widget in (self.summary_label, self.encoding_label):
            widget.setVisible(files_active)
        self.merge_view.footer.setVisible(merge_active)
        self.merge_view.encoding_button.setVisible(merge_active)
        self.directory_summary_label.setVisible(directories_active)
        if self._native_toolbar_bridge is None:
            self.toolbar.setVisible(files_active or directories_active)
            self.merge_toolbar.setVisible(merge_active)
        else:
            self.toolbar.hide()
            self.merge_toolbar.hide()
            if merge_active and self._merge_native_toolbar_bridge is not None:
                self._merge_native_toolbar_bridge.activate()
            elif files_active or directories_active:
                self._native_toolbar_bridge.activate()
            else:
                self._native_toolbar_bridge.set_visible(False)
                if self._merge_native_toolbar_bridge is not None:
                    self._merge_native_toolbar_bridge.set_visible(False)
            if merge_active and self._merge_native_toolbar_bridge is None:
                self.merge_toolbar.show()
        if files_active:
            self._refresh_titles()
        elif directories_active:
            self.setWindowTitle("Directories — Diffly")
        elif merge_active:
            self.setWindowTitle("Merge — Diffly")
        else:
            self.setWindowTitle("Settings — Diffly")
        if self._native_toolbar_bridge is not None:
            self._native_toolbar_bridge.native_window.setTitle_(self.windowTitle())
        self.statusBar().showMessage(
            "Open a file in each pane to compare" if files_active
            else self.directory_comparison.message if directories_active else ""
        )
        self._refresh_toolbar()
        self._refresh_merge_toolbar()

    def _set_indent_width(self, width: int):
        self.config.settings["tab_width"] = width
        self.config.settings["indent_guide_width"] = width
        self.settings_page.set_indent_width(width)
        font = editor_font(self.config.settings)
        for editor in (self.left, self.right):
            editor.set_font(font, width, width)
        self.merge_view.set_appearance(self.config.themes[self._active_theme_name], font, width, width)
        self._update_horizontal_scroll_range()
        try:
            save_indent_width(width)
        except OSError as exc:
            self.statusBar().showMessage(f"Tab width changed but not saved: {exc}")

    def _sync_scroll(self, source: EditorWidget, target: EditorWidget, value: int):
        if self._syncing_scroll:
            return
        self._syncing_scroll = True
        try:
            row = source.editor.visual_row(value) if value else 0
            self.shared_scrollbar.setValue(row)
            target.editor.verticalScrollBar().setValue(target.editor.block_for_visual_row(row))
        finally:
            self._syncing_scroll = False

    def _scroll_both(self, value: int):
        if self._syncing_scroll:
            return
        self._syncing_scroll = True
        try:
            self.left.editor.verticalScrollBar().setValue(self.left.editor.block_for_visual_row(value))
            self.right.editor.verticalScrollBar().setValue(self.right.editor.block_for_visual_row(value))
        finally:
            self._syncing_scroll = False

    def _update_shared_scroll_range(self, *_):
        editors = (self.left.editor, self.right.editor)
        bars = tuple(editor.verticalScrollBar() for editor in editors)
        self.shared_scrollbar.setRange(0, max(
            editor.visual_row(bar.maximum()) for editor, bar in zip(editors, bars)
        ))
        self.shared_scrollbar.setPageStep(max(bar.pageStep() for bar in bars))

    def _scroll_both_horizontal(self, value: int):
        if self._syncing_horizontal_scroll:
            return
        self._syncing_horizontal_scroll = True
        try:
            self.shared_horizontal_scrollbar.setValue(value)
            for editor in (self.left, self.right):
                editor.editor.horizontalScrollBar().setValue(value)
        finally:
            self._syncing_horizontal_scroll = False

    def _update_horizontal_scroll_range(self, *_):
        bars = (self.left.editor.horizontalScrollBar(), self.right.editor.horizontalScrollBar())
        shared = self.shared_horizontal_scrollbar
        shared.setRange(0, max(bar.maximum() for bar in bars))
        shared.setPageStep(min(bar.pageStep() for bar in bars))
        shared.setSingleStep(bars[0].singleStep())
        shared.setVisible(shared.maximum() > 0)
        self._scroll_both_horizontal(shared.value())

    def eventFilter(self, watched, event):
        if (watched in (self.left.editor, self.right.editor)
                and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress)
                and event.modifiers() & ~Qt.KeyboardModifier.ShiftModifier
                == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            append = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            commands = {
                Qt.Key.Key_Up: lambda: self._navigate(-1),
                Qt.Key.Key_Down: lambda: self._navigate(1),
                Qt.Key.Key_Left: lambda: self._transfer(self._selected_hunk, False, append),
                Qt.Key.Key_Right: lambda: self._transfer(self._selected_hunk, True, append),
            }
            command = commands.get(event.key())
            if command is not None:
                if event.type() == QEvent.Type.KeyPress:
                    command()
                event.accept()
                return True
        if (event.type() == QEvent.Type.Wheel
                and watched in (self.left.editor.viewport(), self.right.editor.viewport(), self.shared_horizontal_scrollbar)):
            horizontal = (watched is self.shared_horizontal_scrollbar
                          or abs(event.pixelDelta().x()) > abs(event.pixelDelta().y())
                          or abs(event.angleDelta().x()) > abs(event.angleDelta().y())
                          or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            bar = self.shared_horizontal_scrollbar if horizontal else self.shared_scrollbar
            if horizontal and not event.pixelDelta().isNull():
                delta = event.pixelDelta().x() or event.pixelDelta().y()
                bar.setValue(bar.value() - delta)
                event.accept()
                return True
            if watched is bar:
                return False
            QApplication.sendEvent(bar, event)
            return True
        if self.toolbar is not None and watched is self.toolbar and event.type() == QEvent.Type.MouseButtonDblClick:
            if self.isMaximized():
                self.showNormal()
            else:
                self.showMaximized()
            return True
        if event.type() == QEvent.Type.FocusIn:
            if watched is self.left.editor:
                self._last_active_side = "left"
                if self.find_panel.isVisible():
                    self.find_panel.set_target(self.left)
                self._refresh_encoding_label()
            elif watched is self.right.editor:
                self._last_active_side = "right"
                if self.find_panel.isVisible():
                    self.find_panel.set_target(self.right)
                self._refresh_encoding_label()
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "darwin" and QApplication.instance().platformName() == "cocoa":
            QTimer.singleShot(0, self._configure_native_toolbar)

    def _configure_native_toolbar(self):
        if self._native_toolbar_bridge is not None:
            return
        actions = {
            "diffly.refresh": (self.refresh_action, "refresh"),
            "diffly.previous": (self.previous_action, "up"),
            "diffly.next": (self.next_action, "down"),
            "diffly.apply_left": (self.apply_left_action, "apply_left"),
            "diffly.apply_right": (self.apply_right_action, "apply_right"),
            "diffly.paste": (self.paste_action, "paste"),
        }
        bridge = install_inline_toolbar(self, actions, "com.diffly.files.toolbar")
        if bridge is not None:
            self._native_toolbar_bridge = bridge
            self.toolbar.hide()
            if self.merge_enabled:
                merge_actions = {
                    "diffly.merge.previous": (self.merge_previous_action, "up"),
                    "diffly.merge.next": (self.merge_next_action, "down"),
                    "diffly.merge.left": (self.merge_use_left_action, "apply_right"),
                    "diffly.merge.right": (self.merge_use_right_action, "apply_left"),
                }
                self._merge_native_toolbar_bridge = install_inline_toolbar(
                    self, merge_actions, "com.diffly.merge.toolbar"
                )
            self._tab_changed(self.tabs.currentIndex())
            self._native_toolbar_attempts = 0
            return
        self._native_toolbar_attempts += 1
        if self._native_toolbar_attempts < 4:
            QTimer.singleShot(100, self._configure_native_toolbar)

    def focused_editor(self):
        if self.tabs.currentWidget() is self.merge_view:
            return self.merge_view.result.editor
        if self.right.editor.hasFocus():
            return self.right.editor
        if self.left.editor.hasFocus():
            return self.left.editor
        return self.left.editor if self._last_active_side == "left" else self.right.editor

    def _paste_current(self):
        if self.tabs.currentWidget() is not self.file_comparison:
            return
        editor = self.focused_editor()
        editor.paste()
        editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def _refresh_titles(self):
        for editor, label, side in ((self.left, self.left_title, "Left"), (self.right, self.right_title, "Right")):
            name = str(editor.state.path) if editor.state.path else "Untitled"
            label.setText(f"{side}: {name}{' *' if editor.state.dirty else ''}")
        self._refresh_encoding_label()
        if self.tabs.currentWidget() is self.file_comparison:
            left = self.left.state.path.name if self.left.state.path else "Untitled"
            right = self.right.state.path.name if self.right.state.path else "Untitled"
            self.setWindowTitle(f"{left} ↔ {right} — Diffly" if self.left.state.path or self.right.state.path else "Diffly")

    def _active_pane(self) -> EditorWidget:
        return self.left if self._last_active_side == "left" else self.right

    def _refresh_encoding_label(self):
        state = self._active_pane().state
        label = next((name for name, value in ENCODINGS if value == state.encoding), state.encoding.upper())
        if state.bom:
            label += " BOM"
        self.encoding_label.setText(f"Encoding: {label}")

    def _choose_encoding(self, encoding: str | None):
        editor = self._active_pane()
        if id(editor) in self._busy_files:
            return
        if editor.state.path is None:
            editor.state.encoding = encoding or "utf-8"
            editor.state.bom = False
            self._refresh_encoding_label()
            return
        if not self._confirm_discard(editor):
            return
        self.open_path(editor, editor.state.path, encoding)

    def _refresh_stats(self):
        if self._diff is None:
            self.summary_label.setText("")
            return
        theme = self.config.themes[self._active_theme_name]
        stats = self._diff.stats
        self.summary_label.setText(
            f'<span style="color:{theme["overview_removed"]}">●</span> {stats.deleted} deleted  '
            f'<span style="color:{theme["overview_added"]}">●</span> {stats.added} added  '
            f'<span style="color:{theme["overview_replace"]}">●</span> {stats.changed} changed  '
            f'<span style="color:{theme["muted"]}">●</span> {stats.identical} identical'
        )

    def _set_directory_stats(self, stats):
        self._directory_stats = stats
        self._refresh_directory_stats()

    def _refresh_directory_stats(self):
        if not hasattr(self, "directory_summary_label"):
            return
        stats = self._directory_stats
        if stats is None:
            self.directory_summary_label.setText("")
            return
        theme = self.config.themes[self._active_theme_name]
        self.directory_summary_label.setText(
            f'<span style="color:{theme["overview_replace"]}">●</span> {stats["different"]} different  '
            f'<span style="color:{theme["overview_removed"]}">●</span> {stats["left_only"]} left only  '
            f'<span style="color:{theme["overview_added"]}">●</span> {stats["right_only"]} right only  '
            f'<span style="color:{theme["muted"]}">●</span> {stats["same"]} same  '
            f'<span style="color:{theme["overview_replace"]}">●</span> {stats["changed_folders"]} folders changed'
        )

    def _directory_message_changed(self, message: str):
        if self.tabs.currentWidget() is self.directory_comparison:
            self.statusBar().showMessage(message)

    def _documents_changed(self, editor: EditorWidget):
        self._diff = None
        self._selected_hunk = -1
        self._refresh_toolbar()
        # Keep the old alignment and colors until the debounced diff is ready.
        # Transfer actions are disabled while _diff is None.
        self._refresh_titles()
        self.debounce.start()
        if hasattr(self, "_syntax_timers"):
            self._syntax_timers[editor].start()

    def _request_syntax(self, side: str, editor: EditorWidget):
        text = editor.text()
        language = language_for(editor.state.path, text[:512])
        editor.set_syntax_language(language)
        if not self.syntax_highlighting_enabled or language is None or len(text) > 2_000_000:
            editor.highlighter.set_spans(())
            return
        self.scheduler.submit_syntax(side, text, language, editor.state.revision)

    def _accept_syntax(self, result):
        side, revision, language, spans = result
        if side.startswith("merge-"):
            self.merge_view.accept_syntax(side, revision, language, spans)
            return
        editor = self.left if side == "left" else self.right
        if (not self.syntax_highlighting_enabled or editor.state.revision != revision
                or editor.syntax_language != language):
            return
        editor.highlighter.set_spans(spans)

    def _request_diff(self):
        self.statusBar().showMessage("Comparing…")
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
        self._diff = result
        self._selected_hunk = -1
        self._refresh_stats()
        self.left.show_hunks(result.hunks, True, result.left_spans)
        self.right.show_hunks(result.hunks, False, result.right_spans)
        self._update_shared_scroll_range()
        self.overview.set_hunks(result.hunks)
        self.gutter.set_hunks(result.hunks)
        self._cursor_hunk_changed(self.left if self._last_active_side == "left" else self.right)
        self.statusBar().showMessage(f"{len(result.hunks)} difference(s)")

    def _accept_merge(self, result):
        self.merge_view.accept_merge(result)

    def _merge_error(self, message: str):
        self.merge_view.merge_failed(message)

    def _diff_error(self, message: str):
        self.statusBar().showMessage(f"Comparison failed: {message}")

    def _cursor_hunk_changed(self, editor: EditorWidget):
        if self._updating_hunk_selection or self._diff is None:
            return
        line = editor.editor.textCursor().blockNumber()
        self._selected_hunk = -1
        for index, hunk in enumerate(self._diff.hunks):
            start, end = (hunk.left_start, hunk.left_end) if editor is self.left else (hunk.right_start, hunk.right_end)
            if start <= line < end:
                self._selected_hunk = index
                break
        self._refresh_toolbar()

    def _select_hunk(self, row: int):
        if self._diff is None or not (0 <= row < len(self._diff.hunks)):
            return
        hunk = self._diff.hunks[row]
        left_column = next((span.start for span in self._diff.left_spans if span.line == hunk.left_start), 0)
        right_column = next((span.start for span in self._diff.right_spans if span.line == hunk.right_start), 0)
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
        self._selected_hunk = row
        active = self.left if self._last_active_side == "left" else self.right
        active.editor.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._refresh_toolbar()
        modifier = "Cmd+Option" if sys.platform == "darwin" else "Ctrl+Alt"
        self.statusBar().showMessage(f"Difference {row + 1} of {len(self._diff.hunks)} · {modifier}+← / → to apply")

    def _navigate(self, step: int):
        if self.tabs.currentWidget() is self.directory_comparison:
            if not self._directory_copy_busy:
                self.directory_comparison.navigate(step)
            return
        if self.tabs.currentWidget() is not self.file_comparison:
            return
        if self._diff is None or not self._diff.hunks:
            return
        current = self._selected_hunk
        if current < 0:
            row = 0 if step > 0 else len(self._diff.hunks) - 1
        else:
            row = (current + step) % len(self._diff.hunks)
        self._select_hunk(row)

    def _transfer(self, row: int, source_is_left: bool, append: bool = False):
        if self.tabs.currentWidget() is self.directory_comparison:
            self._copy_selected_entry(to_left=not source_is_left)
            return
        if self.tabs.currentWidget() is not self.file_comparison:
            return
        result = self._diff
        if result is None or not (0 <= row < len(result.hunks)):
            return
        if (result.left_revision, result.right_revision) != (
            self.left.state.revision, self.right.state.revision
        ):
            return
        source = self.left if source_is_left else self.right
        target = self.right if source_is_left else self.left
        new_text = transfer_text(source.text(), target.text(), result.hunks[row], source_is_left, append)
        self._apply_in_place(target, new_text)
        self.statusBar().showMessage(f"Block {'appended' if append else 'copied'}; use Undo to revert")

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
        self._scroll_both_horizontal(horizontal)
        target.editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def _copy_selected_entry(self, to_left: bool):
        view = self.directory_comparison
        if self._directory_copy_busy or not view.can_copy(to_left):
            return
        entry = view.selected_entry()
        source_kind = entry.right_kind if to_left else entry.left_kind
        if source_kind == "directory":
            self._copy_selected_directory(entry, to_left)
            return
        source = entry.right_path if to_left else entry.left_path
        target_root = Path(view.left_path.text() if to_left else view.right_path.text()).expanduser().resolve()
        target = target_root / entry.relative_path
        if any(editor.state.dirty and editor.state.path == target for editor in (self.left, self.right)):
            QMessageBox.warning(self, "Unsaved changes", f"Save or discard changes to {target} before copying.")
            return
        try:
            source_stamp = regular_file_stamp(source)
            target_stamp = regular_file_stamp(target)
            if source_stamp is None:
                raise RuntimeError("Source file no longer exists. Compare the directories again.")
        except (OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "Cannot copy file", str(exc))
            return
        if not self._skip_copy_confirmation:
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Question)
            dialog.setWindowTitle("Copy file")
            dialog.setText(f"{'Replace' if target_stamp is not None else 'Create'} this file?")
            dialog.setInformativeText(f"From: {source}\nTo: {target}")
            dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            dialog.setDefaultButton(QMessageBox.StandardButton.No)
            checkbox = QCheckBox("Don't ask again this session", dialog)
            dialog.setCheckBox(checkbox)
            if dialog.exec() != QMessageBox.StandardButton.Yes:
                return
            if checkbox.isChecked():
                self._skip_copy_confirmation = True
        self._directory_copy_busy = True
        self._refresh_toolbar()
        roots = (view.left_path.text(), view.right_path.text())

        def done(copied):
            self._directory_copy_busy = False
            self.statusBar().showMessage(f"Copied {entry.relative_path} to {'left' if to_left else 'right'}")
            if roots == (view.left_path.text(), view.right_path.text()):
                view.compare()
            self._refresh_toolbar()

        def failed(message):
            self._directory_copy_busy = False
            self._refresh_toolbar()
            QMessageBox.critical(self, "Copy failed", message)

        self.scheduler.submit_file(
            ("directory-copy", id(self)), copy_file_checked,
            (source, target_root, entry.relative_path, source_stamp, target_stamp), done, failed,
        )

    def _copy_selected_directory(self, entry, to_left: bool):
        view = self.directory_comparison
        source = entry.right_path if to_left else entry.left_path
        target_root = Path(view.left_path.text() if to_left else view.right_path.text()).expanduser().resolve()
        target = target_root / entry.relative_path
        for editor in (self.left, self.right):
            path = editor.state.path
            if editor.state.dirty and path is not None and (
                path.is_relative_to(source) or path.is_relative_to(target)
            ):
                QMessageBox.warning(self, "Unsaved changes", "Save or discard edits inside this folder before copying it.")
                return
        try:
            source_stamp = directory_stamp(source)
            target_stamp = path_stamp(target)
            if source_stamp is None:
                raise RuntimeError("Source folder no longer exists. Compare the directories again.")
            if target_stamp is not None and target_stamp[0] not in (stat.S_IFDIR, stat.S_IFREG):
                raise RuntimeError("Destination changed type. Compare the directories again.")
        except (OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "Cannot copy folder", str(exc))
            return
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning if target_stamp is not None else QMessageBox.Icon.Question)
        dialog.setWindowTitle("Replace folder" if target_stamp is not None else "Copy folder")
        dialog.setText("Replace this folder and all its contents?" if target_stamp is not None
                       else "Copy this folder and all its contents?")
        detail = "Files present only at the destination will be removed. " if target_stamp is not None else ""
        dialog.setInformativeText(f"{detail}Hidden and ignored files are included.\nFrom: {source}\nTo: {target}")
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        if dialog.exec() != QMessageBox.StandardButton.Yes:
            return
        self._directory_copy_busy = True
        self._refresh_toolbar()
        roots = (view.left_path.text(), view.right_path.text())

        def done(result):
            _copied, leftover = result
            self._directory_copy_busy = False
            if roots == (view.left_path.text(), view.right_path.text()):
                view.compare()
            self._refresh_toolbar()
            if leftover is not None:
                QMessageBox.warning(self, "Old folder retained", f"Folder copied, but the old folder remains at {leftover}")
            else:
                self.statusBar().showMessage(f"Copied folder {entry.relative_path} to {'left' if to_left else 'right'}")

        def failed(message):
            self._directory_copy_busy = False
            self._refresh_toolbar()
            QMessageBox.critical(self, "Folder copy failed", message)

        self.scheduler.submit_file(
            ("directory-copy", id(self)), copy_directory_checked,
            (source, target_root, entry.relative_path, source_stamp, target_stamp), done, failed,
        )

    def _delete_selected(self, row: int, target_is_left: bool | None = None):
        if self.tabs.currentWidget() is not self.file_comparison:
            return
        result = self._diff
        if result is None or not (0 <= row < len(result.hunks)):
            return
        if (result.left_revision, result.right_revision) != (
            self.left.state.revision, self.right.state.revision
        ):
            return
        if target_is_left is None:
            target_is_left = self._last_active_side == "left"
        target = self.left if target_is_left else self.right
        new_text = delete_hunk_text(target.text(), result.hunks[row], target_is_left)
        if new_text != target.text():
            self._apply_in_place(target, new_text)
            self.statusBar().showMessage("Block deleted; use Undo to revert")

    def _confirm_discard(self, editor: EditorWidget) -> bool:
        if not editor.state.dirty:
            return True
        answer = QMessageBox.question(self, "Unsaved changes", "Discard unsaved changes in this pane?")
        return answer == QMessageBox.StandardButton.Yes

    def _open(self, editor: EditorWidget):
        if id(editor) in self._busy_files or not self._confirm_discard(editor):
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Open text file")
        if not filename:
            return
        self.open_path(editor, Path(filename))

    def open_path(self, editor: EditorWidget, path: Path, encoding: str | None = None):
        if id(editor) in self._busy_files:
            return
        revision = editor.state.revision
        self._busy_files.add(id(editor))
        self._refresh_toolbar()
        self.statusBar().showMessage(f"Opening {path.name}…")
        self._serial += 1
        key = self._serial

        def done(loaded):
            self._busy_files.discard(id(editor))
            self._refresh_toolbar()
            if editor.state.revision != revision:
                self.statusBar().showMessage("Open discarded because the pane changed")
                return
            editor.load_text(loaded.text)
            editor.state.path = path
            editor.state.stamp = loaded.stamp
            editor.state.newline = loaded.newline
            editor.state.encoding = loaded.encoding
            editor.state.bom = loaded.bom
            editor.set_syntax_language(language_for(path, loaded.text[:512]))
            self._syntax_timers[editor].start()
            self._refresh_titles()
            self.statusBar().showMessage(f"Opened {path.name}")
            self._recent_timer.start()

        def failed(message):
            self._busy_files.discard(id(editor))
            self._refresh_toolbar()
            QMessageBox.critical(self, "Open failed", message)

        self.scheduler.submit_file(key, read_file, (path, encoding), done, failed)

    def _save_active(self, save_as: bool = False):
        if self.tabs.currentWidget() is self.file_comparison:
            self._save(self._active_pane(), save_as)
        elif self.tabs.currentWidget() is self.merge_view:
            self.merge_view.save(save_as)

    def _save(self, editor: EditorWidget, save_as: bool = False):
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
        self._busy_files.add(id(editor))
        self._refresh_toolbar()
        self.statusBar().showMessage(f"Saving {path.name}…")
        self._serial += 1
        key = self._serial

        def done(stamp):
            self._busy_files.discard(id(editor))
            self._refresh_toolbar()
            editor.state.path = path
            editor.state.stamp = stamp
            editor.state.saved_revision = revision
            editor.set_syntax_language(language_for(path, editor.text()[:512]))
            self._syntax_timers[editor].start()
            self._refresh_titles()
            self.statusBar().showMessage(f"Saved {path.name}")
            self._recent_timer.start()

        def failed(message):
            self._busy_files.discard(id(editor))
            self._refresh_toolbar()
            QMessageBox.critical(self, "Save failed", message)

        self.scheduler.submit_file(key, write_file, (path, text, newline, bom, expected, encoding), done, failed)

    def closeEvent(self, event: QCloseEvent):
        self.git_merge_success = False
        if self._busy_files or self._directory_copy_busy or self.merge_view.busy:
            QMessageBox.information(self, "Files busy", "Wait for file operations to finish before closing.")
            event.ignore()
            return
        if self.git_mergetool:
            if not self.git_merge_aborted and not self.merge_view.git_merge_complete():
                answer = QMessageBox.question(
                    self, "Incomplete Git merge",
                    "Quit without a saved, resolved Git merge? Git will keep this file conflicted.")
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
        if (any(editor.state.dirty for editor in (self.left, self.right)) or
                (self.merge_view.result.state.dirty and not self.git_mergetool)):
            answer = QMessageBox.question(self, "Unsaved changes", "Quit and discard unsaved changes?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        if self._native_toolbar_bridge is not None:
            self._native_toolbar_bridge.close()
        if self._merge_native_toolbar_bridge is not None:
            self._merge_native_toolbar_bridge.close()
        self.debounce.stop()
        for timer in self._syntax_timers.values():
            timer.stop()
        self.merge_view._syntax_timer.stop()
        self.directory_comparison.cancel()
        self.scheduler.shutdown()
        self.git_merge_success = self.git_mergetool and self.merge_view.git_merge_complete()
        event.accept()
