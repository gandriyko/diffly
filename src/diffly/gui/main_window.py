import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QMenu,
    QScrollBar,
    QTabWidget,
    QToolBar,
    QToolButton,
    QWidget,
)

from diffly.core.diff.engine import DiffResult, delete_hunk_text, transfer_text
from diffly.core.documents.syntax import language_for
from diffly.gui.diff_overview import DiffOverview
from diffly.gui.directory_comparison import DirectoryComparisonView
from diffly.gui.editor_widget import EditorWidget
from diffly.gui.icons import toolbar_icon
from diffly.gui.ignore_patterns_dialog import IgnorePatternsDialog
from diffly.gui.linux_titlebar import LinuxTitleBar
from diffly.gui.theme import (
    apply_theme, editor_font, load_config, save_ignore_patterns, save_indent_width, save_selected_theme,
)
from diffly.platform.macos import install_inline_toolbar
from diffly.services.file_service import file_stamp, read_file, write_file
from diffly.services.directory_service import copy_file_checked, regular_file_stamp
from diffly.services.task_scheduler import TaskScheduler


ENCODINGS = (("UTF-8", "utf-8"), ("UTF-16 LE", "utf-16-le"),
             ("UTF-16 BE", "utf-16-be"), ("Windows-1251", "cp1251"),
             ("Windows-1252", "cp1252"), ("ISO-8859-1", "iso8859-1"),
             ("Windows-1250", "cp1250"), ("Shift JIS", "shift_jis"))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
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
        self.config = load_config()
        self.toolbar = None
        self.left = EditorWidget()
        self.right = EditorWidget()
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

        self.left_title = QLabel()
        self.right_title = QLabel()
        self.left_title.setObjectName("PaneTitle")
        self.right_title.setObjectName("PaneTitle")
        root = QWidget()
        root.setObjectName("DifflyRoot")
        layout = QGridLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(1)
        layout.setVerticalSpacing(0)
        layout.addWidget(self.left_title, 0, 0)
        layout.addWidget(self.right_title, 0, 1)
        layout.addWidget(self.left, 1, 0)
        layout.addWidget(self.right, 1, 1)
        self.shared_scrollbar = QScrollBar(Qt.Orientation.Vertical, root)
        self.shared_scrollbar.hide()
        self.overview = DiffOverview(self.left.editor, self.right.editor, self.shared_scrollbar, root)
        layout.addWidget(self.overview, 1, 2)
        self.shared_horizontal_scrollbar = QScrollBar(Qt.Orientation.Horizontal, root)
        self.shared_horizontal_scrollbar.installEventFilter(self)
        layout.addWidget(self.shared_horizontal_scrollbar, 2, 0, 1, 2)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        layout.setRowStretch(1, 1)
        self.file_comparison = root
        self.directory_comparison = DirectoryComparisonView(
            self.scheduler, list(self.config.settings.get("ignore_patterns", [])), self
        )
        self.directory_comparison.open_files.connect(self._open_directory_files)
        self.directory_comparison.selection_changed.connect(self._refresh_toolbar)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("ComparisonTabs")
        self.tabs.addTab(self.directory_comparison, "Directories")
        self.tabs.addTab(root, "Files")
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
        self.tab_label = QToolButton()
        self.tab_label.setObjectName("IndentWidthButton")
        self.tab_label.setAutoRaise(True)
        self.tab_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tab_label.setToolTip("Switch tab stops and indent guides between 2 and 4 spaces")
        self.tab_label.clicked.connect(self._toggle_indent_width)
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
        self.statusBar().addPermanentWidget(self.encoding_label)
        self.statusBar().addPermanentWidget(self.tab_label)
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
        for label, shortcut, callback in (
            ("Next Difference", "Ctrl+Alt+Down", lambda: self._navigate(1)),
            ("Previous Difference", "Ctrl+Alt+Up", lambda: self._navigate(-1)),
            ("Apply Right to Left", "Ctrl+Alt+Left", lambda: self._transfer(self._selected_hunk, False)),
            ("Apply Left to Right", "Ctrl+Alt+Right", lambda: self._transfer(self._selected_hunk, True)),
            ("Delete Selected Block", "Ctrl+Alt+Delete", lambda: self._delete_selected(self._selected_hunk)),
        ):
            action = QAction(label, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            edit_menu.addAction(action)

        theme_menu = menu_bar.addMenu("Theme")
        group = QActionGroup(self)
        group.setExclusive(True)
        for key, theme in self.config.themes.items():
            action = QAction(theme.get("name", key), self)
            action.setCheckable(True)
            action.setChecked(key == self.config.settings.get("theme"))
            action.triggered.connect(lambda checked=False, name=key: self._choose_theme(name))
            group.addAction(action)
            theme_menu.addAction(action)
        self._theme_actions = {action.text(): action for action in group.actions()}
        settings_menu = menu_bar.addMenu("Settings")
        settings_menu.addAction("Directory ignore masks…", self._edit_ignore_patterns)

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

        self.previous_action = add("Previous", "up", f"Previous difference ({modifier}+Up)", lambda: self._navigate(-1))
        self.next_action = add("Next", "down", f"Next difference ({modifier}+Down)", lambda: self._navigate(1))
        spacer = QWidget(toolbar)
        spacer.setFixedWidth(12)
        toolbar.addWidget(spacer)
        self.apply_left_action = add("Apply ←", "apply_left", f"Copy selected right block to left ({modifier}+Left)", lambda: self._transfer(self._selected_hunk, False))
        self.apply_right_action = add("Apply →", "apply_right", f"Copy selected left block to right ({modifier}+Right)", lambda: self._transfer(self._selected_hunk, True))
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

    def _refresh_toolbar(self):
        files_active = self.tabs.currentWidget() is self.file_comparison
        if not files_active:
            view = self.directory_comparison
            active = not self._directory_copy_busy
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
            self.apply_left_action.setToolTip(f"Copy selected file from right to left ({modifier}+Left)")
            self.apply_right_action.setToolTip(f"Copy selected file from left to right ({modifier}+Right)")
            if self._native_toolbar_bridge is not None:
                self._native_toolbar_bridge.sync()
            return
        has_hunks = files_active and self._diff is not None and bool(self._diff.hunks)
        selected = has_hunks and 0 <= self._selected_hunk < len(self._diff.hunks)
        self.previous_action.setEnabled(has_hunks)
        self.next_action.setEnabled(has_hunks)
        self.paste_action.setEnabled(files_active)
        for action in (self.apply_left_action, self.apply_right_action, self.delete_left_action, self.delete_right_action):
            action.setEnabled(selected)
        modifier = "Command+Option" if sys.platform == "darwin" else "Ctrl+Alt"
        self.previous_action.setToolTip(f"Previous difference ({modifier}+Up)")
        self.next_action.setToolTip(f"Next difference ({modifier}+Down)")
        self.apply_left_action.setToolTip(f"Copy selected right block to left ({modifier}+Left)")
        self.apply_right_action.setToolTip(f"Copy selected left block to right ({modifier}+Right)")
        if self._native_toolbar_bridge is not None:
            self._native_toolbar_bridge.sync()

    def _apply_theme(self, name: str):
        if name not in self.config.themes:
            name = "gruvbox"
        self._active_theme_name = name
        theme = self.config.themes[name]
        apply_theme(QApplication.instance(), theme)
        self._refresh_stats()
        for action, kind in self._toolbar_icons.items():
            action.setIcon(toolbar_icon(kind, theme["text"]))
        font = editor_font(self.config.settings)
        tab_width = max(1, int(self.config.settings.get("tab_width", 4)))
        indent_guide_width = max(1, int(self.config.settings.get("indent_guide_width", 4)))
        self.tab_label.setText(f"Tab: {tab_width} spaces")
        for editor in (self.left, self.right):
            editor.set_font(font, tab_width, indent_guide_width)
            editor.set_theme(theme)
        self.overview.set_theme(theme)
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

    def _edit_ignore_patterns(self):
        dialog = IgnorePatternsDialog(list(self.config.settings.get("ignore_patterns", [])), self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        patterns = dialog.patterns()
        self.config.settings["ignore_patterns"] = patterns
        self.directory_comparison.set_patterns(patterns)
        try:
            save_ignore_patterns(patterns)
        except OSError as exc:
            self.statusBar().showMessage(f"Ignore masks applied but not saved: {exc}")

    def _open_directory_files(self, left: Path, right: Path):
        if any(id(editor) in self._busy_files for editor in (self.left, self.right)):
            return
        if not all(self._confirm_discard(editor) for editor in (self.left, self.right)):
            return
        self.tabs.setCurrentWidget(self.file_comparison)
        self.open_path(self.left, left)
        self.open_path(self.right, right)

    def _tab_changed(self, index: int):
        files_active = self.tabs.widget(index) is self.file_comparison
        for widget in (self.summary_label, self.encoding_label, self.tab_label):
            widget.setVisible(files_active)
        if self._native_toolbar_bridge is None:
            self.toolbar.setVisible(True)
        if files_active:
            self._refresh_titles()
        else:
            self.setWindowTitle("Directories — Diffly")
        self.statusBar().showMessage(
            "Open a file in each pane to compare" if files_active
            else "Double-click a file present on both sides to compare it"
        )
        self._refresh_toolbar()

    def _toggle_indent_width(self):
        current = max(1, int(self.config.settings.get("tab_width", 4)))
        width = 4 if current == 2 else 2
        self.config.settings["tab_width"] = width
        self.config.settings["indent_guide_width"] = width
        self.tab_label.setText(f"Tab: {width} spaces")
        font = editor_font(self.config.settings)
        for editor in (self.left, self.right):
            editor.set_font(font, width, width)
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
                and event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            commands = {
                Qt.Key.Key_Up: lambda: self._navigate(-1),
                Qt.Key.Key_Down: lambda: self._navigate(1),
                Qt.Key.Key_Left: lambda: self._transfer(self._selected_hunk, False),
                Qt.Key.Key_Right: lambda: self._transfer(self._selected_hunk, True),
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
                self._refresh_encoding_label()
            elif watched is self.right.editor:
                self._last_active_side = "right"
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
            "diffly.previous": (self.previous_action, "up"),
            "diffly.next": (self.next_action, "down"),
            "diffly.apply_left": (self.apply_left_action, "apply_left"),
            "diffly.apply_right": (self.apply_right_action, "apply_right"),
            "diffly.paste": (self.paste_action, "paste"),
        }
        bridge = install_inline_toolbar(self, actions)
        if bridge is not None:
            self._native_toolbar_bridge = bridge
            self.toolbar.hide()
            self._native_toolbar_attempts = 0
            return
        self._native_toolbar_attempts += 1
        if self._native_toolbar_attempts < 4:
            QTimer.singleShot(100, self._configure_native_toolbar)

    def focused_editor(self):
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

    def _documents_changed(self, editor: EditorWidget):
        self._diff = None
        self._selected_hunk = -1
        self.left.editor.set_placeholder_ranges(())
        self.right.editor.set_placeholder_ranges(())
        self._update_shared_scroll_range()
        self._refresh_toolbar()
        self._refresh_stats()
        # Keep the last colors while the next diff runs. QTextCursor selections
        # follow edits; actions stay disabled until a current result arrives.
        self._refresh_titles()
        self.debounce.start()
        if hasattr(self, "_syntax_timers"):
            self._syntax_timers[editor].start()

    def _request_syntax(self, side: str, editor: EditorWidget):
        text = editor.text()
        language = language_for(editor.state.path, text[:512])
        editor.set_syntax_language(language)
        if language is None or len(text) > 2_000_000:
            editor.highlighter.set_spans(())
            return
        self.scheduler.submit_syntax(side, text, language, editor.state.revision)

    def _accept_syntax(self, result):
        side, revision, language, spans = result
        editor = self.left if side == "left" else self.right
        if editor.state.revision != revision or editor.syntax_language != language:
            return
        editor.highlighter.set_spans(spans)

    def _request_diff(self):
        self.statusBar().showMessage("Comparing…")
        self.scheduler.submit_diff(
            self.left.text(), self.right.text(),
            self.left.state.revision, self.right.state.revision,
        )

    def _accept_diff(self, result: DiffResult):
        if (result.left_revision, result.right_revision) != (
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
        self._cursor_hunk_changed(self.left if self._last_active_side == "left" else self.right)
        self.statusBar().showMessage(f"{len(result.hunks)} difference(s)")

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

    def _transfer(self, row: int, source_is_left: bool):
        if self.tabs.currentWidget() is self.directory_comparison:
            self._copy_selected_file(to_left=not source_is_left)
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
        new_text = transfer_text(source.text(), target.text(), result.hunks[row], source_is_left)
        target.apply_text(new_text)
        target.editor.setFocus(Qt.FocusReason.OtherFocusReason)
        self.statusBar().showMessage("Block copied; use Undo to revert")

    def _copy_selected_file(self, to_left: bool):
        view = self.directory_comparison
        if self._directory_copy_busy or not view.can_copy(to_left):
            return
        entry = view.selected_entry()
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
            target.apply_text(new_text)
            target.editor.setFocus(Qt.FocusReason.OtherFocusReason)
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
        self.statusBar().showMessage(f"Opening {path.name}…")
        self._serial += 1
        key = self._serial

        def done(loaded):
            self._busy_files.discard(id(editor))
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

        def failed(message):
            self._busy_files.discard(id(editor))
            QMessageBox.critical(self, "Open failed", message)

        self.scheduler.submit_file(key, read_file, (path, encoding), done, failed)

    def _save_active(self, save_as: bool = False):
        if self.tabs.currentWidget() is self.file_comparison:
            self._save(self._active_pane(), save_as)

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
        self.statusBar().showMessage(f"Saving {path.name}…")
        self._serial += 1
        key = self._serial

        def done(stamp):
            self._busy_files.discard(id(editor))
            editor.state.path = path
            editor.state.stamp = stamp
            editor.state.saved_revision = revision
            editor.set_syntax_language(language_for(path, editor.text()[:512]))
            self._syntax_timers[editor].start()
            self._refresh_titles()
            self.statusBar().showMessage(f"Saved {path.name}")

        def failed(message):
            self._busy_files.discard(id(editor))
            QMessageBox.critical(self, "Save failed", message)

        self.scheduler.submit_file(key, write_file, (path, text, newline, bom, expected, encoding), done, failed)

    def closeEvent(self, event: QCloseEvent):
        if self._busy_files or self._directory_copy_busy:
            QMessageBox.information(self, "Files busy", "Wait for file operations to finish before closing.")
            event.ignore()
            return
        if any(editor.state.dirty for editor in (self.left, self.right)):
            answer = QMessageBox.question(self, "Unsaved changes", "Quit and discard unsaved changes?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        if self._native_toolbar_bridge is not None:
            self._native_toolbar_bridge.close()
        self.debounce.stop()
        for timer in self._syntax_timers.values():
            timer.stop()
        self.directory_comparison.cancel()
        self.scheduler.shutdown()
        event.accept()
