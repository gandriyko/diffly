from diffly.gui.main_window import MainWindow
from diffly.gui.theme import load_config


def test_settings_apply_and_survive_relaunch(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("DIFFLY_CONFIG", str(tmp_path / "config.toml"))
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.tabs.setCurrentWidget(window.settings_page)
    page = window.settings_page
    assert window.tabs.currentWidget() is page
    assert page.theme_choice.height() == page.font_size.height() == page.indent_width.height()

    page.theme_choice.setCurrentIndex(page.theme_choice.findData("light"))
    page.font_size.setText("16")
    page.font_size.editingFinished.emit()
    page.indent_width.setCurrentIndex(0)
    page.compare_checks["ignore_case"].setChecked(True)
    page.gitignore.setChecked(False)
    page.preset_checks["build"].setChecked(False)
    page.pattern_input.setText("*.log")
    page._add_pattern()

    settings = load_config().settings
    assert settings["theme"] == "light"
    assert settings["font_size"] == 16
    assert settings["tab_width"] == settings["indent_guide_width"] == 2
    assert settings["ignore_case"] is True
    assert settings["use_gitignore"] is False
    assert settings["ignore_presets"] == ["python", "temporary"]
    assert settings["ignore_patterns"] == ["*.log"]
    assert window.compare_options.ignore_case
    assert window.directory_comparison.patterns == ["*.log"]
    page.font_size.setText("99")
    page.font_size.editingFinished.emit()
    assert page.font_size.text() == "16"
    assert load_config().settings["font_size"] == 16
    window.close()

    reopened = MainWindow()
    qtbot.addWidget(reopened)
    assert reopened.settings_page.compare_checks["ignore_case"].isChecked()
    assert reopened.settings_page.patterns() == ["*.log"]
    assert reopened.compare_options.ignore_case
    reopened.close()
