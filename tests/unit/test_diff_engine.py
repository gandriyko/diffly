from diffly.core.diff.engine import Hunk, compare, transfer_text


def test_bidirectional_block_transfer():
    left = "a\nleft\nz\n"
    right = "a\nright\nz\n"
    result = compare(left, right, 3, 4)
    assert (result.left_revision, result.right_revision) == (3, 4)
    assert len(result.hunks) == 1
    assert transfer_text(left, right, result.hunks[0], True) == left
    assert transfer_text(right, left, result.hunks[0], True) == right


def test_insert_and_delete_blocks():
    left = "a\nextra\nz\n"
    right = "a\nz\n"
    hunk = compare(left, right, 0, 0).hunks[0]
    assert transfer_text(left, right, hunk, True) == left
    assert transfer_text(right, left, hunk, False) == right


def test_matching_prefix_is_not_marked_deleted_before_repeated_block():
    first = "{\n  title\n  old value\n}\n"
    second = "{\n  title\n  new value\n}\n"
    left = first + second
    right = second

    result = compare(left, right, 0, 0)

    assert result.hunks == (Hunk("delete", 2, 6, 2, 2),)
    assert result.stats.identical == 4


def test_changed_text_spans_within_replaced_lines():
    result = compare('"GlossDef2": 1\n', '"GlossDef": 1\n', 0, 0)
    assert [(s.line, s.start, s.end) for s in result.left_spans] == [(0, 9, 10)]
    assert result.right_spans == ()


def test_long_line_still_marks_changed_text():
    common = "a" * 5000
    result = compare(common + "old\n", common + "new\n", 0, 0)
    assert result.left_spans[0].start == 5000
    assert result.right_spans[0].start == 5000


def test_diff_statistics_count_equal_inserted_deleted_and_changed_lines():
    result = compare("same\nold\nremoved\n", "same\nnew\n", 0, 0)
    assert result.stats.identical == 1
    assert result.stats.changed == 1
    assert result.stats.deleted == 1
    assert result.stats.added == 0
