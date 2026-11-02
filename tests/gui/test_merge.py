from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QTextCursor, QWheelEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QToolButton

from diffly.core.diff.engine import compare
from diffly.gui.main_window import MainWindow


def test_transfer_is_one_undo_step(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    window.tabs.setCurrentWidget(window.files)
    qtbot.addWidget(window)
    window.files.left.load_text("one\nleft\n")
    window.files.right.load_text("one\nright\n")
    result = compare(window.files.left.text(), window.files.right.text(), window.files.left.state.revision, window.files.right.state.revision)
    window.files._accept_diff(result)
    window.files.transfer(0, True)
    assert window.files.right.text() == window.files.left.text()
    window.files.right.editor.undo()
    assert window.files.right.text() == "one\nright\n"
    window.close()


def test_gutter_arrow_transfers_and_shift_appends(qtbot):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.files)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    window.files.left.load_text("one\nleft\n")
    window.files.right.load_text("one\nright\n")
    window.files._accept_diff(compare(window.files.left.text(), window.files.right.text(), window.files.left.state.revision, window.files.right.state.revision))
    top = window.files.gutter._band(window.files.diff.hunks[0])[0]
    point = window.files.gutter.button_rect(top, True).center().toPoint()
    qtbot.mouseClick(window.files.gutter, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, point)
    assert window.files.right.text() == "one\nright\nleft\n"

    window.files._accept_diff(compare(window.files.left.text(), window.files.right.text(), window.files.left.state.revision, window.files.right.state.revision))
    top = window.files.gutter._band(window.files.diff.hunks[0])[0]
    point = window.files.gutter.button_rect(top, False).center().toPoint()
    qtbot.mouseClick(window.files.gutter, Qt.MouseButton.LeftButton, pos=point)
    assert window.files.left.text() == window.files.right.text()
    window.close()


def test_transfer_keeps_view_in_place_and_selects_inserted_text(qtbot):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.files)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    body = "".join(f"line {i}\n" for i in range(300))
    # An insertion high up gives the right pane a placeholder that must survive the edit.
    left = "only left\n" + body + "old\n" + body
    right = body + "new\n" + body
    window.files.left.load_text(left)
    window.files.right.load_text(right)
    window.files._accept_diff(compare(left, right, window.files.left.state.revision, window.files.right.state.revision))
    window.files.select_hunk(1)
    view = window.files.shared_scrollbar.value() + 3
    # Leave the right cursor far above the view: the edit must not scroll to it.
    window.files.right.editor.setTextCursor(QTextCursor(window.files.right.editor.document()))
    window.files.shared_scrollbar.setValue(view)
    before = window.files.shared_scrollbar.value()
    assert before > 100

    window.files.transfer(1, True)
    assert window.files.shared_scrollbar.value() == before
    assert window.files.right.editor.textCursor().selectedText() == "old"
    assert window.files.right.editor.placeholder_ranges == ((0, 1),)
    window.close()


def test_background_diff_rejects_stale_result(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    window.tabs.setCurrentWidget(window.files)
    qtbot.addWidget(window)
    window.files.left.load_text("old\n")
    window.files.right.load_text("other\n")
    stale = compare(window.files.left.text(), window.files.right.text(), window.files.left.state.revision, window.files.right.state.revision)
    window.files.left.editor.insertPlainText("new\n")
    window.files._accept_diff(stale)
    assert window.files.diff is None
    window.files._request_diff()
    qtbot.waitUntil(lambda: window.files.diff is not None, timeout=5000)
    assert window.files.diff.left_revision == window.files.left.state.revision
    window.close()


def test_internal_placeholder_aligns_matching_lines_without_editing_text(qtbot):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.files)
    qtbot.addWidget(window)
    window.show()
    left = "a\nb\nextra one\nextra two\nc\nd\n"
    right = "a\nb\nc\nd\n"
    window.files.left.load_text(left)
    window.files.right.load_text(right)
    window.files._accept_diff(compare(left, right, window.files.left.state.revision, window.files.right.state.revision))

    assert window.files.right.editor.placeholder_ranges == ((2, 2),)
    left_block = window.files.left.editor.document().findBlockByNumber(4)
    right_block = window.files.right.editor.document().findBlockByNumber(2)
    left_y = window.files.left.editor.blockBoundingGeometry(left_block).translated(window.files.left.editor.contentOffset()).top()
    right_y = window.files.right.editor.blockBoundingGeometry(right_block).translated(window.files.right.editor.contentOffset()).top()
    assert abs(left_y - right_y) < 1
    assert window.files.left.text() == left
    assert window.files.right.text() == right
    assert not window.files.right.editor.document().isUndoAvailable()
    window.close()


