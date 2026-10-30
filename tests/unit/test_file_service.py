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


@pytest.mark.parametrize("encoding,text", [
    ("cp1251", "Привіт Україно\nДругий рядок\n"),
    ("utf-16-le", "Hello\nWorld\n"),
    ("utf-16-be", "Привіт світе\n"),
])
def test_detected_encoding_survives_save(tmp_path, encoding, text):
    path = tmp_path / "sample.txt"
    path.write_bytes(text.replace("\n", "\r\n").encode(encoding))
    loaded = read_file(path)
    assert loaded.encoding == encoding
    assert loaded.text == text
    write_file(path, loaded.text + "Done\n", loaded.newline, loaded.bom, loaded.stamp, loaded.encoding)
    assert path.read_bytes() == (text + "Done\n").replace("\n", "\r\n").encode(encoding)


def test_explicit_encoding_and_utf16_bom(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_bytes(b"\xff\xfe" + "Hello\n".encode("utf-16-le"))
    loaded = read_file(path)
    assert (loaded.text, loaded.encoding, loaded.bom) == ("Hello\n", "utf-16-le", True)
    write_file(path, loaded.text, loaded.newline, loaded.bom, loaded.stamp, loaded.encoding)
    assert path.read_bytes() == b"\xff\xfe" + "Hello\n".encode("utf-16-le")

    path.write_bytes("Résumé".encode("cp1252"))
    assert read_file(path, "cp1252").text == "Résumé"
