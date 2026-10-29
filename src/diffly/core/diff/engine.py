"""Qt-free line diff. Replace this module to change the diff engine."""

from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True, slots=True)
class Hunk:
    tag: str
    left_start: int
    left_end: int
    right_start: int
    right_end: int


@dataclass(frozen=True, slots=True)
class TextSpan:
    line: int
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class DiffStats:
    deleted: int = 0
    added: int = 0
    changed: int = 0
    identical: int = 0


@dataclass(frozen=True, slots=True)
class DiffResult:
    left_revision: int
    right_revision: int
    hunks: tuple[Hunk, ...]
    left_spans: tuple[TextSpan, ...]
    right_spans: tuple[TextSpan, ...]
    stats: DiffStats


def compare(left: str, right: str, left_revision: int, right_revision: int) -> DiffResult:
    left_lines = left.splitlines(keepends=True)
    right_lines = right.splitlines(keepends=True)
    # Anchor the shared beginning before matching the rest. With repeated
    # blocks, SequenceMatcher can otherwise pair the right-hand block with a
    # later, longer match and report identical leading lines as deleted.
    prefix = 0
    while (prefix < len(left_lines) and prefix < len(right_lines)
           and left_lines[prefix] == right_lines[prefix]):
        prefix += 1
    matcher = SequenceMatcher(None, left_lines[prefix:], right_lines[prefix:], autojunk=False)
    hunks = []
    left_spans = []
    right_spans = []
    deleted = added = changed = 0
    identical = prefix
    for tag, start_left, end_left, start_right, end_right in matcher.get_opcodes():
        i1, i2 = start_left + prefix, end_left + prefix
        j1, j2 = start_right + prefix, end_right + prefix
        if tag == "equal":
            identical += i2 - i1
            continue
        left_count, right_count = i2 - i1, j2 - j1
        if tag == "replace":
            changed += min(left_count, right_count)
            deleted += max(0, left_count - right_count)
            added += max(0, right_count - left_count)
        elif tag == "delete":
            deleted += left_count
        else:
            added += right_count
        hunks.append(Hunk(tag, i1, i2, j1, j2))
        if tag != "replace":
            continue
        for left_line, right_line in zip(range(i1, i2), range(j1, j2)):
            old = left_lines[left_line].rstrip("\r\n")
            new = right_lines[right_line].rstrip("\r\n")
            # Use a linear prefix/suffix match for very long lines so changed
            # text still gets highlighted without costly character matching.
            if max(len(old), len(new)) > 4096:
                prefix = 0
                while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
                    prefix += 1
                old_end, new_end = len(old), len(new)
                while old_end > prefix and new_end > prefix and old[old_end - 1] == new[new_end - 1]:
                    old_end -= 1
                    new_end -= 1
                if prefix < old_end:
                    left_spans.append(TextSpan(left_line, prefix, old_end))
                if prefix < new_end:
                    right_spans.append(TextSpan(right_line, prefix, new_end))
                continue
            for change, a1, a2, b1, b2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
                if change == "equal":
                    continue
                if a1 < a2:
                    left_spans.append(TextSpan(left_line, a1, a2))
                if b1 < b2:
                    right_spans.append(TextSpan(right_line, b1, b2))
    return DiffResult(
        left_revision, right_revision, tuple(hunks), tuple(left_spans), tuple(right_spans),
        DiffStats(deleted, added, changed, identical),
    )


def transfer_text(source: str, target: str, hunk: Hunk, source_is_left: bool) -> str:
    """Return target text after transferring one whole hunk."""
    source_lines = source.splitlines(keepends=True)
    target_lines = target.splitlines(keepends=True)
    source_start, source_end = (
        (hunk.left_start, hunk.left_end) if source_is_left else (hunk.right_start, hunk.right_end)
    )
    target_start, target_end = (
        (hunk.right_start, hunk.right_end) if source_is_left else (hunk.left_start, hunk.left_end)
    )
    target_lines[target_start:target_end] = source_lines[source_start:source_end]
    return "".join(target_lines)


def delete_hunk_text(target: str, hunk: Hunk, target_is_left: bool) -> str:
    """Remove the selected hunk's lines from one side."""
    lines = target.splitlines(keepends=True)
    start, end = (
        (hunk.left_start, hunk.left_end) if target_is_left
        else (hunk.right_start, hunk.right_end)
    )
    del lines[start:end]
    return "".join(lines)
