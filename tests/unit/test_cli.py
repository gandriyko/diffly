import pytest

from diffly.app import parse_args


def test_cli_accepts_up_to_two_file_paths():
    assert parse_args([]).left is None
    assert str(parse_args(["left.txt"]).left) == "left.txt"
    args = parse_args(["left.txt", "right.txt"])
    assert (str(args.left), str(args.right)) == ("left.txt", "right.txt")
    with pytest.raises(SystemExit):
        parse_args(["a", "b", "c"])


def test_cli_accepts_directory_pair_separately_from_files():
    args = parse_args(["--directories", "left", "right"])
    assert [str(path) for path in args.directories] == ["left", "right"]
    with pytest.raises(SystemExit):
        parse_args(["file.txt", "--directories", "left", "right"])
