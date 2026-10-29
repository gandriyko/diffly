"""Qt-threaded file I/O and process-based diff computation."""

from concurrent.futures import ProcessPoolExecutor
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from diffly.core.diff.engine import compare
from diffly.core.documents.syntax import tokenize_document


class TaskSignals(QObject):
    completed = Signal(object, object)
    failed = Signal(object, str)


class _FileTask(QRunnable):
    def __init__(self, key: object, operation, args: tuple, signals: TaskSignals):
        super().__init__()
        self.key = key
        self.operation = operation
        self.args = args
        self.signals = signals

    def run(self):
        try:
            result = self.operation(*self.args)
        except Exception as exc:
            self.signals.failed.emit(self.key, str(exc))
        else:
            self.signals.completed.emit(self.key, result)


class TaskScheduler(QObject):
    diff_ready = Signal(object)
    diff_failed = Signal(str)
    syntax_ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.io_pool = QThreadPool(self)
        self.io_pool.setMaxThreadCount(2)
        self.process_pool = ProcessPoolExecutor(max_workers=1)
        self.syntax_pool = ProcessPoolExecutor(max_workers=1)
        self._diff_future = None
        self._syntax_futures = {}
        self._pending_file_signals: dict[object, TaskSignals] = {}

    def submit_file(self, key: object, operation, args: tuple, on_done, on_error):
        signals = TaskSignals()
        self._pending_file_signals[key] = signals

        def completed(received_key, result):
            self._pending_file_signals.pop(received_key, None)
            on_done(result)

        def failed(received_key, message):
            self._pending_file_signals.pop(received_key, None)
            on_error(message)

        signals.completed.connect(completed)
        signals.failed.connect(failed)
        self.io_pool.start(_FileTask(key, operation, args, signals))

    def submit_diff(self, left: str, right: str, left_revision: int, right_revision: int):
        if self._diff_future is not None:
            self._diff_future.cancel()
        future = self.process_pool.submit(compare, left, right, left_revision, right_revision)
        self._diff_future = future

        def finished(done):
            try:
                result = done.result()
            except Exception as exc:
                if not done.cancelled():
                    self.diff_failed.emit(str(exc))
            else:
                self.diff_ready.emit(result)

        future.add_done_callback(finished)

    def submit_syntax(self, key: str, text: str, language: str, revision: int):
        previous = self._syntax_futures.get(key)
        if previous is not None:
            previous.cancel()
        future = self.syntax_pool.submit(tokenize_document, text, language, revision)
        self._syntax_futures[key] = future

        def finished(done):
            if not done.cancelled():
                try:
                    self.syntax_ready.emit((key, *done.result()))
                except Exception:
                    pass  # Syntax coloring must never interrupt editing.

        future.add_done_callback(finished)

    def shutdown(self):
        if self._diff_future is not None:
            self._diff_future.cancel()
        self.process_pool.shutdown(wait=False, cancel_futures=True)
        for future in self._syntax_futures.values():
            future.cancel()
        self.syntax_pool.shutdown(wait=False, cancel_futures=True)
        self.io_pool.waitForDone(1000)
