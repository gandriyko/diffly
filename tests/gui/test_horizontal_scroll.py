from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from diffly.core.diff.engine import compare
from diffly.gui.main_window import MainWindow


def test_shared_horizontal_scroll_and_range_changes(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(700, 350)
    window.show()
    window.left.load_text('x' * 400)
    window.right.load_text('x' * 200)
    left = window.left.editor.horizontalScrollBar()
    right = window.right.editor.horizontalScrollBar()
    shared = window.shared_horizontal_scrollbar
    qtbot.waitUntil(lambda: shared.isVisible() and right.maximum() > 100)
    assert not left.isVisible() and not right.isVisible()
    assert shared.maximum() == max(left.maximum(), right.maximum())
    shared.setValue(80)
    assert (left.value(), right.value()) == (80, 80)
    right.setValue(100)
    assert (left.value(), shared.value()) == (100, 100)
    shared.setValue(shared.maximum())
    assert left.value() == shared.value()
    assert right.value() == right.maximum()

    for pixel_delta, delta, modifiers in (
        (QPoint(), QPoint(-120, 0), Qt.KeyboardModifier.NoModifier),
        (QPoint(), QPoint(0, -120), Qt.KeyboardModifier.ShiftModifier),
        (QPoint(-40, 0), QPoint(), Qt.KeyboardModifier.NoModifier),
    ):
        shared.setValue(80)
        before_vertical = window.shared_scrollbar.value()
        wheel = QWheelEvent(
            QPointF(50, 50), QPointF(50, 50), pixel_delta, delta,
            Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.ScrollUpdate, False,
        )
        QApplication.sendEvent(window.right.editor.viewport(), wheel)
        assert shared.value() > 80
        assert left.value() == right.value() == shared.value()
        assert window.shared_scrollbar.value() == before_vertical

    window.resize(1000, 350)
    qtbot.waitUntil(lambda: shared.maximum() == max(left.maximum(), right.maximum()))
    window.left.load_text('short')
    window.right.load_text('short')
    qtbot.waitUntil(lambda: not shared.isVisible())
    assert shared.value() == left.value() == right.value() == 0
    window.close()


def test_diff_colors_remain_until_current_result_arrives(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    qtbot.addWidget(window)
    window.left.load_text('const left = 1;')
    window.right.load_text('const right = 2;')
    previous = compare(window.left.text(), window.right.text(), window.left.state.revision, window.right.state.revision)
    window._accept_diff(previous)
    assert window.left.editor.extraSelections()
    window.left.editor.insertPlainText(' ')
    assert window._diff is None
    assert window.left.editor.extraSelections()
    assert window.right.editor.extraSelections()
    assert not window.apply_left_action.isEnabled()
    window._accept_diff(previous)
    assert window._diff is None
    window.right.apply_text(window.left.text())
    window._accept_diff(compare(window.left.text(), window.right.text(), window.left.state.revision, window.right.state.revision))
    assert not window.left.editor.extraSelections()
    assert not window.right.editor.extraSelections()
    window.close()
