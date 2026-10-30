import os

# Modal dialogs would block the run until a human clicks them; see MainWindow.
os.environ.setdefault("DIFFLY_NO_DIALOGS", "1")