def test_files_gutter_arrows_follow_each_pane_through_placeholder_scroll(qtbot, monkeypatch, tmp_path):
    from pathlib import Path

    monkeypatch.setenv("DIFFLY_CONFIG", str(tmp_path / "config.toml"))
    fixture = Path(__file__).parents[1] / "fixtures" / "diff"
    window = MainWindow()
    window.resize(1000, 700)
    window.tabs.setCurrentWidget(window.files)
    qtbot.addWidget(window)
    window.show()
    left = (fixture / "python_left.py").read_text()
    right = (fixture / "python_right.py").read_text()
    window.files.left.load_text(left)
    window.files.right.load_text(right)
    window.files._accept_diff(compare(left, right, window.files.left.state.revision,
                                window.files.right.state.revision))
    hunk = next(hunk for hunk in window.files.diff.hunks
                if hunk.left_end > hunk.left_start and hunk.right_end > hunk.right_start
                and hunk.left_start >= 20)
    row = window.files.diff.hunks.index(hunk)
    for value in range(window.files.shared_scrollbar.maximum() + 1):
        window.files.shared_scrollbar.setValue(value)
        edges = window.files.gutter._edges(hunk)
        if edges is not None and abs(edges[0] - edges[2]) > 1:
            break
    else:
        raise AssertionError("fixture did not scroll through a placeholder")
    left_top, _left_bottom, right_top, _right_bottom = edges
    assert window.files.gutter._button_at(window.files.gutter.button_rect(left_top, True).center()) == (row, True)
    assert window.files.gutter._button_at(window.files.gutter.button_rect(right_top, False).center()) == (row, False)
    window.close()


