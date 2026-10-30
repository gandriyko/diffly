"""Safe byte-for-byte file copying for directory comparison."""

import os
import stat
import tempfile
from pathlib import Path

from diffly.core.documents.model import FileStamp


def regular_file_stamp(path: Path) -> FileStamp | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"Not a regular file: {path}")
    return FileStamp(info.st_size, info.st_mtime_ns, info.st_ino)


def _check_parent(root: Path, target: Path) -> None:
    try:
        parts = target.parent.relative_to(root).parts
    except ValueError as exc:
        raise RuntimeError("Destination is outside the selected directory") from exc
    current = root
    for part in parts:
        current /= part
        if current.is_symlink():
            raise RuntimeError(f"Destination folder is a symbolic link: {current}")


def copy_file_checked(
    source: Path, target_root: Path, relative_path: str,
    expected_source: FileStamp, expected_target: FileStamp | None,
) -> Path:
    """Copy a regular file atomically, refusing changed source or destination."""
    parts = Path(relative_path).parts
    if not parts or Path(relative_path).is_absolute() or any(part in (".", "..") for part in parts):
        raise RuntimeError("Invalid relative file path")
    target_root = target_root.resolve()
    target = target_root / relative_path
    if source == target:
        raise RuntimeError("Source and destination are the same file")
    if regular_file_stamp(source) != expected_source:
        raise RuntimeError("Source file changed. Compare the directories again.")
    _check_parent(target_root, target)
    if regular_file_stamp(target) != expected_target:
        raise RuntimeError("Destination file changed. Compare the directories again.")
    target.parent.mkdir(parents=True, exist_ok=True)
    _check_parent(target_root, target)

    temp_name = None
    try:
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(source_fd, "rb") as input_file, tempfile.NamedTemporaryFile(
            "wb", dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as output:
            info = os.fstat(input_file.fileno())
            if not stat.S_ISREG(info.st_mode) or FileStamp(info.st_size, info.st_mtime_ns, info.st_ino) != expected_source:
                raise RuntimeError("Source file changed. Compare the directories again.")
            temp_name = output.name
            mode = (target if expected_target is not None else source).stat().st_mode
            os.fchmod(output.fileno(), stat.S_IMODE(mode))
            while chunk := input_file.read(1024 * 1024):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if regular_file_stamp(source) != expected_source:
            raise RuntimeError("Source file changed during copy.")
        if regular_file_stamp(target) != expected_target:
            raise RuntimeError("Destination file changed during copy.")
        _check_parent(target_root, target)
        os.replace(temp_name, target)
        temp_name = None
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return target
    finally:
        if temp_name is not None:
            os.unlink(temp_name)
