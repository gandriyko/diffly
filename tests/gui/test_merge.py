from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from diffly.core.diff.engine import compare
from diffly.gui.main_window import MainWindow


def test_transfer_is_one_undo_step(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    qtbot.addWidget(window)
    window.left.load_text("one\nleft\n")
    window.right.load_text("one\nright\n")
    result = compare(window.left.text(), window.right.text(), window.left.state.revision, window.right.state.revision)
    window._accept_diff(result)
    window._transfer(0, True)
    assert window.right.text() == window.left.text()
    window.right.editor.undo()
    assert window.right.text() == "one\nright\n"
    window.close()


def test_background_diff_rejects_stale_result(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    qtbot.addWidget(window)
    window.left.load_text("old\n")
    window.right.load_text("other\n")
    stale = compare(window.left.text(), window.right.text(), window.left.state.revision, window.right.state.revision)
    window.left.editor.insertPlainText("new\n")
    window._accept_diff(stale)
    assert window._diff is None
    window._request_diff()
    qtbot.waitUntil(lambda: window._diff is not None, timeout=5000)
    assert window._diff.left_revision == window.left.state.revision
    window.close()


def test_internal_placeholder_aligns_matching_lines_without_editing_text(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    left = "a\nb\nextra one\nextra two\nc\nd\n"
    right = "a\nb\nc\nd\n"
    window.left.load_text(left)
    window.right.load_text(right)
    window._accept_diff(compare(left, right, window.left.state.revision, window.right.state.revision))

    assert window.right.editor.placeholder_ranges == ((2, 2),)
    left_block = window.left.editor.document().findBlockByNumber(4)
    right_block = window.right.editor.document().findBlockByNumber(2)
    left_y = window.left.editor.blockBoundingGeometry(left_block).translated(window.left.editor.contentOffset()).top()
    right_y = window.right.editor.blockBoundingGeometry(right_block).translated(window.right.editor.contentOffset()).top()
    assert abs(left_y - right_y) < 1
    assert window.left.text() == left
    assert window.right.text() == right
    assert not window.right.editor.document().isUndoAvailable()
    window.close()


def test_click_changed_line_selects_diff_for_toolbar(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.left.load_text("a\nleft\nz\n")
    window.right.load_text("a\nright\nz\n")
    window._accept_diff(compare(window.left.text(), window.right.text(), window.left.state.revision, window.right.state.revision))
    block = window.left.editor.document().findBlockByNumber(1)
    geometry = window.left.editor.blockBoundingGeometry(block).translated(window.left.editor.contentOffset())
    qtbot.mouseClick(window.left.editor.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(5, round(geometry.center().y())))
    assert window._selected_hunk == 0
    window.delete_left_action.trigger()
    assert window.left.text() == "a\nz\n"
    window._accept_diff(compare(window.left.text(), window.right.text(), window.left.state.revision, window.right.state.revision))
    window._select_hunk(0)
    window.delete_right_action.trigger()
    assert window.right.text() == "a\nz\n"
    window.close()


def test_ctrl_alt_arrows_navigate_and_apply_differences(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.activateWindow()
    qtbot.wait(20)
    window.left.load_text("left1\nsame\nleft2\n")
    window.right.load_text("right1\nsame\nright2\n")
    window._accept_diff(compare(window.left.text(), window.right.text(), window.left.state.revision, window.right.state.revision))
    cursor = window.left.editor.textCursor()
    cursor.setPosition(window.left.editor.document().findBlockByNumber(1).position())
    window.left.editor.setTextCursor(cursor)
    window.left.editor.setFocus()
    modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
    qtbot.keyClick(window.left.editor, Qt.Key.Key_Down, modifiers)
    assert window._selected_hunk == 0
    assert window.left.editor.textCursor().blockNumber() == 0
    assert window.left.editor.hasFocus()
    qtbot.keyClick(window.left.editor, Qt.Key.Key_Down, modifiers)
    assert window._selected_hunk == 1
    assert window.left.editor.textCursor().blockNumber() == 2
    qtbot.keyClick(window.left.editor, Qt.Key.Key_Up, modifiers)
    assert window._selected_hunk == 0
    qtbot.keyClick(window.left.editor, Qt.Key.Key_Right, modifiers)
    assert window.right.text() == "left1\nsame\nright2\n"
    window.close()


def test_open_paths_loads_both_panes_in_background(qtbot, tmp_path):
    left = tmp_path / "left.txt"
    right = tmp_path / "right.txt"
    left.write_text("left\n")
    right.write_text("right\n")
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_path(window.left, left)
    window.open_path(window.right, right)
    qtbot.waitUntil(lambda: window.left.state.path == left and window.right.state.path == right, timeout=5000)
    assert window.left.text() == "left\n"
    assert window.right.text() == "right\n"
    window.close()


def test_toolbar_applies_selected_diff_and_has_right_overview(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.left.load_text("same\nleft\n")
    window.right.load_text("same\nright\n")
    window._accept_diff(compare(window.left.text(), window.right.text(), window.left.state.revision, window.right.state.revision))
    assert window.overview.isVisible()
    assert window.overview.width() == 18
    assert window.overview.hunks
    assert window.next_action.isEnabled()
    assert not window.apply_right_action.isEnabled()
    window.next_action.trigger()
    assert window._selected_hunk == 0
    assert window.apply_right_action.isEnabled()
    window.apply_right_action.trigger()
    assert window.right.text() == window.left.text()
    qtbot.waitUntil(lambda: window.focused_editor() is window.right.editor)
    window.close()


def test_double_click_on_toolbar_zooms_without_fullscreen(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.mouseDClick(window.toolbar, Qt.MouseButton.LeftButton, pos=QPoint(500, window.toolbar.height() // 2))
    assert window.isMaximized()
    assert not window.isFullScreen()
    window.close()


def test_compact_layout_and_status_information(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.left.load_text("same\nleft\n")
    window.right.load_text("same\nright\n")
    window._accept_diff(compare(window.left.text(), window.right.text(), window.left.state.revision, window.right.state.revision))
    window.show()
    assert window.centralWidget().layout().contentsMargins().left() == 0
    assert window.centralWidget().layout().horizontalSpacing() == 1
    assert "1 changed" in window.summary_label.text()
    assert "1 identical" in window.summary_label.text()
    width = max(1, int(window.config.settings.get("tab_width", 4)))
    assert window.tab_label.text() == f"Tab: {width} spaces"
    assert "UTF-8" in window.left_info.text()
    window.close()


def test_right_overview_controls_both_panes(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(700, 350)
    window.show()
    window.left.load_text("\n".join(str(index) for index in range(150)))
    window.right.load_text("\n".join(str(index) for index in range(90)))
    left = window.left.editor.verticalScrollBar()
    right = window.right.editor.verticalScrollBar()
    shared = window.shared_scrollbar
    assert not shared.isVisible()
    assert window.overview.isVisible()
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
    QApplication.sendEvent(window.right.editor.viewport(), wheel)
    assert shared.value() > before
    assert left.value() == shared.value()
    assert right.value() == right.maximum()
    qtbot.mouseClick(window.overview, Qt.MouseButton.LeftButton, pos=QPoint(9, window.overview.height() // 2))
    assert 0 < shared.value() < shared.maximum()
    assert left.value() == shared.value()
    window.close()