def test_click_changed_line_selects_diff_for_toolbar(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    window.tabs.setCurrentWidget(window.files)
    qtbot.addWidget(window)
    window.show()
    window.files.left.load_text("a\nleft\nz\n")
    window.files.right.load_text("a\nright\nz\n")
    window.files._accept_diff(compare(window.files.left.text(), window.files.right.text(), window.files.left.state.revision, window.files.right.state.revision))
    block = window.files.left.editor.document().findBlockByNumber(1)
    geometry = window.files.left.editor.blockBoundingGeometry(block).translated(window.files.left.editor.contentOffset())
    qtbot.mouseClick(window.files.left.editor.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(5, round(geometry.center().y())))
    assert window.files.selected_hunk == 0
    window.delete_left_action.trigger()
    assert window.files.left.text() == "a\nz\n"
    window.files._accept_diff(compare(window.files.left.text(), window.files.right.text(), window.files.left.state.revision, window.files.right.state.revision))
    window.files.select_hunk(0)
    window.delete_right_action.trigger()
    assert window.files.right.text() == "a\nz\n"
    window.close()


def test_ctrl_alt_arrows_navigate_and_apply_differences(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    window.tabs.setCurrentWidget(window.files)
    qtbot.addWidget(window)
    window.show()
    window.activateWindow()
    qtbot.wait(20)
    window.files.left.load_text("left1\nsame\nleft2\n")
    window.files.right.load_text("right1\nsame\nright2\n")
    window.files._accept_diff(compare(window.files.left.text(), window.files.right.text(), window.files.left.state.revision, window.files.right.state.revision))
    cursor = window.files.left.editor.textCursor()
    cursor.setPosition(window.files.left.editor.document().findBlockByNumber(1).position())
    window.files.left.editor.setTextCursor(cursor)
    window.files.left.editor.setFocus()
    modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
    qtbot.keyClick(window.files.left.editor, Qt.Key.Key_Down, modifiers)
    assert window.files.selected_hunk == 0
    assert window.files.left.editor.textCursor().blockNumber() == 0
    assert window.files.left.editor.hasFocus()
    qtbot.keyClick(window.files.left.editor, Qt.Key.Key_Down, modifiers)
    assert window.files.selected_hunk == 1
    assert window.files.left.editor.textCursor().blockNumber() == 2
    qtbot.keyClick(window.files.left.editor, Qt.Key.Key_Up, modifiers)
    assert window.files.selected_hunk == 0
    qtbot.keyClick(window.files.left.editor, Qt.Key.Key_Right, modifiers)
    assert window.files.right.text() == "left1\nsame\nright2\n"
    window.close()


def test_open_paths_loads_both_panes_in_background(qtbot, tmp_path):
    left = tmp_path / "left.txt"
    right = tmp_path / "right.txt"
    left.write_text("left\n")
    right.write_text("right\n")
    window = MainWindow()
    window.tabs.setCurrentWidget(window.files)
    qtbot.addWidget(window)
    window.files.open_path(window.files.left, left)
    window.files.open_path(window.files.right, right)
    qtbot.waitUntil(lambda: window.files.left.state.path == left and window.files.right.state.path == right, timeout=5000)
    assert window.files.left.text() == "left\n"
    assert window.files.right.text() == "right\n"
    window.close()


def test_toolbar_applies_selected_diff_and_has_right_overview(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    window.tabs.setCurrentWidget(window.files)
    qtbot.addWidget(window)
    window.show()
    window.files.left.load_text("same\nleft\n")
    window.files.right.load_text("same\nright\n")
    window.files._accept_diff(compare(window.files.left.text(), window.files.right.text(), window.files.left.state.revision, window.files.right.state.revision))
    assert window.files.overview.isVisible()
    assert window.files.overview.width() == 18
    assert window.files.overview.hunks
    assert window.next_action.isEnabled()
    assert not window.apply_right_action.isEnabled()
    window.next_action.trigger()
    assert window.files.selected_hunk == 0
    assert window.apply_right_action.isEnabled()
    window.apply_right_action.trigger()
    assert window.files.right.text() == window.files.left.text()
    qtbot.waitUntil(lambda: window.focused_editor() is window.files.right.editor)
    window.close()


def test_double_click_on_toolbar_zooms_without_fullscreen(qtbot):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.files)
    qtbot.addWidget(window)
    window.show()
    qtbot.mouseDClick(window.toolbar, Qt.MouseButton.LeftButton, pos=QPoint(500, window.toolbar.height() // 2))
    assert window.isMaximized()
    assert not window.isFullScreen()
    window.close()


def test_linux_toolbar_shares_title_row_with_menu_and_window_controls(qtbot, monkeypatch):
    import diffly.gui.main_window as main_window
    from types import SimpleNamespace

    monkeypatch.setattr(main_window, "sys", SimpleNamespace(platform="linux"))
    window = MainWindow()
    window.tabs.setCurrentWidget(window.files)
    qtbot.addWidget(window)
    window.show()
    header = window._linux_titlebar
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert window.menuWidget() is header
    assert window.toolbar.parent() is header
    assert [action.text() for action in header.menu_bar.actions()] == ["File", "Edit", "Settings"]
    window.setWindowTitle("Left ↔ Right — Diffly")
    assert header.title.text() == "Left ↔ Right — Diffly"
    maximize = next(button for button in header.findChildren(QToolButton)
                    if button.toolTip() == "Maximize or restore")
    qtbot.mouseClick(maximize, Qt.MouseButton.LeftButton)
    assert window.isMaximized()
    qtbot.mouseClick(maximize, Qt.MouseButton.LeftButton)
    assert not window.isMaximized()
    assert window.statusBar().isSizeGripEnabled()
    window.close()


def test_compact_layout_and_status_information(qtbot):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.files)
    qtbot.addWidget(window)
    window.files.left.load_text("same\nleft\n")
    window.files.right.load_text("same\nright\n")
    window.files._accept_diff(compare(window.files.left.text(), window.files.right.text(), window.files.left.state.revision, window.files.right.state.revision))
    window.show()
    assert window.tabs.count() == 3
    assert window.files.layout().contentsMargins().left() == 4
    assert window.files.layout().spacing() == 0
    assert "1 changed" in window.files.footer.text()
    assert "1 identical" in window.files.footer.text()
    assert not window.findChildren(QToolButton, "IndentWidthButton")
    assert "UTF-8" in window.files.encoding_button.text() and "LF" in window.files.encoding_button.text()
    # Splitter, shared horizontal scrollbar and find panel.
    assert window.files.layout().count() == 3
    assert window.files.left_title.text() == "Left · Untitled"
    assert not window.files.find_panel.isVisible()
    window.close()


def test_right_overview_controls_both_panes(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    window.tabs.setCurrentWidget(window.files)
    qtbot.addWidget(window)
    window.resize(700, 350)
    window.show()
    window.files.left.load_text("\n".join(str(index) for index in range(150)))
    window.files.right.load_text("\n".join(str(index) for index in range(90)))
    left = window.files.left.editor.verticalScrollBar()
    right = window.files.right.editor.verticalScrollBar()
    shared = window.files.shared_scrollbar
    assert not shared.isVisible()
    assert window.files.overview.isVisible()
    assert not left.isVisible() and not right.isVisible()
    assert shared.maximum() == max(left.maximum(), right.maximum())
    shared.setValue(20)
    assert (left.value(), right.value()) == (20, 20)
    left.setValue(30)
    assert (shared.value(), right.value()) == (30, 30)
    shared.setValue(right.maximum() + 5)
    before = shared.value()
    wheel = QWheelEvent(
        QPointF(50, 50), QPointF(50, 50), QPoint(), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate, False,
    )
    QApplication.sendEvent(window.files.right.editor.viewport(), wheel)
    assert shared.value() > before
    assert left.value() == shared.value()
    assert right.value() == right.maximum()
    qtbot.mouseClick(window.files.overview, Qt.MouseButton.LeftButton, pos=QPoint(9, window.files.overview.height() // 2))
    assert 0 < shared.value() < shared.maximum()
    assert left.value() == shared.value()
    window.close()
