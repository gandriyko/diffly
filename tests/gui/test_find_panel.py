from PySide6.QtCore import Qt

from diffly.gui.main_window import MainWindow


def test_footer_find_and_replace_stays_in_current_pane(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("DIFFLY_CONFIG", str(tmp_path / "config.toml"))
    window = MainWindow()
    qtbot.addWidget(window)
    window.tabs.setCurrentWidget(window.file_comparison)
    window.show()
    window.left.load_text("alpha beta beta\n")
    window.right.load_text("beta BETA\n")
    panel = window.find_panel
    window.left.editor.setFocus()
    qtbot.waitUntil(window.left.editor.hasFocus)
    window._show_find(False)
    assert panel.isVisible()
    assert panel.parent() is window.file_comparison
    assert panel.mapTo(window, panel.rect().bottomLeft()).y() < window.statusBar().y()
    assert not hasattr(panel, "scope")
    panel.query.setText("beta")
    panel.next_button.click()
    assert panel.isVisible()
    assert window.left.editor.textCursor().selectedText() == "beta"
    qtbot.keyClick(panel.query, Qt.Key.Key_Escape)
    assert not panel.isVisible()
    qtbot.keyClick(window.left.editor, Qt.Key.Key_F3)
    assert window.left.editor.textCursor().selectionStart() == len("alpha beta ")
    qtbot.keyClick(window.left.editor, Qt.Key.Key_F3, Qt.KeyboardModifier.ShiftModifier)
    assert window.left.editor.textCursor().selectionStart() == len("alpha ")
    assert window.right.text() == "beta BETA\n"
    window.right.editor.setFocus()
    qtbot.waitUntil(window.right.editor.hasFocus)
    qtbot.keyClick(window.right.editor, Qt.Key.Key_F3)
    assert window.right.editor.textCursor().selectedText() == "beta"
    window._show_find(True)
    assert panel.replace_row.isVisible()
    panel.replacement.setText("gamma")
    panel.replace_all()
    assert window.left.text() == "alpha beta beta\n"
    assert window.right.text() == "gamma gamma\n"
    window.right.editor.undo()
    assert window.right.text() == "beta BETA\n"
    panel.hide_search()
    window.close()
