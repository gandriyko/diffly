"""Line-based, Qt-free three-way merge."""

from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True, slots=True)
class Edit:
    start: int
    end: int
    side_start: int
    side_end: int


@dataclass(frozen=True, slots=True)
class Conflict:
    id: int
    base_range: tuple[int, int]
    left_range: tuple[int, int]
    right_range: tuple[int, int]
    result_range: tuple[int, int]
    state: str = "unresolved"


@dataclass(frozen=True, slots=True)
class MergeRegion:
    base_range: tuple[int, int]
    left_range: tuple[int, int]
    right_range: tuple[int, int]
    result_range: tuple[int, int]
    conflict_id: int | None = None


@dataclass(frozen=True, slots=True)
class MergeResult:
    text: str
    conflicts: tuple[Conflict, ...]
    revisions: tuple[int, int, int]
    left_changes: tuple[tuple[int, int], ...] = ()
    right_changes: tuple[tuple[int, int], ...] = ()
    regions: tuple[MergeRegion, ...] = ()


def _edits(base: list[str], side: list[str]) -> list[Edit]:
    prefix = 0
    while prefix < min(len(base), len(side)) and base[prefix] == side[prefix]:
        prefix += 1
    matcher = SequenceMatcher(None, base[prefix:], side[prefix:], autojunk=False)
    edits = []
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace" and b - a == d - c:
            edits.extend(Edit(a + prefix + i, a + prefix + i + 1,
                              c + prefix + i, c + prefix + i + 1)
                         for i in range(b - a) if base[a + prefix + i] != side[c + prefix + i])
        else:
            edits.append(Edit(a + prefix, b + prefix, c + prefix, d + prefix))
    return edits


def _overlap(a: Edit, b: Edit) -> bool:
    if a.start == a.end and b.start == b.end:
        return a.start == b.start
    if a.start == a.end:
        return b.start < a.start < b.end
    if b.start == b.end:
        return a.start < b.start < a.end
    return a.start < b.end and b.start < a.end


def _render(base: list[str], side: list[str], edits: list[Edit], selected: list[Edit],
            start: int, end: int) -> tuple[list[str], tuple[int, int]]:
    before = sum(edit.side_end - edit.side_start - (edit.end - edit.start)
                 for edit in edits if edit.end <= start and
                 not (edit.start == edit.end == start))
    side_start = start + before
    output: list[str] = []
    cursor = start
    for edit in selected:
        output.extend(base[cursor:edit.start])
        output.extend(side[edit.side_start:edit.side_end])
        cursor = edit.end
    output.extend(base[cursor:end])
    return output, (side_start, side_start + len(output))


def merge(base: str, left: str, right: str,
          revisions: tuple[int, int, int]) -> MergeResult:
    base_lines = base.splitlines(keepends=True)
    left_lines = left.splitlines(keepends=True)
    right_lines = right.splitlines(keepends=True)
    left_edits = _edits(base_lines, left_lines)
    right_edits = _edits(base_lines, right_lines)
    tagged = sorted([(e, "left") for e in left_edits] +
                    [(e, "right") for e in right_edits],
                    key=lambda item: (item[0].start, item[0].end))
    regions: list[list[tuple[Edit, str]]] = []
    for item in tagged:
        if regions and any(_overlap(item[0], old[0]) for old in regions[-1]):
            regions[-1].append(item)
        else:
            regions.append([item])
    output: list[str] = []
    conflicts: list[Conflict] = []
    merge_regions: list[MergeRegion] = []
    cursor = 0
    for region in regions:
        start = min(edit.start for edit, _ in region)
        end = max(edit.end for edit, _ in region)
        output.extend(base_lines[cursor:start])
        left_piece, left_range = _render(base_lines, left_lines, left_edits,
                                         [e for e, side in region if side == "left"], start, end)
        right_piece, right_range = _render(base_lines, right_lines, right_edits,
                                           [e for e, side in region if side == "right"], start, end)
        base_piece = base_lines[start:end]
        result_start = len(output)
        conflict_id = None
        if left_piece == right_piece:
            output.extend(left_piece)
        elif left_piece == base_piece:
            output.extend(right_piece)
        elif right_piece == base_piece:
            output.extend(left_piece)
        else:
            # Result starts with Left's text. The conflict record, rather than
            # marker lines, keeps the competing versions available to the UI.
            output.extend(left_piece)
            conflict_id = len(conflicts) + 1
            conflicts.append(Conflict(conflict_id, (start, end), left_range,
                                      right_range, (result_start, len(output))))
        merge_regions.append(MergeRegion((start, end), left_range, right_range,
                                         (result_start, len(output)), conflict_id))
        cursor = end
    output.extend(base_lines[cursor:])
    return MergeResult("".join(output), tuple(conflicts), revisions,
                       tuple((edit.side_start, edit.side_end) for edit in left_edits),
                       tuple((edit.side_start, edit.side_end) for edit in right_edits),
                       tuple(merge_regions))
