from diffly.gui.theme import COLOR_KEYS, load_config, save_ignore_patterns, save_indent_width, save_selected_theme


def test_bundled_themes_and_toml_override(tmp_path, monkeypatch):
    config = load_config()
    assert {"gruvbox", "dark", "light", "solarized_dark"} <= set(config.themes)
    assert all(all(key in theme for key in COLOR_KEYS) for theme in config.themes.values())
    override = tmp_path / "config.toml"
    override.write_text('[settings]\ntheme = "light"\nfont_size = 15\n[themes.gruvbox]\naccent = "#ffffff"\n')
    monkeypatch.setenv("DIFFLY_CONFIG", str(override))
    customized = load_config()
    assert customized.settings["theme"] == "light"
    assert customized.settings["font_size"] == 15
    assert customized.themes["gruvbox"]["accent"] == "#ffffff"
    assert customized.themes["gruvbox"]["background"] == config.themes["gruvbox"]["background"]


def test_theme_selection_persists_without_losing_other_settings(tmp_path, monkeypatch):
    override = tmp_path / "diffly" / "config.toml"
    override.parent.mkdir()
    override.write_text('[settings]\nfont_size = 15\ntheme = "dark"\n\n[themes.gruvbox]\naccent = "#ffffff"\n')
    monkeypatch.setenv("DIFFLY_CONFIG", str(override))
    save_selected_theme("light")
    assert load_config().settings["theme"] == "light"
    assert load_config().settings["font_size"] == 15
    assert load_config().themes["gruvbox"]["accent"] == "#ffffff"
    save_selected_theme("solarized_dark")
    assert override.read_text().count('theme = "solarized_dark"') == 1


def test_indent_width_persists_both_grid_settings(tmp_path, monkeypatch):
    override = tmp_path / "diffly" / "config.toml"
    override.parent.mkdir()
    override.write_text('[settings]\ntheme = "dark"\ntab_width = 4\n\n[themes.gruvbox]\naccent = "#ffffff"\n')
    monkeypatch.setenv("DIFFLY_CONFIG", str(override))

    save_indent_width(2)
    customized = load_config()
    assert customized.settings["tab_width"] == 2
    assert customized.settings["indent_guide_width"] == 2
    assert customized.settings["theme"] == "dark"
    assert customized.themes["gruvbox"]["accent"] == "#ffffff"

    save_indent_width(4)
    assert override.read_text().count("tab_width = 4") == 1
    assert override.read_text().count("indent_guide_width = 4") == 1


def test_directory_ignore_masks_persist_without_replacing_other_settings(tmp_path, monkeypatch):
    override = tmp_path / "config.toml"
    override.write_text('[settings]\ntheme = "dark"\n[themes.gruvbox]\naccent = "#ffffff"\n')
    monkeypatch.setenv("DIFFLY_CONFIG", str(override))
    save_ignore_patterns(["*.log", "node_modules", "*.log"])
    config = load_config()
    assert config.settings["ignore_patterns"] == ["*.log", "node_modules"]
    assert config.settings["theme"] == "dark"
    assert config.themes["gruvbox"]["accent"] == "#ffffff"
