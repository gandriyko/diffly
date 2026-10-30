from PySide6.QtWidgets import QInputDialog

from diffly.gui.main_window import MainWindow
from diffly.services.session_store import load_sessions


def test_named_comparison_reopens_paths_and_options(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("DIFFLY_CONFIG", str(tmp_path / "config.toml"))
    left = tmp_path / "left.txt"
    right = tmp_path / "right.txt"
    left.write_text("Same  \n")
    right.write_text("same\n")
    window = MainWindow()
    qtbot.addWidget(window)
    window.tabs.setCurrentWidget(window.file_comparison)
    window.open_path(window.left, left)
    window.open_path(window.right, right)
    qtbot.waitUntil(lambda: window.left.state.path == left and window.right.state.path == right)
    window.settings_page.compare_checks["ignore_whitespace"].setChecked(True)
    window.settings_page.compare_checks["ignore_case"].setChecked(True)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("My pair", True))
    window._save_comparison()
    assert load_sessions()["saved"][0]["name"] == "My pair"
    window.settings_page.compare_checks["ignore_case"].setChecked(False)
    window._open_comparison(load_sessions()["saved"][0])
    qtbot.waitUntil(lambda: not window._busy_files)
    assert window.compare_options.ignore_whitespace
    assert window.compare_options.ignore_case
    window.close()
