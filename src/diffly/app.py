import argparse
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from diffly.gui.main_window import MainWindow


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare and merge text files in two panes.")
    parser.add_argument("left", nargs="?", type=Path, help="file to open in the left pane")
    parser.add_argument("right", nargs="?", type=Path, help="file to open in the right pane")
    parser.add_argument(
        "--directories", nargs=2, type=Path, metavar=("LEFT", "RIGHT"),
        help="open and compare two directories",
    )
    parser.add_argument("--merge", nargs=3, metavar=("BASE", "LEFT", "RIGHT"),
                        help="open a three-way file merge")
    parser.add_argument("--output", type=Path, metavar="RESULT", help="result save path for --merge")
    args = parser.parse_args(argv)
    if sum((args.directories is not None, args.merge is not None,
            args.left is not None or args.right is not None)) > 1:
        parser.error("file paths, --directories, and --merge cannot be used together")
    if args.output is not None and args.merge is None:
        parser.error("--output requires --merge")
    args.git_mergetool = args.merge is not None and args.output is not None
    if args.merge is not None:
        if not args.merge[1] or not args.merge[2]:
            parser.error("LEFT and RIGHT must be file paths")
        if not args.merge[0] and not args.git_mergetool:
            parser.error("an empty BASE requires --output")
        args.merge = tuple(Path(value) if value else None for value in args.merge)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if sys.platform == "darwin":
        from Foundation import NSProcessInfo

        NSProcessInfo.processInfo().setProcessName_("Diffly")
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Diffly")
    app.setApplicationDisplayName("Diffly")
    window = MainWindow(merge_enabled=args.merge is not None, git_mergetool=args.git_mergetool)
    window.show()
    if args.merge is not None:
        paths = tuple(path.expanduser().absolute() if path is not None else None for path in args.merge)
        output = args.output.expanduser().absolute() if args.output is not None else None
        window.tabs.setCurrentWidget(window.merge_view)
        window.merge_view.open_paths(*paths, output)
    elif args.directories is not None:
        left, right = (path.expanduser().absolute() for path in args.directories)
        view = window.directory_comparison
        view.left_path.setText(str(left))
        view.right_path.setText(str(right))
        window.tabs.setCurrentWidget(window.directory_comparison)
        view.compare()
    else:
        if args.left is not None or args.right is not None:
            window.tabs.setCurrentWidget(window.file_comparison)
        if args.left is not None:
            window.open_path(window.left, args.left.expanduser().absolute())
        if args.right is not None:
            window.open_path(window.right, args.right.expanduser().absolute())

    # Qt's C++ event loop otherwise leaves Python without an opportunity to
    # dispatch SIGINT. Close the window so unsaved-change checks still run.
    interrupt_timer = QTimer(app)
    interrupt_timer.setInterval(100)
    interrupt_timer.timeout.connect(lambda: None)
    interrupt_timer.start()
    previous_handler = signal.getsignal(signal.SIGINT)

    def interrupt(_signum, _frame):
        QTimer.singleShot(0, window.close)

    signal.signal(signal.SIGINT, interrupt)
    try:
        exit_code = app.exec()
        if args.git_mergetool:
            return (0 if exit_code == 0 and not window.git_merge_aborted and
                    window.merge_view.git_merge_complete() else 1)
        return exit_code
    finally:
        interrupt_timer.stop()
        signal.signal(signal.SIGINT, previous_handler)
