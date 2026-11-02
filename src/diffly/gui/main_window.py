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
    QLabel,
    QMainWindow,
    QMessageBox,
    QInputDialog,
    QTabWidget,
    QToolBar,
    QWidget,
)

from diffly.core.diff.engine import CompareOptions
from diffly.core.directories.filters import DEFAULT_PRESETS, PRESETS
from diffly.gui.directory_comparison import DirectoryComparisonView
from diffly.gui.file_comparison import FileComparisonView
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
from diffly.services.directory_service import (
    copy_directory_checked, copy_file_checked, directory_stamp, path_stamp, regular_file_stamp,
)
from diffly.services.task_scheduler import TaskScheduler
from diffly.services.session_store import load_sessions, remember, save_sessions

# Tooltip name of the Ctrl+Alt shortcut prefix (Qt maps Ctrl to Command on macOS).
NAV_MODIFIER = "Command+Option" if sys.platform == "darwin" else "Ctrl+Alt"

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
        self.syntax_highlighting_enabled = bool(self.config.settings.get("syntax_highlighting", True))
        line_numbers_visible = bool(self.config.settings.get("show_line_numbers", False))
        self._directory_copy_busy = False
        self._skip_copy_confirmation = False
        self._native_toolbar_attempts = 0
        self._native_toolbar_bridge = None
        self._merge_native_toolbar_bridge = None
        self._settings_native_toolbar_bridge = None
        self._append_mode = False
        QApplication.instance().installEventFilter(_ShiftWatcher(self._set_append_mode, self))

        self.files = FileComparisonView(
            self.scheduler, self.compare_options, self,
            line_numbers_visible=line_numbers_visible,
            syntax_highlighting_enabled=self.syntax_highlighting_enabled,
        )
        self.files.state_changed.connect(self._refresh_toolbar)
        self.files.status_changed.connect(self.statusBar().showMessage)
        self.files.paths_changed.connect(self._files_paths_changed)
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
        self.directory_comparison.copy_requested.connect(self._copy_directory_entry)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("ComparisonTabs")
        self.tabs.addTab(self.directory_comparison, "Directories")
        self.tabs.addTab(self.files, "Files")
        self.merge_view = MergeView(
            self.scheduler, self,
            line_numbers_visible=line_numbers_visible,
            syntax_highlighting_enabled=self.syntax_highlighting_enabled,
            git_mergetool=git_mergetool,
        )
        self.merge_find_panel = FindPanel(self.merge_view)
        self.merge_find_panel.set_target(self.merge_view.result)
        self.merge_view.layout().addWidget(self.merge_find_panel)
        self.merge_view.compare_options = self.compare_options
        if merge_enabled:
            self.tabs.addTab(self.merge_view, "Merge")
        else:
            self.merge_view.hide()
        self.merge_view.paths_changed.connect(self._remember_merge)
        self.merge_view.paths_changed.connect(self._refresh_merge_title)
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

        self._make_menu()
        self._make_toolbar()
        self._make_merge_toolbar()
        self._make_settings_toolbar()
        self.merge_view.state_changed.connect(self._refresh_merge_toolbar)
        self._recent_timer = QTimer(self)
        self._recent_timer.setSingleShot(True)
        self._recent_timer.setInterval(700)
        self._recent_timer.timeout.connect(self._remember_files)
        self.directory_summary_label = QLabel()
        self.statusBar().addPermanentWidget(self.files.footer)
        self.statusBar().addPermanentWidget(self.directory_summary_label)
        self.statusBar().addPermanentWidget(self.files.encoding_button)
        self._apply_theme(self.config.settings.get("theme", "gruvbox"))
        self._refresh_toolbar()
        self.statusBar().showMessage("Open a file in each pane to compare")
        self._tab_changed(self.tabs.currentIndex())

    def _make_menu(self):
        menu_bar = self._linux_titlebar.menu_bar if self._linux_titlebar is not None else self.menuBar()
        file_menu = menu_bar.addMenu("File")
        self.file_actions = {}
        for label, shortcut, callback in (
            ("Open Left…", "Ctrl+O", lambda: self.files.open_dialog(self.files.left)),
            ("Open Right…", "Ctrl+Shift+O", lambda: self.files.open_dialog(self.files.right)),
            ("Save", "Ctrl+S", self._save_active),
            ("Save As…", None, lambda: self._save_active(True)),
        ):
            action = QAction(label, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            file_menu.addAction(action)
            self.file_actions[label] = action
        file_menu.addSeparator()
        self.save_comparison_action = file_menu.addAction("Save Comparison…", self._save_comparison)
        self.saved_menu = file_menu.addMenu("Saved Comparisons")
        self.recent_menu = file_menu.addMenu("Recent Comparisons")
        self._refresh_session_menus()
        file_menu.addSeparator()
        self.merge_recreate_action = file_menu.addAction("Recreate Merge Result", self.merge_view.recreate)
        self.merge_reload_action = file_menu.addAction("Reload Merge Sources", self.merge_view.reload_sources)
        edit_menu = menu_bar.addMenu("Edit")
        self.undo_redo_actions = []
        for label, shortcut, operation in (
            ("Undo", "Ctrl+Z", "undo"),
            ("Redo", "Ctrl+Shift+Z", "redo"),
        ):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda checked=False, op=operation: getattr(self.focused_editor(), op)())
            edit_menu.addAction(action)
            self.undo_redo_actions.append(action)
        edit_menu.addSeparator()
        self.merge_actions = []
        for label, shortcut, callback in (
            ("Previous conflict", ("Ctrl+Alt+Up", "Shift+F7"), lambda: self.merge_view.navigate(-1)),
            ("Next conflict", ("Ctrl+Alt+Down", "F7"), lambda: self.merge_view.navigate(1)),
            ("Use Left", "Ctrl+Alt+1", lambda: self.merge_view.use("left")),
            ("Use Right", "Ctrl+Alt+2", lambda: self.merge_view.use("right")),
            ("Use Base", "Ctrl+Alt+3", lambda: self.merge_view.use("base")),
            ("Mark Resolved", "Ctrl+Alt+0", self.merge_view.mark_resolved),
        ):
            action = QAction(label, self)
            action.setShortcuts([QKeySequence(key) for key in (
                shortcut if isinstance(shortcut, tuple) else (shortcut,))])
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
        self.comparison_actions = {}
        for label, shortcut, callback in (
            ("Next Difference", "Ctrl+Alt+Down", lambda: self._navigate(1)),
            ("Previous Difference", "Ctrl+Alt+Up", lambda: self._navigate(-1)),
            ("Apply Right to Left", "Ctrl+Alt+Left", lambda: self._transfer(False)),
            ("Apply Left to Right", "Ctrl+Alt+Right", lambda: self._transfer(True)),
            ("Append Right Below Left", "Ctrl+Alt+Shift+Left", lambda: self._transfer(False, True)),
            ("Append Left Below Right", "Ctrl+Alt+Shift+Right", lambda: self._transfer(True, True)),
            ("Delete Selected Block", "Ctrl+Alt+Delete", lambda: self._delete_selected()),
        ):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            edit_menu.addAction(action)
            self.comparison_actions[label] = action

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
        if self.tabs.currentWidget() is self.files:
            return self.files.find_panel, self.files.active_pane()
        if self.tabs.currentWidget() is self.merge_view:
            return self.merge_find_panel, self.merge_view.result
        return None, None

    def _set_comparison_options(self, options: CompareOptions):
        self.compare_options = options
        self.files.set_compare_options(options)
        self.merge_view.set_compare_options(options)
        values = {key: getattr(options, key) for key in
                  ("ignore_whitespace", "ignore_trailing_whitespace", "ignore_case")}
        self.config.settings.update(values)
        try:
            save_comparison_options(options)
        except OSError as exc:
            self.statusBar().showMessage(f"Comparison settings applied but not saved: {exc}")

    def _comparison_entry(self):
        if self.tabs.currentWidget() is self.settings_page:
            return None
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
            left = str(self.files.left.state.path or "")
            right = str(self.files.right.state.path or "")
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

    def _files_paths_changed(self):
        self._recent_timer.start()
        if self.tabs.currentWidget() is self.files:
            self.setWindowTitle(self.files.window_title())

    def _remember_files(self):
        left, right = self.files.left.state.path, self.files.right.state.path
        if self.files.busy or left is None or right is None:
            return
        entry = {"kind": "files", "left": str(left.absolute()),
                 "right": str(right.absolute()),
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
        if self.files.busy or self._directory_copy_busy:
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
            if not self.files.confirm_replace():
                return
            self.tabs.setCurrentWidget(self.files)
            options = entry.get("options", {})
            self.compare_options = CompareOptions(**{
                key: bool(options.get(key, False)) for key in
                ("ignore_whitespace", "ignore_trailing_whitespace", "ignore_case")
            })
            self.settings_page.set_comparison_options(self.compare_options)
            self.files.set_compare_options(self.compare_options)
            self.files.open_path(self.files.left, paths[0])
            self.files.open_path(self.files.right, paths[1])
        else:
            self.tabs.setCurrentWidget(self.directory_comparison)
            self.directory_comparison.left_path.setText(str(paths[0]))
            self.directory_comparison.right_path.setText(str(paths[1]))
            self.directory_comparison.compare()

    def _new_toolbar(self, title: str) -> QToolBar:
        toolbar = QToolBar(title, self._linux_titlebar or self)
        toolbar.setObjectName("DifflyToolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        if self._linux_titlebar is None:
            self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        else:
            self._linux_titlebar.add_toolbar(toolbar)
        return toolbar

    def _toolbar_action(self, toolbar: QToolBar, icons: dict, label: str, kind: str,
                        tip: str, callback) -> QAction:
        action = QAction(label, self)
        action.setToolTip(tip)
        action.triggered.connect(callback)
        toolbar.addAction(action)
        icons[action] = kind
        return action

    @staticmethod
    def _toolbar_spacer(toolbar: QToolBar):
        spacer = QWidget(toolbar)
        spacer.setFixedWidth(12)
        toolbar.addWidget(spacer)

    def _make_toolbar(self):
        modifier = NAV_MODIFIER
        toolbar = self._new_toolbar("Diff controls")
        self._toolbar_icons = {}

        def add(label, icon_kind, tip, callback):
            return self._toolbar_action(toolbar, self._toolbar_icons, label, icon_kind, tip, callback)

        self.refresh_action = add("Refresh", "refresh", "Refresh directory comparison", self._refresh_current)
        self._toolbar_spacer(toolbar)
        self.previous_action = add("Previous", "up", f"Previous difference ({modifier}+Up)", lambda: self._navigate(-1))
        self.next_action = add("Next", "down", f"Next difference ({modifier}+Down)", lambda: self._navigate(1))
        self._toolbar_spacer(toolbar)
        self.apply_left_action = add("Apply ←", "apply_left", f"Copy selected right block to left ({modifier}+Left)", lambda: self._transfer(False, _shift_held()))
        self.apply_right_action = add("Apply →", "apply_right", f"Copy selected left block to right ({modifier}+Right)", lambda: self._transfer(True, _shift_held()))
        self._toolbar_spacer(toolbar)
        self.paste_action = add("Paste", "paste", "Paste clipboard into the active editor", self._paste_current)
        # Keep side-specific delete actions available to callers without
        # showing destructive controls in the compact toolbar.
        self.delete_left_action = QAction("Delete L", self)
        self.delete_left_action.triggered.connect(lambda: self._delete_selected(True))
        self.delete_right_action = QAction("Delete R", self)
        self.delete_right_action.triggered.connect(lambda: self._delete_selected(False))
        self.toolbar = toolbar
        toolbar.installEventFilter(self)

    def _make_merge_toolbar(self):
        toolbar = self._new_toolbar("Merge controls")
        self._merge_toolbar_icons = {}

        def add(label, kind, tip, callback):
            return self._toolbar_action(toolbar, self._merge_toolbar_icons, label, kind, tip, callback)

        modifier = NAV_MODIFIER
        self.merge_refresh_action = add("Refresh", "refresh", "Reload merge sources from disk and merge again",
                                        self.merge_view.reload_sources)
        self._toolbar_spacer(toolbar)
        self.merge_previous_action = add("Previous conflict", "up", f"Previous conflict ({modifier}+Up)",
                                         lambda: self.merge_view.navigate(-1))
        self.merge_next_action = add("Next conflict", "down", f"Next conflict ({modifier}+Down)",
                                     lambda: self.merge_view.navigate(1))
        self._toolbar_spacer(toolbar)
        self.merge_use_left_action = add("Use Left", "apply_right", f"Use Left for selected conflict ({modifier}+1)",
                                         lambda: self.merge_view.use("left"))
        self.merge_use_right_action = add("Use Right", "apply_left", f"Use Right for selected conflict ({modifier}+2)",
                                          lambda: self.merge_view.use("right"))
        self.merge_use_base_action = add("Use Base", "base", f"Use Base for selected conflict ({modifier}+3)",
                                         lambda: self.merge_view.use("base"))
        self.merge_resolved_action = add("Mark Resolved", "check", f"Mark edited conflict as resolved ({modifier}+0)",
                                         self.merge_view.mark_resolved)
        self.merge_toolbar = toolbar
        toolbar.hide()
        self._refresh_merge_toolbar()

    def _make_settings_toolbar(self):
        self.settings_toolbar = self._new_toolbar("Settings controls")
        self.settings_toolbar.hide()

    def _refresh_merge_toolbar(self):
        if not hasattr(self, "merge_toolbar"):
            return
        view = self.merge_view
        for action, key in ((self.merge_previous_action, "Previous conflict"),
                            (self.merge_next_action, "Next conflict"),
                            (self.merge_refresh_action, "Reload Sources"),
                            (self.merge_use_base_action, "Use Base"),
                            (self.merge_resolved_action, "Mark Resolved")):
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
            self._sync_comparison_actions()
            if self._native_toolbar_bridge is not None:
                self._native_toolbar_bridge.sync()
            return
        files_active = self.tabs.currentWidget() is self.files
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
            modifier = NAV_MODIFIER
            self.previous_action.setToolTip(f"Previous different file ({modifier}+Up)")
            self.next_action.setToolTip(f"Next different file ({modifier}+Down)")
            entry = view.selected_entry()
            left_kind = "folder" if entry is not None and entry.right_kind == "directory" else "file"
            right_kind = "folder" if entry is not None and entry.left_kind == "directory" else "file"
            self.apply_left_action.setToolTip(f"Copy selected {left_kind} from right to left ({modifier}+Left)")
            self.apply_right_action.setToolTip(f"Copy selected {right_kind} from left to right ({modifier}+Right)")
            self._set_transfer_icons(False)
            self._sync_comparison_actions()
            if self._native_toolbar_bridge is not None:
                self._native_toolbar_bridge.sync()
            return
        diff = self.files.diff
        has_hunks = diff is not None and bool(diff.hunks)
        self.refresh_action.setEnabled(not self.files.busy)
        self.refresh_action.setToolTip("Reload open files from disk and compare")
        selected = has_hunks and 0 <= self.files.selected_hunk < len(diff.hunks)
        self.previous_action.setEnabled(has_hunks)
        self.next_action.setEnabled(has_hunks)
        self.paste_action.setEnabled(files_active)
        for action in (self.apply_left_action, self.apply_right_action, self.delete_left_action, self.delete_right_action):
            action.setEnabled(selected)
        modifier = NAV_MODIFIER
        self.previous_action.setToolTip(f"Previous difference ({modifier}+Up)")
        self.next_action.setToolTip(f"Next difference ({modifier}+Down)")
        if self._append_mode:
            self.apply_left_action.setToolTip(f"Append selected right block below left ({modifier}+Shift+Left)")
            self.apply_right_action.setToolTip(f"Append selected left block below right ({modifier}+Shift+Right)")
        else:
            self.apply_left_action.setToolTip(f"Copy selected right block to left ({modifier}+Left, Shift to append below)")
            self.apply_right_action.setToolTip(f"Copy selected left block to right ({modifier}+Right, Shift to append below)")
        self._set_transfer_icons(self._append_mode)
        self._sync_comparison_actions()
        if self._native_toolbar_bridge is not None:
            self._native_toolbar_bridge.sync()

    def _sync_comparison_actions(self):
        files_active = self.tabs.currentWidget() is self.files
        directories_active = self.tabs.currentWidget() is self.directory_comparison
        for label, source in (
            ("Next Difference", self.next_action),
            ("Previous Difference", self.previous_action),
            ("Apply Right to Left", self.apply_left_action),
            ("Apply Left to Right", self.apply_right_action),
        ):
            self.comparison_actions[label].setEnabled(
                (files_active or directories_active) and source.isEnabled())
        for label in ("Append Right Below Left", "Append Left Below Right", "Delete Selected Block"):
            self.comparison_actions[label].setEnabled(
                files_active and self.apply_left_action.isEnabled())

    def _set_append_mode(self, on: bool):
        if on != self._append_mode:
            self._append_mode = on
            self.files.set_append(on)
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
        self._refresh_directory_stats()
        for action, kind in self._toolbar_icons.items():
            action.setIcon(toolbar_icon(kind, theme["text"]))
        for action, kind in self._merge_toolbar_icons.items():
            action.setIcon(toolbar_icon(kind, theme["text"]))
        font = editor_font(self.config.settings)
        tab_width = max(1, int(self.config.settings.get("tab_width", 4)))
        indent_guide_width = max(1, int(self.config.settings.get("indent_guide_width", 4)))
        self.files.set_appearance(theme, font, tab_width, indent_guide_width)
        self.merge_view.set_appearance(theme, font, tab_width, indent_guide_width)
        self.directory_comparison.set_theme(theme)
        if self._native_toolbar_bridge is not None:
            self._native_toolbar_bridge.set_theme(theme)

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
        self.files.set_line_numbers_visible(line_numbers)
        self.files.set_syntax_highlighting_enabled(syntax_highlighting)
        self.syntax_highlighting_enabled = syntax_highlighting
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
        if not self.files.confirm_replace():
            return
        self.tabs.setCurrentWidget(self.files)
        self.files.open_path(self.files.left, left)
        self.files.open_path(self.files.right, right)

    def _refresh_current(self):
        if self.tabs.currentWidget() is self.directory_comparison:
            if not self._directory_copy_busy:
                self.directory_comparison.compare()
            return
        self.files.reload()

    def _tab_changed(self, index: int):
        files_active = self.tabs.widget(index) is self.files
        directories_active = self.tabs.widget(index) is self.directory_comparison
        merge_active = self.tabs.widget(index) is self.merge_view
        settings_active = self.tabs.widget(index) is self.settings_page
        for label in ("Open Left…", "Open Right…"):
            self.file_actions[label].setEnabled(files_active)
        self.file_actions["Save"].setEnabled(files_active or merge_active)
        self.file_actions["Save As…"].setEnabled(
            files_active or (merge_active and not self.git_mergetool))
        self.save_comparison_action.setEnabled(
            not settings_active and not (merge_active and self.git_mergetool))
        for action in self.undo_redo_actions:
            action.setEnabled(files_active or merge_active)
        directory_labels = {
            "Next Difference": "Next Different File",
            "Previous Difference": "Previous Different File",
            "Apply Right to Left": "Copy Right to Left",
            "Apply Left to Right": "Copy Left to Right",
        }
        for label, action in self.comparison_actions.items():
            action.setText(directory_labels.get(label, label) if directories_active else label)
        if not files_active:
            self.files.find_panel.hide()
        if not merge_active:
            self.merge_find_panel.hide()
        for action in self.find_actions:
            action.setEnabled(files_active or merge_active)
        for action in self.merge_actions:
            action.setEnabled(merge_active)
        for widget in (self.files.footer, self.files.encoding_button):
            widget.setVisible(files_active)
        self.merge_view.footer.setVisible(merge_active)
        self.merge_view.encoding_button.setVisible(merge_active)
        self.directory_summary_label.setVisible(directories_active)
        if self._native_toolbar_bridge is None:
            self.settings_toolbar.setFixedHeight(max(self.toolbar.height(), self.merge_toolbar.height()))
            self.toolbar.setVisible(files_active or directories_active)
            self.merge_toolbar.setVisible(merge_active)
            self.settings_toolbar.setVisible(settings_active)
        else:
            self.toolbar.hide()
            self.merge_toolbar.hide()
            self.settings_toolbar.hide()
            if merge_active:
                if self._merge_native_toolbar_bridge is not None:
                    self._merge_native_toolbar_bridge.activate()
                else:
                    self._native_toolbar_bridge.set_visible(False)
                    self.merge_toolbar.show()
            elif settings_active:
                if self._settings_native_toolbar_bridge is not None:
                    self._settings_native_toolbar_bridge.activate()
                else:
                    self._native_toolbar_bridge.set_visible(False)
                    self.settings_toolbar.show()
            else:
                self._native_toolbar_bridge.activate()
        if files_active:
            self.setWindowTitle(self.files.window_title())
        elif directories_active:
            self.setWindowTitle("Directories — Diffly")
        elif merge_active:
            self._refresh_merge_title()
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
        theme = self.config.themes[self._active_theme_name]
        self.files.set_appearance(theme, font, width, width)
        self.merge_view.set_appearance(theme, font, width, width)
        try:
            save_indent_width(width)
        except OSError as exc:
            self.statusBar().showMessage(f"Tab width changed but not saved: {exc}")

    def eventFilter(self, watched, event):
        if self.toolbar is not None and watched is self.toolbar and event.type() == QEvent.Type.MouseButtonDblClick:
            if self.isMaximized():
                self.showNormal()
            else:
                self.showMaximized()
            return True
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
                    "diffly.merge.refresh": (self.merge_refresh_action, "refresh"),
                    "diffly.merge.previous": (self.merge_previous_action, "up"),
                    "diffly.merge.next": (self.merge_next_action, "down"),
                    "diffly.merge.left": (self.merge_use_left_action, "apply_right"),
                    "diffly.merge.right": (self.merge_use_right_action, "apply_left"),
                    "diffly.merge.base": (self.merge_use_base_action, "base"),
                    "diffly.merge.resolved": (self.merge_resolved_action, "check"),
                }
                self._merge_native_toolbar_bridge = install_inline_toolbar(
                    self, merge_actions, "com.diffly.merge.toolbar"
                )
            self._settings_native_toolbar_bridge = install_inline_toolbar(
                self, {}, "com.diffly.settings.toolbar"
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
        return self.files.focused_editor()

    def _paste_current(self):
        if self.tabs.currentWidget() is self.files:
            self.files.paste()

    def _refresh_merge_title(self):
        if self.tabs.currentWidget() is not self.merge_view:
            return
        paths = self.merge_view.paths
        self.setWindowTitle(f"{paths[1].name} ↔ {paths[2].name} — Diffly" if paths else "Merge — Diffly")

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

    def _accept_merge(self, result):
        self.merge_view.accept_merge(result)

    def _merge_error(self, message: str):
        self.merge_view.merge_failed(message)

    def _navigate(self, step: int):
        if self.tabs.currentWidget() is self.directory_comparison:
            if not self._directory_copy_busy:
                self.directory_comparison.navigate(step)
            return
        if self.tabs.currentWidget() is self.files:
            self.files.navigate(step)

    def _transfer(self, source_is_left: bool, append: bool = False):
        """Toolbar/menu apply: copy a directory entry or the selected Files block."""
        if self.tabs.currentWidget() is self.directory_comparison:
            self._copy_selected_entry(to_left=not source_is_left)
        elif self.tabs.currentWidget() is self.files:
            self.files.transfer(self.files.selected_hunk, source_is_left, append)

    def _delete_selected(self, target_is_left: bool | None = None):
        if self.tabs.currentWidget() is self.files:
            self.files.delete_selected(self.files.selected_hunk, target_is_left)

    def _copy_directory_entry(self, relative_path: str, to_left: bool):
        self.directory_comparison.select_path(relative_path)
        self._copy_selected_entry(to_left)

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
        if any(editor.state.dirty and editor.state.path == target for editor in (self.files.left, self.files.right)):
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
        for editor in (self.files.left, self.files.right):
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

    def _save_active(self, save_as: bool = False):
        if self.tabs.currentWidget() is self.files:
            self.files.save_active(save_as)
        elif self.tabs.currentWidget() is self.merge_view:
            self.merge_view.save(save_as)

    def closeEvent(self, event: QCloseEvent):
        self.git_merge_success = False
        if self.files.busy or self._directory_copy_busy or self.merge_view.busy:
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
        if (self.files.dirty or
                (self.merge_view.result.state.dirty and not self.git_mergetool)):
            answer = QMessageBox.question(self, "Unsaved changes", "Quit and discard unsaved changes?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        if self._native_toolbar_bridge is not None:
            self._native_toolbar_bridge.close()
        if self._merge_native_toolbar_bridge is not None:
            self._merge_native_toolbar_bridge.close()
        if self._settings_native_toolbar_bridge is not None:
            self._settings_native_toolbar_bridge.close()
        self.files.stop()
        self.merge_view.syntax.stop()
        self.directory_comparison.cancel()
        self.scheduler.shutdown()
        self.git_merge_success = self.git_mergetool and self.merge_view.git_merge_complete()
        event.accept()
