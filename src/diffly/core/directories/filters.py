"""Built-in directory ignore groups and their default state."""

PRESETS = {
    "python": ("Python caches", ("__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".mypy_cache", ".ruff_cache")),
    "temporary": ("Editor and temporary files", ("*~", "*.~", "*.swp", "*.swo", "*.tmp", ".DS_Store", "Thumbs.db")),
    "build": ("Build and dependencies", (".git", ".venv", "node_modules", "build", "dist")),
}

DEFAULT_PRESETS = tuple(PRESETS)


def effective_patterns(custom: tuple[str, ...], enabled: tuple[str, ...]) -> tuple[str, ...]:
    patterns = list(custom)
    for key in enabled:
        if key in PRESETS:
            patterns.extend(PRESETS[key][1])
    return tuple(dict.fromkeys(patterns))
