from PySide6.QtWidgets import QMessageBox

from diffly.gui.main_window import MainWindow


def test_file_refresh_reloads_disk_and_respects_unsaved_changes(qtbot, tmp_path, monkeypatch):
    left, right = tmp_path / "left.txt", tmp_path / "right.txt"
    left.write_text("old left")
    right.write_text("old right")
    window = MainWindow()
    qtbot.addWidget(window)
    window.tabs.setCurrentWidget(window.files)
    window.show()
    window.files.open_path(window.files.left, left)
    window.files.open_path(window.files.right, right)
    qtbot.waitUntil(lambda: window.files.left.text() == "old left" and window.files.right.text() == "old right")
    left.write_text("new left")
    right.write_text("new right")
    window.refresh_action.trigger()
    qtbot.waitUntil(lambda: window.files.left.text() == "new left" and window.files.right.text() == "new right")

    window.files.left.apply_text("unsaved")
    left.write_text("newer left")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    window.refresh_action.trigger()
    assert window.files.left.text() == "unsaved"
    assert window.files.right.text() == "new right"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window.refresh_action.trigger()
    qtbot.waitUntil(lambda: window.files.left.text() == "newer left")
    window.close()
