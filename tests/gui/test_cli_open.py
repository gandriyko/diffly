from pathlib import Path

from diffly.app import open_requested, parse_args
from diffly.gui.main_window import MainWindow

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_command_line_paths_open_in_their_tabs(qtbot):
    left, right = FIXTURES / "diff" / "json_left.json", FIXTURES / "diff" / "json_right.json"
    window = MainWindow()
    qtbot.addWidget(window)
    open_requested(window, parse_args([str(left), str(right)]))
    assert window.tabs.currentWidget() is window.files
    qtbot.waitUntil(lambda: window.files.left.state.path == left and window.files.right.state.path == right,
                    timeout=5000)

    open_requested(window, parse_args(["--directories", str(FIXTURES / "directories" / "left"),
                                       str(FIXTURES / "directories" / "right")]))
    assert window.tabs.currentWidget() is window.directory_comparison
    window.close()

    merging = MainWindow(merge_enabled=True)
    qtbot.addWidget(merging)
    open_requested(merging, parse_args(["--merge", *(str(FIXTURES / "merge" / name)
                                                     for name in ("base.txt", "left.txt", "right.txt"))]))
    assert merging.tabs.currentWidget() is merging.merge_view
    qtbot.waitUntil(lambda: not merging.merge_view.busy and merging.merge_view.paths is not None, timeout=5000)
    merging.close()
