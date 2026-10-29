"""Blocking file operations; callers run these in a worker thread."""

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from diffly.core.documents.model import FileStamp


@dataclass(frozen=True, slots=True)
class LoadedFile:
    text: str
    newline: str
    bom: bool
    stamp: FileStamp


def file_stamp(path: Path) -> FileStamp:
    info = path.stat()
    return FileStamp(info.st_size, info.st_mtime_ns, info.st_ino)


def read_file(path: Path) -> LoadedFile:
    with path.open("rb") as source:
        before = os.fstat(source.fileno())
        raw = source.read()
        after = os.fstat(source.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("File changed while reading. Please retry.")
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if b"\r\n" in raw else "\r" if b"\r" in raw else "\n"
    stamp = FileStamp(after.st_size, after.st_mtime_ns, after.st_ino)
    return LoadedFile(text.replace("\r\n", "\n").replace("\r", "\n"), newline, raw.startswith(b"\xef\xbb\xbf"), stamp)


def write_file(path: Path, text: str, newline: str, bom: bool, expected: FileStamp | None) -> FileStamp:
    """Atomically replace a UTF-8 file, refusing an unexpected external change."""
    path = path.resolve()
    try:
        current = file_stamp(path)
    except FileNotFoundError:
        current = None
    if current != expected:
        raise RuntimeError("File changed on disk. Reload it or use Save As.")
    data = (b"\xef\xbb\xbf" if bom else b"") + text.replace("\n", newline).encode("utf-8")
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as temp:
            temp_name = temp.name
            if current is not None:
                os.fchmod(temp.fileno(), stat.S_IMODE(path.stat().st_mode))
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
        # Check once more immediately before replacement. An atomic compare-and-swap
        # is unavailable for ordinary files on both supported platforms.
        try:
            latest = file_stamp(path)
        except FileNotFoundError:
            latest = None
        if latest != expected:
            raise RuntimeError("File changed on disk during save.")
        os.replace(temp_name, path)
        temp_name = None
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return file_stamp(path)
    finally:
        if temp_name is not None:
            os.unlink(temp_name)
