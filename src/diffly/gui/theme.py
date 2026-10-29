"""Load bundled themes and an optional user TOML override."""

import os
import re
import stat
import sys
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
    theme_line = f'theme = "{name}"{newline}'
    if settings_start is None:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        if lines and lines[-1].strip():
            lines.append(newline)
        lines.extend((f"[settings]{newline}", theme_line))
    else:
        for index in range(settings_start + 1, settings_end):
            if re.match(r"^\s*theme\s*=", lines[index]):
                lines[index] = theme_line
                break
        else:
            lines.insert(settings_start + 1, theme_line)
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
    native_mac_toolbar = sys.platform == "darwin" and app.platformName() == "cocoa"
    toolbar_style = "" if native_mac_toolbar else f"""
        QToolBar#DifflyToolbar {{ background: {c['surface']}; border: none;
            spacing: 5px; padding: 5px 8px; }}
        QToolBar#DifflyToolbar QToolButton {{ color: {c['text']}; background: transparent;
            border: 1px solid transparent; border-radius: 6px; padding: 5px 7px; }}
        QToolBar#DifflyToolbar QToolButton:hover {{ background: {c['button_hover']};
            border-color: {c['border']}; }}
        QToolBar#DifflyToolbar QToolButton:disabled {{ color: {c['muted']}; }}
    """
    app.setStyleSheet(f"""
        QMainWindow, QWidget#DifflyRoot {{ background: {c['surface']}; color: {c['text']}; }}
        QLabel, QMenuBar, QMenu, QStatusBar {{ color: {c['text']}; background: {c['surface']}; }}
        QLabel#PaneTitle {{ color: {c['text']}; padding: 5px 8px; }}
        QLabel#FileInfo {{ color: {c['muted']}; font-size: 11px; padding: 2px 8px; }}
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
