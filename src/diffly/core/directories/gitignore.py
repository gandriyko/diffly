"""Match local .gitignore rules while scanning a selected directory tree."""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Rule:
    base: Path
    regex: re.Pattern
    basename: bool
    directories_only: bool
    ignored: bool

    def matches(self, path: Path, is_dir: bool) -> bool:
        if self.directories_only and not is_dir:
            return False
        try:
            relative = path.relative_to(self.base).as_posix()
        except ValueError:
            return False
        if relative == ".":
            return False
        if self.basename:
            return any(self.regex.fullmatch(part) for part in relative.split("/"))
        return self.regex.fullmatch(relative) is not None


def _compile_glob(pattern: str) -> re.Pattern:
    output = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\" and index + 1 < len(pattern):
            output.append(re.escape(pattern[index + 1]))
            index += 2
        elif char == "*":
            if pattern[index:index + 3] == "**/":
                output.append("(?:.*/)?")
                index += 3
            elif pattern[index:index + 2] == "**":
                output.append(".*")
                index += 2
            else:
                output.append("[^/]*")
                index += 1
        elif char == "?":
            output.append("[^/]")
            index += 1
        elif char == "[":
            end = pattern.find("]", index + 1)
            if end > index + 1:
                content = pattern[index + 1:end]
                if content.startswith("!"):
                    content = "^" + content[1:]
                output.append("[" + content.replace("\\", "\\\\") + "]")
                index = end + 1
            else:
                output.append(r"\[")
                index += 1
        else:
            output.append(re.escape(char))
            index += 1
    return re.compile("".join(output))


def read_rules(folder: Path) -> tuple[Rule, ...]:
    source = folder / ".gitignore"
    if source.is_symlink() or not source.is_file():
        return ()
    rules = []
    for raw in source.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        # Git discards unescaped trailing spaces.
        line = raw
        while line.endswith(" ") and not line.endswith("\\ "):
            line = line[:-1]
        if not line or line.startswith("#"):
            continue
        escaped_prefix = line.startswith(r"\#") or line.startswith(r"\!")
        if escaped_prefix:
            line = line[1:]
        ignored = escaped_prefix or not line.startswith("!")
        if not ignored:
            line = line[1:]
        if not line:
            continue
        directories_only = line.endswith("/")
        pattern = line.strip("/")
        if not pattern:
            continue
        basename = "/" not in pattern and not line.startswith("/")
        try:
            rules.append(Rule(folder, _compile_glob(pattern), basename, directories_only, ignored))
        except re.error:
            continue
    return tuple(rules)


def ancestor_rules(root: Path) -> tuple[Rule, ...]:
    """Load parent rules down to the selected root, respecting a Git worktree boundary."""
    folders = [root, *root.parents]
    for index, folder in enumerate(folders):
        if (folder / ".git").exists():
            folders = folders[:index + 1]
            break
    rules = []
    for folder in reversed(folders):
        rules.extend(read_rules(folder))
    return tuple(rules)


def is_ignored(path: Path, is_dir: bool, rules: tuple[Rule, ...]) -> bool:
    ignored = False
    for rule in rules:
        if rule.matches(path, is_dir):
            ignored = rule.ignored
    return ignored
