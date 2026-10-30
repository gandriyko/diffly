"""Client-side title bar for Linux, with the diff toolbar in the title row."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenuBar, QSizePolicy, QToolButton, QWidget


class LinuxTitleBar(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setObjectName("LinuxTitleBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(4)

        self.menu_bar = QMenuBar(self)
        layout.addWidget(self.menu_bar)

        self.title = QLabel(window.windowTitle(), self)
        self.title.setObjectName("LinuxWindowTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.installEventFilter(self)
        layout.addWidget(self.title, 1)
        window.windowTitleChanged.connect(self.title.setText)

        for label, tip, callback in (
            ("−", "Minimize", window.showMinimized),
            ("□", "Maximize or restore", self._toggle_maximized),
            ("×", "Close", window.close),
        ):
            button = QToolButton(self)
            button.setObjectName("LinuxWindowButton")
            button.setText(label)
            button.setToolTip(tip)
            button.setFixedSize(32, 30)
            button.clicked.connect(callback)
            layout.addWidget(button)

    def add_toolbar(self, toolbar):
        self.layout().insertWidget(1, toolbar)

    def _toggle_maximized(self):
        if self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()

    def eventFilter(self, watched, event):
        if watched is self.title:
            if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                self._toggle_maximized()
                return True
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                handle = self.window.windowHandle()
                if handle is not None and handle.startSystemMove():
                    return True
        return super().eventFilter(watched, event)
