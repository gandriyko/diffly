from concurrent.futures import CancelledError
from threading import Event

import pytest

from diffly.core.directories.compare import compare_directories


def test_recursive_comparison_uses_file_bytes_and_marks_parent_directories(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    for root in (left, right):
        (root / "nested").mkdir(parents=True)
        (root / "nested" / "same.txt").write_text("same")
        (root / "nested" / "changed.txt").write_text("left" if root == left else "rght")
        (root / "empty").mkdir()
    (left / "only-left.txt").write_text("left")
    (right / "only-right.txt").write_text("right")
    (left / "clash").write_text("file")
    (right / "clash").mkdir()

    result = {entry.relative_path: entry for entry in compare_directories(left, right)}
    assert result["nested/changed.txt"].status == "different"  # Same size, different bytes.
    assert result["nested/same.txt"].status == "same"
    assert result["nested"].status == "different"
    assert result["empty"].status == "same"
    assert result["only-left.txt"].status == "left_only"
    assert result["only-right.txt"].status == "right_only"
    assert result["clash"].status == "type_changed"
    assert (result["clash"].left_kind, result["clash"].right_kind) == ("file", "directory")


def test_ignore_masks_prune_folders_and_match_names_at_any_depth(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    for root in (left, right):
        (root / "nested" / "node_modules").mkdir(parents=True)
        (root / "build").mkdir()
        (root / "nested" / "node_modules" / "bundle.js").write_text("different")
        (root / "nested" / "trace.log").write_text("different")
        (root / "nested" / "keep.txt").write_text("same")
        (root / "build" / "cache.tmp").write_text("different")

    result = compare_directories(left, right, ("node_modules", "*.log", "build/*.tmp"))
    assert {entry.relative_path for entry in result} == {"build", "nested", "nested/keep.txt"}
    assert all(entry.status == "same" for entry in result)


def test_directory_scan_does_not_follow_symlinks_and_can_cancel(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "loop").symlink_to(left, target_is_directory=True)
    (right / "loop").symlink_to(right, target_is_directory=True)
    result = compare_directories(left, right)
    assert len(result) == 1
    assert result[0].kind == "symlink"
    assert result[0].status == "different"

    cancel = Event()
    cancel.set()
    with pytest.raises(CancelledError):
        compare_directories(left, right, cancel=cancel)
