"""Blocking file operations; callers run these in a worker thread."""

import os
import stat
import tempfile
import codecs
from dataclasses import dataclass
from pathlib import Path
from charset_normalizer import from_bytes

from diffly.core.documents.model import FileStamp


@dataclass(frozen=True, slots=True)
class LoadedFile:
    text: str
    newline: str
    bom: bool
    stamp: FileStamp | None
    encoding: str = "utf-8"


def file_stamp(path: Path) -> FileStamp:
    info = path.stat()
    return FileStamp(info.st_size, info.st_mtime_ns, info.st_ino)


def detect_encoding(raw: bytes) -> str:
    for marker, encoding in ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF32_LE, "utf-32-le"),
                             (codecs.BOM_UTF32_BE, "utf-32-be"), (codecs.BOM_UTF16_LE, "utf-16-le"),
                             (codecs.BOM_UTF16_BE, "utf-16-be")):
        if raw.startswith(marker):
            return encoding
    # ASCII text in UTF-16/32 also decodes as UTF-8 unless its NUL pattern is checked.
    if raw and len(raw) % 4 == 0:
        for encoding, positions in (("utf-32-le", (1, 2, 3)), ("utf-32-be", (0, 1, 2))):
            if all(raw[index] == 0 for index in range(len(raw)) if index % 4 in positions):
                return encoding
    if raw and len(raw) % 2 == 0:
        for encoding, position in (("utf-16-le", 1), ("utf-16-be", 0)):
            if all(raw[index] == 0 for index in range(position, len(raw), 2)):
                return encoding
    if b"\x00" in raw or any(byte < 9 or 13 < byte < 32 for byte in raw):
        match = from_bytes(raw).best()
        if match is not None:
            candidate = codecs.lookup(match.encoding).name
            if candidate.startswith(("utf-16", "utf-32")):
                return candidate
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    match = from_bytes(raw).best()
    if match is None:
        raise UnicodeError("Could not detect file encoding. Choose an encoding manually.")
    return codecs.lookup(match.encoding).name


def read_file(path: Path, encoding: str | None = None) -> LoadedFile:
    with path.open("rb") as source:
        before = os.fstat(source.fileno())
        raw = source.read()
        after = os.fstat(source.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("File changed while reading. Please retry.")
    encoding = codecs.lookup(encoding).name if encoding else detect_encoding(raw)
    marker = {"utf-8": codecs.BOM_UTF8, "utf-16-le": codecs.BOM_UTF16_LE,
              "utf-16-be": codecs.BOM_UTF16_BE, "utf-32-le": codecs.BOM_UTF32_LE,
              "utf-32-be": codecs.BOM_UTF32_BE}.get(encoding, b"")
    bom = bool(marker and raw.startswith(marker))
    text = raw[len(marker):].decode(encoding) if bom else raw.decode(encoding)
    newline = "\r\n" if "\r\n" in text else "\r" if "\r" in text else "\n"
    stamp = FileStamp(after.st_size, after.st_mtime_ns, after.st_ino)
    return LoadedFile(text.replace("\r\n", "\n").replace("\r", "\n"), newline, bom, stamp, encoding)


def read_merge_file(path: Path | None, encoding: str | None = None) -> LoadedFile:
    """Read a merge source and reject binary-looking decoded content."""
    if path is None:
        return LoadedFile("", "\n", False, None)
    loaded = read_file(path, encoding)
    if any(ord(char) < 32 and char not in "\n\t" for char in loaded.text):
        raise ValueError("Binary input is not supported for merging")
    return loaded


def write_file(path: Path, text: str, newline: str, bom: bool, expected: FileStamp | None,
               encoding: str = "utf-8") -> FileStamp:
    """Atomically replace a file, refusing an unexpected external change."""
    path = path.resolve()
    try:
        current = file_stamp(path)
    except FileNotFoundError:
        current = None
    if current != expected:
        raise RuntimeError("File changed on disk. Reload it or use Save As.")
    encoding = codecs.lookup(encoding).name
    marker = {"utf-8": codecs.BOM_UTF8, "utf-16-le": codecs.BOM_UTF16_LE,
              "utf-16-be": codecs.BOM_UTF16_BE, "utf-32-le": codecs.BOM_UTF32_LE,
              "utf-32-be": codecs.BOM_UTF32_BE}.get(encoding, b"")
    data = (marker if bom else b"") + text.replace("\n", newline).encode(encoding)
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
