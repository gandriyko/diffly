from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from diffly.core.documents.syntax import language_for, tokenize_document
from diffly.gui.main_window import MainWindow


def test_json_and_python_highlighting_arrive_from_background(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window = MainWindow()
    qtbot.addWidget(window)
    window.left.state.path = Path("sample.json")
    window.right.state.path = Path("sample.py")
    window.left.load_text('{"name": "Diffly", "count": 2}\n')
    window.right.load_text('def greet():\n    return "hello" # note\n')
    qtbot.waitUntil(lambda: bool(window.left.highlighter._spans) and bool(window.right.highlighter._spans), timeout=5000)
    assert (1, 7, "key") in window.left.highlighter._spans[0]
    assert any(kind == "number" for _, _, kind in window.left.highlighter._spans[0])
    assert (0, 3, "keyword") in window.right.highlighter._spans[0]
    assert any(kind == "comment" for _, _, kind in window.right.highlighter._spans[1])
    assert window.left.editor.document().firstBlock().layout().formats()
    assert window.right.editor.document().firstBlock().layout().formats()
    window.close()


def test_stale_syntax_result_is_ignored(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.left.state.path = Path("a.json")
    window.left.load_text('{"a": 1}')
    stale_revision = window.left.state.revision
    window.left.editor.insertPlainText('new')
    window._accept_syntax(("left", stale_revision, "json", ((0, 0, 5, "key"),)))
    assert not window.left.highlighter._spans
    window.left.state.saved_revision = window.left.state.revision
    window.close()


def test_language_detection_and_tokenization():
    assert language_for(Path("a.py"), "") == "python"
    assert language_for(Path("a.json"), "") == "json"
    assert language_for(None, "  {\"a\": 1}") == "json"
    assert language_for(Path("a.txt"), "def x():") is None
    _, _, spans = tokenize_document('def x():\n  return "ok"\n', "python", 1)
    assert (0, 0, 3, "keyword") in spans
    assert (0, 4, 5, "function") in spans
