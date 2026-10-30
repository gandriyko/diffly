from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog, QMessageBox, QToolButton

from diffly.gui.main_window import MainWindow
from diffly.gui.theme import load_config


def test_directory_tab_scans_in_background_and_opens_file_pair(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("DIFFLY_CONFIG", str(tmp_path / "config.toml"))
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "changed.txt").write_text("left")
    (right / "changed.txt").write_text("rght")
    (left / "ignored.log").write_text("left")
    (right / "ignored.log").write_text("right")
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window.tabs.tabText(0) == "Directories"
    assert window.tabs.tabText(1) == "Files"
    assert window.tabs.tabText(2) == "Settings"
    assert window.windowTitle() == "Directories — Diffly"
    view = window.directory_comparison
    assert view.left_path.parent() is view.left_pane
    assert view.right_path.parent() is view.right_pane
    browse_buttons = view.findChildren(QToolButton, "DirectoryBrowseButton")
    assert len(browse_buttons) == 2
    assert all(button.text() == "" and not button.icon().isNull() for button in browse_buttons)
    view.left_path.setText(str(left))
    view.right_path.setText(str(right))
    view.set_patterns(["*.log"])
    qtbot.waitUntil(lambda: bool(view._entries), timeout=5000)

    assert list(view._entries) == ["changed.txt"]
    assert view._entries["changed.txt"].status == "different"
    assert "1 different" in window.directory_summary_label.text()
    assert not hasattr(view, "summary")
    assert not window.findChildren(QToolButton, "SettingsButton")
    assert not window.paste_action.isEnabled()
    item = view.left_tree.topLevelItem(0)
    assert item.background(0).color() == QColor(window.config.themes[window._active_theme_name]["diff_replace"])
    view.left_tree.itemDoubleClicked.emit(item, 0)
    qtbot.waitUntil(lambda: window.left.state.path == left / "changed.txt"
                    and window.right.state.path == right / "changed.txt", timeout=5000)
    assert window.tabs.currentWidget() is window.file_comparison
    assert window.left.text() == "left"
    assert window.right.text() == "rght"
    window.close()


