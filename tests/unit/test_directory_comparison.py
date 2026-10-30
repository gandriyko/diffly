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

    result = compare_directories(left, right, ("node_modules", "*.log", "build/*.tmp"), presets=())
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


def test_gitignore_from_parent_and_nested_folder_with_negation(tmp_path):
    (tmp_path / ".gitignore").write_text("*.pyc\ncache/\n")
    left, right = tmp_path / "left", tmp_path / "right"
    for root in (left, right):
        (root / "src").mkdir(parents=True)
        (root / "cache").mkdir()
        (root / "cache" / "data.txt").write_text("different")
        (root / "src" / "module.pyc").write_text("different")
        (root / "src" / "keep.pyc").write_text("different")
        (root / "src" / ".gitignore").write_text("!keep.pyc\n")
        (root / "visible.txt").write_text("different")
    paths = {entry.relative_path for entry in compare_directories(left, right, presets=())}
    assert "cache" not in paths
    assert "src/module.pyc" not in paths
    assert "src/keep.pyc" in paths
    assert "visible.txt" in paths
    all_paths = {entry.relative_path for entry in compare_directories(left, right, use_gitignore=False, presets=())}
    assert "cache/data.txt" in all_paths
    assert "src/module.pyc" in all_paths


def test_gitignore_anchored_and_double_star_patterns(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    for root in (left, right):
        (root / "a" / "b").mkdir(parents=True)
        (root / "a" / "cache.tmp").write_text("x")
        (root / "a" / "b" / "cache.tmp").write_text("x")
        (root / "root.tmp").write_text("x")
        (root / ".gitignore").write_text("/root.tmp\na/**/cache.tmp\n")
    paths = {entry.relative_path for entry in compare_directories(left, right, presets=())}
    assert "root.tmp" not in paths
    assert "a/cache.tmp" not in paths
    assert "a/b/cache.tmp" not in paths


def test_built_in_presets_are_enabled_immediately(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    for root in (left, right):
        (root / "__pycache__").mkdir(parents=True)
        (root / "__pycache__" / "module.pyc").write_text("cache")
        (root / "draft.txt~").write_text("backup")
        (root / "visible.txt").write_text("visible")
    paths = {entry.relative_path for entry in compare_directories(left, right)}
    assert paths == {"visible.txt"}
    paths_without_presets = {entry.relative_path for entry in compare_directories(left, right, presets=())}
    assert {"__pycache__", "draft.txt~"} <= paths_without_presets
