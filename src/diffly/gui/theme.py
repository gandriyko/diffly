"""Load bundled themes and an optional user TOML override."""

import json
import os
import re
import stat
import tempfile
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication


COLOR_KEYS = (
    "background", "surface", "text", "muted", "border", "accent",
    "selection", "diff_replace", "diff_added", "diff_removed", "diff_text",
    "overview_replace", "overview_added", "overview_removed", "button", "button_hover",
    "syntax_keyword", "syntax_string", "syntax_number", "syntax_comment", "syntax_key",
    "syntax_function",
)


@dataclass(frozen=True)
class ThemeConfig:
    settings: dict
    themes: dict[str, dict]


def load_config() -> ThemeConfig:
    with files("diffly").joinpath("config.toml").open("rb") as source:
        data = tomllib.load(source)
    override = user_config_path()
    if override.is_file():
        with override.open("rb") as source:
            custom = tomllib.load(source)
        data["settings"].update(custom.get("settings", {}))
        for key, values in custom.get("themes", {}).items():
            data["themes"].setdefault(key, {}).update(values)
    return ThemeConfig(data["settings"], data["themes"])


def user_config_path() -> Path:
    override_name = os.environ.get("DIFFLY_CONFIG")
    return Path(override_name).expanduser() if override_name else Path.home() / ".config/diffly/config.toml"


def save_selected_theme(name: str) -> None:
    """Persist a theme choice while keeping other TOML settings and sections."""
    _save_settings({"theme": f'"{name}"'})


def save_font_size(size: int) -> None:
    _save_settings({"font_size": str(size)})


def save_comparison_options(options) -> None:
    _save_settings({key: "true" if getattr(options, key) else "false" for key in
                    ("ignore_whitespace", "ignore_trailing_whitespace", "ignore_case")})


def save_indent_width(width: int) -> None:
    """Keep tab stops and indent guides on the same two/four-space grid."""
    if width not in (2, 4):
        raise ValueError("Indent width must be 2 or 4")
    _save_settings({"tab_width": str(width), "indent_guide_width": str(width)})


def save_ignore_patterns(patterns: list[str]) -> None:
    """Persist directory ignore masks without replacing other settings."""
    unique = list(dict.fromkeys(pattern.strip() for pattern in patterns if pattern.strip()))
    _save_settings({"ignore_patterns": json.dumps(unique, ensure_ascii=False)})


def save_directory_filters(patterns: list[str], use_gitignore: bool,
                           enabled_presets: tuple[str, ...]) -> None:
    unique = list(dict.fromkeys(pattern.strip() for pattern in patterns if pattern.strip()))
    _save_settings({"ignore_patterns": json.dumps(unique, ensure_ascii=False),
                    "use_gitignore": "true" if use_gitignore else "false",
                    "ignore_presets": json.dumps(enabled_presets)})


def _save_settings(values: dict[str, str]) -> None:
    path = user_config_path()
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    newline = "\r\n" if "\r\n" in original else "\n"
    lines = original.splitlines(keepends=True)
    settings_start = None
    settings_end = len(lines)
    for index, line in enumerate(lines):
        section = re.match(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$", line.strip())
        if section:
            if settings_start is not None:
                settings_end = index
                break
            if section.group(1).strip() == "settings":
                settings_start = index
    if settings_start is None:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        if lines and lines[-1].strip():
            lines.append(newline)
        lines.append(f"[settings]{newline}")
        lines.extend(f"{key} = {value}{newline}" for key, value in values.items())
    else:
        body = lines[settings_start + 1:settings_end]
        missing = []
        for key, value in values.items():
            replacement = f"{key} = {value}{newline}"
            for index, line in enumerate(body):
                if re.match(rf"^\s*{re.escape(key)}\s*=", line):
                    body[index] = replacement
                    break
            else:
                missing.append(replacement)
        body[0:0] = missing
        lines[settings_start + 1:settings_end] = body
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, prefix=".config-", delete=False) as output:
            temporary = Path(output.name)
            output.write("".join(lines))
            output.flush()
            os.fsync(output.fileno())
        if mode is not None:
            temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def editor_font(settings: dict) -> QFont:
    family = settings.get("font_family") or QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    font = QFont(family)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    font.setPointSize(max(8, int(settings.get("font_size", 13))))
    return font


