import ast
from pathlib import Path

from diffly.core.merge.three_way import merge


def run(base, left, right):
    return merge(base, left, right, (1, 2, 3))


def test_separate_and_identical_changes():
    assert run("A\nB\nC\n", "A\nX\nC\n", "A\nB\nY\n").text == "A\nX\nY\n"
    result = run("A\nB\n", "A\nX\n", "A\nX\n")
    assert result.text == "A\nX\n" and not result.conflicts


def test_competing_changes_and_insertions():
    for base, left, right in (("A\nB\n", "A\nL\n", "A\nR\n"),
                              ("A\n", "A\nL\n", "A\nR\n"),
                              ("A\nB\n", "A\n", "A\nR\n")):
        result = run(base, left, right)
        assert len(result.conflicts) == 1
        assert result.text == left
        assert "<<<<<<<" not in result.text
        assert result.conflicts[0].state == "unresolved"


def test_adjacent_edits_and_newline_preserved():
    result = run("A\nB\nC", "A\nX\nC", "A\nB\nY")
    assert result.text == "A\nX\nY"
    assert not result.conflicts
    result = run("A\nB\nC\n", "A\nX\nY\n", "A\nB\nY\n")
    assert result.text == "A\nX\nY\n"
    assert not result.conflicts


def test_marker_like_source_text_is_ordinary_text():
    result = run("<<<<<<< LEFT\nB\n", "<<<<<<< LEFT\nL\n", "<<<<<<< LEFT\nR\n")
    assert len(result.conflicts) == 1
    assert result.conflicts[0].base_range == (1, 2)


def test_python_fixture_has_expected_merge_cases():
    fixture = Path(__file__).parents[1] / "fixtures" / "merge"
    sources = [(fixture / name).read_text(encoding="utf-8") for name in
               ("base.txt", "left.txt", "right.txt")]
    for source in sources:
        ast.parse(source)
        assert len(source.splitlines()) > 230
    result = run(*sources)
    ast.parse(result.text)
    assert len(result.regions) == 31
    assert len(result.conflicts) == 21
    assert max(end - start for start, end in (c.base_range for c in result.conflicts)) >= 25
    assert any(c.base_range[0] == c.base_range[1] for c in result.conflicts)
    assert any(c.left_range[0] == c.left_range[1] for c in result.conflicts)
    assert 'left_target = "left"' in result.text
    assert 'right_target = "right"' in result.text
    assert 'doomed_a = "one"' not in result.text
    assert result.text.count("<<<<<<<") == 1  # The literal Python string in case 23.
