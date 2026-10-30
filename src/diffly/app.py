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
    args = parser.parse_args(argv)
    if args.directories is not None and (args.left is not None or args.right is not None):
        parser.error("file paths and --directories cannot be used together")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Diffly")
    window = MainWindow()
    window.show()
    if args.directories is not None:
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
        return app.exec()
    finally:
        interrupt_timer.stop()
        signal.signal(signal.SIGINT, previous_handler)
