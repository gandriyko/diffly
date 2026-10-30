from PySide6.QtGui import QKeySequence

from diffly.gui.main_window import MainWindow


def test_save_action_targets_only_active_editor(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    menu_bar = window._linux_titlebar.menu_bar if window._linux_titlebar is not None else window.menuBar()
    file_menu = menu_bar.actions()[0].menu()
    save = next(action for action in file_menu.actions() if action.text() == "Save")
    save_as = next(action for action in file_menu.actions() if action.text() == "Save As…")
    assert save.shortcut() == QKeySequence("Ctrl+S")
    assert save_as.shortcut().isEmpty()
    assert not any(action.shortcut() == QKeySequence("Ctrl+Shift+S") for action in file_menu.actions())

    calls = []
    monkeypatch.setattr(window, "_save", lambda editor, save_as=False: calls.append((editor, save_as)))
    window.tabs.setCurrentWidget(window.file_comparison)
    window.left.editor.setFocus()
    qtbot.waitUntil(window.left.editor.hasFocus)
    save.trigger()
    window.right.editor.setFocus()
    qtbot.waitUntil(window.right.editor.hasFocus)
    save.trigger()
    save_as.trigger()
    assert calls == [(window.left, False), (window.right, False), (window.right, True)]
    window.tabs.setCurrentWidget(window.directory_comparison)
    save.trigger()
    assert len(calls) == 3
    window.close()
