"""Recursive directory comparison without Qt dependencies."""

import fnmatch
import os
from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path
from threading import Event


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    relative_path: str
    kind: str
    status: str
    left_path: Path | None
    right_path: Path | None
    left_kind: str | None
    right_kind: str | None


@dataclass(frozen=True, slots=True)
class _ScannedEntry:
    path: Path
    kind: str
    size: int = 0


def _check_cancelled(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise CancelledError()


def _ignored(name: str, relative_path: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        if "/" not in pattern:
            if fnmatch.fnmatchcase(name, pattern):
                return True
        elif fnmatch.fnmatchcase(relative_path, pattern):
            return True
        elif pattern.startswith("**/") and fnmatch.fnmatchcase(relative_path, pattern[3:]):
            return True
    return False


def _scan(root: Path, patterns: tuple[str, ...], cancel: Event | None) -> dict[str, _ScannedEntry]:
    result: dict[str, _ScannedEntry] = {}

    def visit(folder: Path, parent: str) -> None:
        _check_cancelled(cancel)
        with os.scandir(folder) as scan:
            children = sorted(scan, key=lambda entry: (entry.name.casefold(), entry.name))
        for child in children:
            _check_cancelled(cancel)
            relative = f"{parent}/{child.name}" if parent else child.name
            if _ignored(child.name, relative, patterns):
                continue
            path = Path(child.path)
            if child.is_symlink():
                result[relative] = _ScannedEntry(path, "symlink")
            elif child.is_dir(follow_symlinks=False):
                result[relative] = _ScannedEntry(path, "directory")
                visit(path, relative)
            elif child.is_file(follow_symlinks=False):
                result[relative] = _ScannedEntry(path, "file", child.stat(follow_symlinks=False).st_size)

    visit(root, "")
    return result


def _same_file(left: Path, right: Path, cancel: Event | None) -> bool:
    with left.open("rb") as left_file, right.open("rb") as right_file:
        while True:
            _check_cancelled(cancel)
            left_chunk = left_file.read(1024 * 1024)
            right_chunk = right_file.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def compare_directories(
    left_root: Path, right_root: Path, patterns: tuple[str, ...] = (), cancel: Event | None = None,
) -> tuple[DirectoryEntry, ...]:
    """Compare names, types and file bytes; never follow symbolic links."""
    left_root = Path(left_root).expanduser().resolve()
    right_root = Path(right_root).expanduser().resolve()
    for root in (left_root, right_root):
        if not root.is_dir():
            raise ValueError(f"Not a directory: {root}")
    patterns = tuple(pattern.strip() for pattern in patterns if pattern.strip())
    left = _scan(left_root, patterns, cancel)
    right = _scan(right_root, patterns, cancel)
    union = set(left) | set(right)
    entries: dict[str, DirectoryEntry] = {}
    changed_children: set[str] = set()

    for relative in sorted(union, key=lambda path: path.count("/"), reverse=True):
        _check_cancelled(cancel)
        left_entry = left.get(relative)
        right_entry = right.get(relative)
        if left_entry is None:
            status = "right_only"
        elif right_entry is None:
            status = "left_only"
        elif left_entry.kind != right_entry.kind:
            status = "type_changed"
        elif left_entry.kind == "directory":
            status = "different" if relative in changed_children else "same"
        elif left_entry.kind == "symlink":
            status = "same" if os.readlink(left_entry.path) == os.readlink(right_entry.path) else "different"
        else:
            status = "same" if left_entry.size == right_entry.size and _same_file(
                left_entry.path, right_entry.path, cancel
            ) else "different"
        entries[relative] = DirectoryEntry(
            relative, (left_entry or right_entry).kind, status,
            left_entry.path if left_entry else None,
            right_entry.path if right_entry else None,
            left_entry.kind if left_entry else None,
            right_entry.kind if right_entry else None,
        )
        if status != "same" and "/" in relative:
            changed_children.add(relative.rsplit("/", 1)[0])

    return tuple(entries[path] for path in sorted(
        union, key=lambda path: (path.casefold(), path)
    ))