def test_settings_page_adds_edits_and_removes_patterns(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("DIFFLY_CONFIG", str(tmp_path / "config.toml"))
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.tabs.setCurrentWidget(window.settings_page)
    assert window.tabs.currentWidget() is window.settings_page
    page = window.settings_page
    page.pattern_input.setText("*.log")
    page._add_pattern()
    page.pattern_input.setText("node_modules")
    page._add_pattern()
    assert page.patterns() == ["*.log", "node_modules"]
    page.patterns_list.setCurrentRow(0)
    page._remove_selected()
    assert page.patterns() == ["node_modules"]
    page.patterns_list.item(0).setText("*.tmp")
    assert load_config().settings["ignore_patterns"] == ["*.tmp"]
    assert window.directory_comparison.patterns == ["*.tmp"]
    window.close()


def test_filter_presets_and_gitignore_setting(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("DIFFLY_CONFIG", str(tmp_path / "config.toml"))
    window = MainWindow()
    qtbot.addWidget(window)
    page = window.settings_page
    assert page.enabled_presets() == ("python", "temporary", "build")
    page.pattern_input.setText("custom.log")
    page._add_pattern()
    page.preset_checks["build"].setChecked(False)
    page.gitignore.setChecked(False)
    config = load_config()
    assert config.settings["use_gitignore"] is False
    assert config.settings["ignore_patterns"] == ["custom.log"]
    assert config.settings["ignore_presets"] == ["python", "temporary"]
    assert window.directory_comparison.use_gitignore is False
    window.close()


def test_directory_toolbar_navigates_and_copies_with_session_confirmation(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("DIFFLY_CONFIG", str(tmp_path / "config.toml"))
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "a.txt").write_bytes(b"left")
    (right / "a.txt").write_bytes(b"right")
    (left / "sub").mkdir()
    (left / "sub" / "b.bin").write_bytes(b"\x00\xff")
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    view = window.directory_comparison
    view.left_path.setText(str(left))
    view.right_path.setText(str(right))
    view.compare()
    qtbot.waitUntil(lambda: len(view._entries) == 3)
    assert view.selected_entry().relative_path == "a.txt"
    assert window.apply_left_action.isEnabled() and window.apply_right_action.isEnabled()
    window.next_action.trigger()
    assert view.selected_entry().relative_path == "sub/b.bin"
    assert not window.apply_left_action.isEnabled() and window.apply_right_action.isEnabled()
    window.previous_action.trigger()
    assert view.selected_entry().relative_path == "a.txt"

    responses = [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes]
    calls = []

    def answer(dialog):
        calls.append(dialog.informativeText())
        response = responses.pop(0)
        if response == QMessageBox.StandardButton.Yes:
            dialog.checkBox().setChecked(True)
        return response

    monkeypatch.setattr(QMessageBox, "exec", answer)
    window.apply_right_action.trigger()
    assert (right / "a.txt").read_bytes() == b"right"
    window.apply_right_action.trigger()
    qtbot.waitUntil(lambda: (right / "a.txt").read_bytes() == b"left")
    qtbot.waitUntil(lambda: view._entries.get("a.txt") is not None and view._entries["a.txt"].status == "same")
    view.select_path("sub/b.bin")
    window.apply_right_action.trigger()
    qtbot.waitUntil(lambda: (right / "sub" / "b.bin").exists())
    assert (right / "sub" / "b.bin").read_bytes() == b"\x00\xff"
    assert len(calls) == 2
    assert window._skip_copy_confirmation
    window.close()


def test_selected_folder_copy_replaces_all_children(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("DIFFLY_CONFIG", str(tmp_path / "config.toml"))
    left, right = tmp_path / "left", tmp_path / "right"
    (left / "sub" / "nested").mkdir(parents=True)
    (right / "sub" / "nested").mkdir(parents=True)
    (left / "sub" / "nested" / "new.txt").write_text("new")
    (left / "sub" / "cache.pyc").write_text("ignored but copied")
    (right / "sub" / "nested" / "old.txt").write_text("old")
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    view = window.directory_comparison
    view.left_path.setText(str(left))
    view.right_path.setText(str(right))
    view.compare()
    qtbot.waitUntil(lambda: "sub" in view._entries)
    view.select_path("sub")
    assert window.apply_right_action.isEnabled()
    prompts = []

    def confirm(dialog):
        prompts.append(dialog.text())
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "exec", confirm)
    window._skip_copy_confirmation = True
    window.apply_right_action.trigger()
    qtbot.waitUntil(lambda: (right / "sub" / "nested" / "new.txt").exists())
    assert (right / "sub" / "cache.pyc").read_text() == "ignored but copied"
    assert not (right / "sub" / "nested" / "old.txt").exists()
    assert prompts == ["Replace this folder and all its contents?"]
    qtbot.waitUntil(lambda: view._entries.get("sub") is not None and view._entries["sub"].status == "same")
    window.close()


def test_directory_selection_enter_and_toolbar_refresh(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("DIFFLY_CONFIG", str(tmp_path / "config.toml"))
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "one.txt").write_text("left")
    (right / "one.txt").write_text("right")
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    view = window.directory_comparison
    assert not hasattr(view, "compare_button")
    assert window.toolbar.actions()[0] is window.refresh_action
    choices = iter((str(left), str(right)))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: next(choices))
    view._browse(view.left_path)
    assert not view._entries
    view._browse(view.right_path)
    qtbot.waitUntil(lambda: view._entries.get("one.txt") is not None)
    assert view._entries["one.txt"].status == "different"

    (right / "one.txt").write_text("left")
    window.refresh_action.trigger()
    qtbot.waitUntil(lambda: view._entries.get("one.txt") is not None and view._entries["one.txt"].status == "same")
    (right / "one.txt").write_text("new")
    view.right_path.returnPressed.emit()
    qtbot.waitUntil(lambda: view._entries.get("one.txt") is not None and view._entries["one.txt"].status == "different")
    window.close()
