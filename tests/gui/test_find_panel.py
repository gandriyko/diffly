from PySide6.QtCore import Qt

from diffly.gui.main_window import MainWindow


def test_footer_find_and_replace_stays_in_current_pane(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("DIFFLY_CONFIG", str(tmp_path / "config.toml"))
    window = MainWindow()
    qtbot.addWidget(window)
    window.tabs.setCurrentWidget(window.files)
    window.show()
    window.files.left.load_text("alpha beta beta\n")
    window.files.right.load_text("beta BETA\n")
    panel = window.files.find_panel
    window.files.left.editor.setFocus()
    qtbot.waitUntil(window.files.left.editor.hasFocus)
    window._show_find(False)
    assert panel.isVisible()
    assert panel.parent() is window.files
    assert panel.mapTo(window, panel.rect().bottomLeft()).y() < window.statusBar().y()
    assert not hasattr(panel, "scope")
    panel.query.setText("beta")
    panel.next_button.click()
    assert panel.isVisible()
    assert window.files.left.editor.textCursor().selectedText() == "beta"
    qtbot.keyClick(panel.query, Qt.Key.Key_Escape)
    assert not panel.isVisible()
    qtbot.keyClick(window.files.left.editor, Qt.Key.Key_F3)
    assert window.files.left.editor.textCursor().selectionStart() == len("alpha beta ")
    qtbot.keyClick(window.files.left.editor, Qt.Key.Key_F3, Qt.KeyboardModifier.ShiftModifier)
    assert window.files.left.editor.textCursor().selectionStart() == len("alpha ")
    assert window.files.right.text() == "beta BETA\n"
    window.files.right.editor.setFocus()
    qtbot.waitUntil(window.files.right.editor.hasFocus)
    qtbot.keyClick(window.files.right.editor, Qt.Key.Key_F3)
    assert window.files.right.editor.textCursor().selectedText() == "beta"
    window._show_find(True)
    assert panel.replace_row.isVisible()
    panel.replacement.setText("gamma")
    panel.replace_all()
    assert window.files.left.text() == "alpha beta beta\n"
    assert window.files.right.text() == "gamma gamma\n"
    window.files.right.editor.undo()
    assert window.files.right.text() == "beta BETA\n"
    panel.hide_search()
    window.close()
