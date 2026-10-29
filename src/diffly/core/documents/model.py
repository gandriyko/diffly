"""File metadata and revision state, independent of Qt."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FileStamp:
    size: int
    modified_ns: int
    inode: int


@dataclass(slots=True)
class DocumentState:
    path: Path | None = None
    stamp: FileStamp | None = None
    newline: str = "\n"
    bom: bool = False
    revision: int = 0
    saved_revision: int = 0

    @property
    def dirty(self) -> bool:
        return self.revision != self.saved_revision
