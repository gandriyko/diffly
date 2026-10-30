import pytest

from diffly.services.directory_service import copy_file_checked, regular_file_stamp


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