def apply_theme(app: QApplication, theme: dict):
    missing = [key for key in COLOR_KEYS if key not in theme]
    if missing:
        raise ValueError(f"Theme is missing colors: {', '.join(missing)}")
    c = theme
    toolbar_style = f"""
        QToolBar#DifflyToolbar {{ background: {c['surface']}; border: none;
            spacing: 5px; padding: 5px 8px; }}
        QToolBar#DifflyToolbar QToolButton {{ color: {c['text']}; background: transparent;
            border: 1px solid transparent; border-radius: 6px; padding: 5px 7px; }}
        QToolBar#DifflyToolbar QToolButton:hover:!disabled {{ background: transparent;
            border-color: {c['border']}; }}
        QToolBar#DifflyToolbar QToolButton:disabled {{ color: {c['muted']}; }}
    """
    app.setStyleSheet(f"""
        QMainWindow, QWidget#DifflyRoot {{ background: {c['surface']}; color: {c['text']}; }}
        QWidget#FindPanel {{ background: {c['surface']}; border-top: 1px solid {c['border']}; }}
        QLabel#FindTarget {{ color: {c['accent']}; font-weight: bold; }}
        QLabel#FindMessage {{ color: {c['muted']}; }}
        QToolButton#FindPanelButton {{ color: {c['text']}; background: {c['button']};
            border: 1px solid {c['border']}; border-radius: 4px; min-width: 26px; padding: 4px; }}
        QToolButton#FindPanelButton:hover {{ background: {c['button_hover']}; }}
        QLabel, QMenuBar, QMenu, QStatusBar {{ color: {c['text']}; background: {c['surface']}; }}
        QLabel#PaneTitle {{ color: {c['text']}; padding: 5px 8px; }}
        QWidget#LinuxTitleBar {{ background: {c['surface']}; border-bottom: 1px solid {c['border']}; }}
        QLabel#LinuxWindowTitle {{ color: {c['muted']}; padding: 0 8px; }}
        QToolButton#LinuxWindowButton {{ color: {c['text']}; background: transparent;
            border: 1px solid transparent; border-radius: 5px; }}
        QToolButton#LinuxWindowButton:hover {{ border-color: {c['border']}; }}
        QToolButton#EncodingButton {{ color: {c['muted']}; background: transparent;
            border: 1px solid transparent; border-radius: 5px; padding: 2px 6px; }}
        QToolButton#EncodingButton:hover {{ border-color: {c['border']}; }}
        QWidget#SettingsPage, QWidget#SettingsContent {{ background: {c['surface']}; color: {c['text']}; }}
        QWidget#SettingsPage QLabel {{ background: transparent; }}
        QLabel#SettingsTitle {{ color: {c['text']}; font-size: 20px; font-weight: bold; }}
        QLabel#SettingsSectionTitle {{ color: {c['text']}; font-size: 14px; font-weight: bold; }}
        QLabel#SettingsSubheading {{ color: {c['muted']}; font-size: 12px; font-weight: bold; padding-top: 8px; }}
        QLabel#SettingsHint {{ color: {c['muted']}; }}
        QWidget#SettingsCard {{ color: {c['text']}; background: {c['background']};
            border: 1px solid {c['border']}; border-radius: 8px; }}
        QFrame#SettingsDivider {{ color: {c['border']}; background: {c['border']};
            border: none; max-height: 1px; }}
        QWidget#SettingsPage QCheckBox {{ color: {c['text']}; spacing: 9px; }}
        QToolButton#EncodingButton::menu-indicator {{ image: none; width: 0px; }}
        QToolButton#DirectoryBrowseButton {{ background: {c['button']};
            border: 1px solid {c['border']}; border-radius: 4px; }}
        QToolButton#DirectoryBrowseButton:hover {{ background: {c['button_hover']}; }}
        QTabWidget#ComparisonTabs::pane {{ border: none; }}
        QTabBar::tab {{ color: {c['muted']}; background: {c['surface']};
            border: none; border-bottom: 2px solid transparent; padding: 7px 18px; }}
        QTabBar::tab:selected {{ color: {c['text']}; border-bottom-color: {c['accent']}; }}
        QTabBar::tab:hover {{ color: {c['text']}; }}
        QTreeWidget {{ color: {c['text']}; background: {c['background']};
            alternate-background-color: {c['surface']}; border: 1px solid {c['border']}; }}
        QTreeWidget::item:selected {{ background: {c['selection']}; }}
        QDialog {{ color: {c['text']}; background: {c['surface']}; }}
        QListWidget {{ color: {c['text']}; background: {c['background']};
            border: 1px solid {c['border']}; }}
        QListWidget::item:selected {{ background: {c['selection']}; }}
        QHeaderView::section {{ color: {c['text']}; background: {c['surface']};
            border: none; border-bottom: 1px solid {c['border']}; padding: 5px; }}
        QLineEdit {{ color: {c['text']}; background: {c['background']};
            border: 1px solid {c['border']}; border-radius: 4px; padding: 5px; }}
        QPushButton {{ color: {c['text']}; background: {c['button']};
            border: 1px solid {c['border']}; border-radius: 4px; padding: 5px 9px; }}
        QPushButton:hover {{ background: {c['button_hover']}; }}
        QStatusBar {{ border: none; padding: 2px 6px; }}
        QStatusBar::item {{ border: none; }}
        QMenuBar::item:selected, QMenu::item:selected {{ background: {c['selection']}; }}
        QPlainTextEdit {{ background: {c['background']}; color: {c['text']};
            border: none; selection-background-color: {c['selection']};
            selection-color: {c['text']}; }}
        QScrollBar:vertical {{ background: {c['surface']}; width: 10px; margin: 0; border: none; }}
        QScrollBar:horizontal {{ background: {c['surface']}; height: 10px; margin: 0; border: none; }}
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: {c['border']};
            border-radius: 5px; min-height: 24px; min-width: 24px; }}
        QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {c['muted']}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
        {toolbar_style}
        QToolTip {{ color: {c['text']}; background: {c['surface']}; border: 1px solid {c['border']}; }}
    """)
