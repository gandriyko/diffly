import pytest
from pathlib import Path

from diffly.services.directory_service import (
    copy_directory_checked, copy_file_checked, directory_stamp, path_stamp, regular_file_stamp,
)


def test_copy_creates_nested_file_and_rejects_changed_destination(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    source = left / "binary.dat"
    source.write_bytes(b"\x00\xff")
    stamp = regular_file_stamp(source)
    copied = copy_file_checked(source, right, "nested/binary.dat", stamp, None)
    assert copied.read_bytes() == b"\x00\xff"
    with pytest.raises(RuntimeError, match="Destination file changed"):
        copy_file_checked(source, right, "nested/binary.dat", stamp, None)


def test_copy_rejects_path_traversal_and_symlink_parent(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"data")
    destination = tmp_path / "destination"
    destination.mkdir()
    stamp = regular_file_stamp(source)
    with pytest.raises(RuntimeError, match="Invalid relative file path"):
        copy_file_checked(source, destination, "../outside", stamp, None)
    (destination / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symbolic link"):
        copy_file_checked(source, destination, "link/outside", stamp, None)
    assert not (tmp_path / "outside").exists()


def test_copy_folder_replaces_entire_tree_including_ignored_files(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    source, target = left / "sub", right / "sub"
    (source / "nested").mkdir(parents=True)
    (target / "nested").mkdir(parents=True)
    (source / "nested" / "new.txt").write_text("new")
    (source / "cache.pyc").write_bytes(b"cache")
    (source / "link").symlink_to("nested/new.txt")
    (target / "nested" / "old.txt").write_text("old")
    (target / "only-here.txt").write_text("remove")
    copied, leftover = copy_directory_checked(source, right, "sub", directory_stamp(source), path_stamp(target))
    assert copied == target and leftover is None
    assert (target / "nested" / "new.txt").read_text() == "new"
    assert (target / "cache.pyc").read_bytes() == b"cache"
    assert (target / "link").is_symlink()
    assert not (target / "nested" / "old.txt").exists()
    assert not (target / "only-here.txt").exists()
    assert (source / "nested" / "new.txt").read_text() == "new"


def test_copy_folder_rejects_stale_destination_and_overlapping_roots(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    source, target = left / "sub", right / "sub"
    source.mkdir(parents=True)
    target.mkdir(parents=True)
    expected_target = path_stamp(target)
    (target / "new.txt").write_text("changed")
    with pytest.raises(RuntimeError, match="Destination changed"):
        copy_directory_checked(source, right, "sub", directory_stamp(source), expected_target)
    with pytest.raises(RuntimeError, match="overlap"):
        copy_directory_checked(source, source, "nested", directory_stamp(source), None)


def test_copy_folder_restores_destination_if_swap_fails(tmp_path, monkeypatch):
    import diffly.services.directory_service as directory_service

    left, right = tmp_path / "left", tmp_path / "right"
    source, target = left / "sub", right / "sub"
    source.mkdir(parents=True)
    target.mkdir(parents=True)
    (source / "new.txt").write_text("new")
    (target / "old.txt").write_text("old")
    original_replace = directory_service.os.replace

    def fail_staged_swap(source_path, target_path):
        if Path(source_path).name.startswith(".sub.stage-") and Path(target_path) == target:
            raise OSError("simulated swap failure")
        return original_replace(source_path, target_path)

    monkeypatch.setattr(directory_service.os, "replace", fail_staged_swap)
    with pytest.raises(OSError, match="simulated swap failure"):
        copy_directory_checked(source, right, "sub", directory_stamp(source), path_stamp(target))
    assert (target / "old.txt").read_text() == "old"
    assert not (target / "new.txt").exists()
    assert not list(right.glob(".sub.stage-*"))
    assert not list(right.glob(".sub.backup-*"))


def test_copy_folder_does_not_replace_destination_created_during_copy(tmp_path, monkeypatch):
    import diffly.services.directory_service as directory_service

    left, right = tmp_path / "left", tmp_path / "right"
    source = left / "sub"
    source.mkdir(parents=True)
    right.mkdir()
    (source / "new.txt").write_text("new")
    original_copytree = directory_service.shutil.copytree

    def create_target_during_copy(*args, **kwargs):
        result = original_copytree(*args, **kwargs)
        (right / "sub").mkdir()
        (right / "sub" / "other.txt").write_text("keep")
        return result

    monkeypatch.setattr(directory_service.shutil, "copytree", create_target_during_copy)
    with pytest.raises(RuntimeError, match="Destination changed during copy"):
        copy_directory_checked(source, right, "sub", directory_stamp(source), None)
    assert (right / "sub" / "other.txt").read_text() == "keep"
