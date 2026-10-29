import pytest

from diffly.services.file_service import read_file, write_file


def test_save_preserves_bom_line_endings_and_rejects_external_change(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_bytes(b"\xef\xbb\xbfa\r\nb\r\n")
    loaded = read_file(path)
    assert (loaded.text, loaded.newline, loaded.bom) == ("a\nb\n", "\r\n", True)
    saved_stamp = write_file(path, "a\nc\n", loaded.newline, loaded.bom, loaded.stamp)
    assert path.read_bytes() == b"\xef\xbb\xbfa\r\nc\r\n"
    path.write_text("external", encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed on disk"):
        write_file(path, "ours", loaded.newline, loaded.bom, saved_stamp)
    assert path.read_text() == "external"
