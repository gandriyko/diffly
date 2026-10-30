"""Safe byte-for-byte file copying for directory comparison."""

import os
import shutil
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


def path_stamp(path: Path) -> tuple[int, int, int, int] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    return stat.S_IFMT(info.st_mode), info.st_size, info.st_mtime_ns, info.st_ino


def directory_stamp(path: Path) -> tuple[int, int, int, int] | None:
    """Return a root stamp for checking that a selected folder has not changed type."""
    stamp = path_stamp(path)
    if stamp is not None and stamp[0] != stat.S_IFDIR:
        raise RuntimeError(f"Not a directory: {path}")
    return stamp


def _tree_snapshot(root: Path) -> tuple:
    """Capture metadata for every entry without following symbolic links."""
    result = []

    def visit(path: Path, relative: str):
        stamp = path_stamp(path)
        if stamp is None:
            raise RuntimeError(f"Path changed during folder copy: {path}")
        link = os.readlink(path) if stamp[0] == stat.S_IFLNK else None
        result.append((relative, stamp, link))
        if stamp[0] == stat.S_IFDIR:
            with os.scandir(path) as scan:
                children = sorted(scan, key=lambda item: item.name)
            for child in children:
                name = f"{relative}/{child.name}" if relative else child.name
                visit(Path(child.path), name)

    visit(root, "")
    return tuple(result)


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def copy_directory_checked(
    source: Path, target_root: Path, relative_path: str,
    expected_source: tuple[int, int, int, int],
    expected_target: tuple[int, int, int, int] | None,
) -> tuple[Path, Path | None]:
    """Replace a whole folder from a staged copy, restoring the old tree on failure."""
    relative = Path(relative_path)
    if not relative.parts or relative.is_absolute() or any(part in (".", "..") for part in relative.parts):
        raise RuntimeError("Invalid relative folder path")
    target_root = target_root.resolve()
    target = target_root / relative
    source = source.absolute()
    source_resolved = source.resolve()
    target_resolved = target.resolve()
    if (source_resolved == target_resolved or source_resolved.is_relative_to(target_resolved)
            or target_resolved.is_relative_to(source_resolved)):
        raise RuntimeError("Source and destination folders overlap")
    if directory_stamp(source) != expected_source:
        raise RuntimeError("Source folder changed. Compare the directories again.")
    _check_parent(target_root, target)
    if path_stamp(target) != expected_target:
        raise RuntimeError("Destination changed. Compare the directories again.")
    source_snapshot = _tree_snapshot(source)
    target_snapshot = _tree_snapshot(target) if expected_target is not None else None
    target.parent.mkdir(parents=True, exist_ok=True)
    _check_parent(target_root, target)
    stage = Path(tempfile.mkdtemp(dir=target.parent, prefix=f".{target.name}.stage-"))
    backup = None
    try:
        shutil.copytree(source, stage, symlinks=True, dirs_exist_ok=True)
        if _tree_snapshot(source) != source_snapshot:
            raise RuntimeError("Source folder changed during copy.")
        if path_stamp(target) != expected_target or (
            target_snapshot is not None and _tree_snapshot(target) != target_snapshot
        ):
            raise RuntimeError("Destination changed during copy.")
        _check_parent(target_root, target)
        if target_snapshot is not None:
            backup = Path(tempfile.mkdtemp(dir=target.parent, prefix=f".{target.name}.backup-"))
            backup.rmdir()
            os.replace(target, backup)
        try:
            os.replace(stage, target)
            stage = None
        except OSError as exc:
            if backup is not None:
                try:
                    os.replace(backup, target)
                except OSError as rollback_error:
                    raise RuntimeError(
                        f"Copy failed; original folder remains at {backup}: {rollback_error}"
                    ) from exc
                backup = None
            raise
        if backup is not None:
            try:
                _remove_path(backup)
            except OSError:
                return target, backup
        return target, None
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)
