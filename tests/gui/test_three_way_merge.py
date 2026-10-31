from pathlib import Path
import codecs
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QTextCharFormat, QTextCursor, QWheelEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from diffly.core.merge.three_way import merge

from diffly.gui.main_window import MainWindow


def test_merge_tab_is_only_added_for_merge_launch(qtbot):
    ordinary = MainWindow()
    qtbot.addWidget(ordinary)
    assert [ordinary.tabs.tabText(index) for index in range(ordinary.tabs.count())] == [
        "Directories", "Files", "Settings"
    ]
    merging = MainWindow(merge_enabled=True)
    qtbot.addWidget(merging)
    assert [merging.tabs.tabText(index) for index in range(merging.tabs.count())] == [
        "Directories", "Files", "Merge", "Settings"
    ]


def test_merge_highlights_python_in_txt_sources_and_result(qtbot, tmp_path):
    paths = tuple(tmp_path / name for name in ("base.txt", "left.txt", "right.txt"))
    for path, value in zip(paths, ("base", "left", "right")):
        path.write_text(f'#!/usr/bin/env python3\ndef example():\n    return "{value}"\n')
    window = MainWindow(merge_enabled=True)
    qtbot.addWidget(window)
    window.tabs.setCurrentWidget(window.merge_view)
    view = window.merge_view
    view.open_paths(*paths)
    qtbot.waitUntil(lambda: not view.busy and all(
        pane.syntax_language == "python" and
        (0, 3, "keyword") in pane.highlighter._spans.get(1, ())
        for pane in (*view.sources, view.result)
    ), timeout=5000)
    assert all(pane.editor.document().findBlockByNumber(1).layout().formats()
               for pane in (*view.sources, view.result))


def test_merge_display_options_toggle_all_panes_and_persist(qtbot, tmp_path, monkeypatch):
    from diffly.gui.theme import load_config

    monkeypatch.setenv("DIFFLY_CONFIG", str(tmp_path / "config.toml"))
    paths = tuple(tmp_path / name for name in ("base.py", "left.py", "right.py"))
    for path, value in zip(paths, ("base", "left", "right")):
        path.write_text(f'def example():\n    return "{value}"\n')
    window = MainWindow(merge_enabled=True)
    qtbot.addWidget(window)
    view = window.merge_view
    view.open_paths(*paths)
    for editor in (window.left, window.right):
        editor.state.path = tmp_path / "sample.py"
        editor.load_text('def other():\n    return "files"\n')
    panes = (*view.sources, view.result, window.left, window.right)
    qtbot.waitUntil(lambda: not view.busy and all(pane.highlighter._spans for pane in panes),
                    timeout=5000)
    settings = window.settings_page
    assert not settings.show_line_numbers.isChecked()
    assert settings.syntax_highlighting.isChecked()
    assert all(pane.editor.side_width() == 0 for pane in panes)
    settings.show_line_numbers.setChecked(True)
    assert all(pane.editor.side_width() > 0 for pane in panes)
    settings.syntax_highlighting.setChecked(False)
    assert all(not pane.highlighter._spans for pane in panes)
    qtbot.waitUntil(lambda: all(not pane.editor.document().firstBlock().layout().formats()
                                for pane in panes), timeout=5000)
    view.accept_syntax("merge-1", view.sources[1].state.revision, "python",
                       ((0, 0, 3, "keyword"),))
    assert not view.sources[1].highlighter._spans
    assert load_config().settings["show_line_numbers"] is True
    assert load_config().settings["syntax_highlighting"] is False
    settings.syntax_highlighting.setChecked(True)
    qtbot.waitUntil(lambda: all(pane.highlighter._spans for pane in panes), timeout=5000)
    assert all(pane.editor.document().firstBlock().layout().formats() for pane in panes)
    assert load_config().settings["syntax_highlighting"] is True
    window.close()
    reopened = MainWindow(merge_enabled=True)
    qtbot.addWidget(reopened)
    assert reopened.settings_page.show_line_numbers.isChecked()
    assert reopened.settings_page.syntax_highlighting.isChecked()
    assert reopened.merge_view.line_numbers_visible
    assert reopened.merge_view.syntax_highlighting_enabled
    assert reopened.left.editor.side_width() > 0
    assert reopened.right.editor.side_width() > 0


def test_merge_view_resolves_and_undoes(qtbot, tmp_path):
    paths = tuple(tmp_path / name for name in ("base.txt", "left.txt", "right.txt"))
    for path, text in zip(paths, ("A\nB\n", "A\nL\n", "A\nR\n")):
        path.write_text(text)
    window = MainWindow(merge_enabled=True)
    qtbot.addWidget(window)
    window.tabs.setCurrentWidget(window.merge_view)
    window.merge_view.open_paths(*paths)
    qtbot.waitUntil(lambda: not window.merge_view.busy and
                    len(window.merge_view._conflicts) == 1, timeout=5000)
    view = window.merge_view
    assert view.result.text() == "A\nL\n"
    assert "1 unresolved" in view.footer.text()
    view.use("right")
    assert view.result.text() == "A\nR\n"
    assert "0 unresolved" in view.footer.text()
    view.result.editor.undo()
    assert view.result.text() == "A\nL\n"
    assert "1 unresolved" in view.footer.text()
    view.result.editor.redo()
    assert view.result.text() == "A\nR\n"
    assert "0 unresolved" in view.footer.text()


def test_merge_output_rejects_source_path(qtbot, tmp_path, monkeypatch):
    paths = tuple(tmp_path / name for name in ("base", "left", "right"))
    for path in paths:
        path.write_text("same\n")
    window = MainWindow(merge_enabled=True)
    qtbot.addWidget(window)
    view = window.merge_view
    warned = []
    monkeypatch.setattr("diffly.gui.merge_view.QMessageBox.warning",
                        lambda *args: warned.append(args))
    view.open_paths(*paths, output=paths[0])
    assert warned and view.paths is None


def test_manual_resolution_after_edit(qtbot, tmp_path):
    paths = tuple(tmp_path / name for name in ("base", "left", "right"))
    for path, text in zip(paths, ("B\n", "L\n", "R\n")):
        path.write_text(text)
    window = MainWindow(merge_enabled=True)
    qtbot.addWidget(window)
    view = window.merge_view
    view.open_paths(*paths)
    qtbot.waitUntil(lambda: not view.busy and len(view._conflicts) == 1, timeout=5000)
    conflict = view._conflicts[0]
    cursor = QTextCursor(view.result.editor.document())
    cursor.setPosition(conflict.start)
    cursor.setPosition(conflict.end, QTextCursor.MoveMode.KeepAnchor)
    cursor.insertText("combined\n")
    assert "1 unresolved" in view.footer.text()
    assert view.buttons["Mark Resolved"].isEnabled()
    view.mark_resolved()
    assert "0 unresolved" in view.footer.text()
    view.result.editor.undo()
    assert "1 unresolved" in view.footer.text()


def test_merge_save_preserves_sources_and_rejects_external_change(qtbot, tmp_path, monkeypatch):
    paths = tuple(tmp_path / name for name in ("base.txt", "left.txt", "right.txt"))
    for path, text in zip(paths, ("A\nB\n", "A\nL\n", "A\nB\n")):
        path.write_text(text)
    output = tmp_path / "result.txt"
    output.write_text("old\n")
    window = MainWindow(merge_enabled=True)
    qtbot.addWidget(window)
    view = window.merge_view
    view.open_paths(*paths, output)
    qtbot.waitUntil(lambda: not view.busy and view.result.text() == "A\nL\n", timeout=5000)
    view.result.editor.insertPlainText("edited ")
    output.write_text("external change\n")
    messages = []
    monkeypatch.setattr("diffly.gui.merge_view.QMessageBox.critical",
                        lambda *args: messages.append(args))
    view.save()
    qtbot.waitUntil(lambda: not view.busy, timeout=5000)
    assert messages
    assert output.read_text() == "external change\n"
    assert view.result.state.dirty
    assert [path.read_text() for path in paths] == ["A\nB\n", "A\nL\n", "A\nB\n"]


def test_merge_save_uses_left_bom_newline_and_final_line(qtbot, tmp_path):
    base, left, right = (tmp_path / name for name in ("base.txt", "left.txt", "right.txt"))
    base.write_bytes(b"A\nB")
    left.write_bytes(codecs.BOM_UTF16_LE + "A\r\nL".encode("utf-16-le"))
    right.write_bytes(b"A\nB")
    output = tmp_path / "merged.txt"
    window = MainWindow(merge_enabled=True)
    qtbot.addWidget(window)
    view = window.merge_view
    view.open_paths(base, left, right, output)
    qtbot.waitUntil(lambda: not view.busy and view.result.text() == "A\nL", timeout=5000)
    view.save()
    qtbot.waitUntil(lambda: not view.busy and output.exists(), timeout=5000)
    assert output.read_bytes() == codecs.BOM_UTF16_LE + "A\r\nL".encode("utf-16-le")


def test_saving_unresolved_result_confirms_without_writing_markers(qtbot, tmp_path, monkeypatch):
    paths = tuple(tmp_path / name for name in ("base", "left", "right"))
    for path, text in zip(paths, ("B\n", "L\n", "R\n")):
        path.write_text(text)
    output = tmp_path / "result"
    window = MainWindow(merge_enabled=True)
    qtbot.addWidget(window)
    view = window.merge_view
    view.open_paths(*paths, output)
    qtbot.waitUntil(lambda: not view.busy and len(view._conflicts) == 1, timeout=5000)
    questions = []
    def answer(_parent, title, _message):
        questions.append(title)
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr("diffly.gui.merge_view.QMessageBox.question", answer)
    view.save()
    qtbot.waitUntil(lambda: not view.busy and output.exists(), timeout=5000)
    assert questions == ["Unresolved conflicts"]
    assert output.read_text() == "L\n"


def test_stale_source_revision_cannot_replace_result(qtbot):
    window = MainWindow(merge_enabled=True)
    qtbot.addWidget(window)
    view = window.merge_view
    for editor, text in zip(view.sources, ("B\n", "L\n", "R\n")):
        editor.load_text(text)
    revisions = tuple(editor.state.revision for editor in view.sources)
    stale = merge("B\n", "L\n", "R\n", revisions)
    view.sources[1].load_text("new left\n")
    view.accept_merge(stale)
    assert view.result.text() == ""


def test_edit_spanning_conflicts_keeps_both_unresolved(qtbot, tmp_path):
    paths = tuple(tmp_path / name for name in ("base", "left", "right"))
    for path, text in zip(paths, ("A\nB\nC\nD\n", "A\nL\nC\nX\n", "A\nR\nC\nY\n")):
        path.write_text(text)
    window = MainWindow(merge_enabled=True)
    qtbot.addWidget(window)
    view = window.merge_view
    view.open_paths(*paths)
    qtbot.waitUntil(lambda: not view.busy and len(view._conflicts) == 2, timeout=5000)
    view.use("left")
    assert "1 unresolved" in view.footer.text()
    first, second = view._conflicts
    cursor = QTextCursor(view.result.editor.document())
    cursor.setPosition(first.start)
    cursor.setPosition(second.end, QTextCursor.MoveMode.KeepAnchor)
    cursor.insertText("combined\n")
    assert "2 unresolved" in view.footer.text()


def test_meld_layout_has_center_result_and_gutter_actions(qtbot, tmp_path):
    paths = tuple(tmp_path / name for name in ("base", "left", "right"))
    for path, text in zip(paths, ("B\n", "L\n", "R\n")):
        path.write_text(text)
    window = MainWindow(merge_enabled=True)
    window.resize(1100, 720)
    qtbot.addWidget(window)
    window.tabs.setCurrentWidget(window.merge_view)
    window.show()
    assert window.merge_toolbar.isVisible()
    assert not window.toolbar.isVisible()
    assert [action.text() for action in window.merge_toolbar.actions() if not action.isSeparator()] == [
        "Previous conflict", "Next conflict", "Use Left", "Use Right"
    ]
    assert window.merge_view.encoding_button.isVisible()
    view = window.merge_view
    view.open_paths(*paths)
    qtbot.waitUntil(lambda: not view.busy and len(view._conflicts) == 1, timeout=5000)
    for pane in (view.sources[1], view.result, view.sources[2]):
        selections = pane.editor.extraSelections()
        assert any(selection.format.property(QTextCharFormat.Property.FullWidthSelection)
                   for selection in selections)
    for pane in (view.sources[1], view.sources[2]):
        assert any(selection.format.background().color().name() == pane.theme["diff_removed"]
                   for selection in pane.editor.extraSelections())
    assert view.sources[1].mapToGlobal(view.sources[1].rect().center()).x() < view.result.mapToGlobal(view.result.rect().center()).x()
    assert view.result.mapToGlobal(view.result.rect().center()).x() < view.sources[2].mapToGlobal(view.sources[2].rect().center()).x()
    assert all(pane.editor.side_width() == 0 and not pane.editor.side_area.isVisible()
               for pane in (*view.sources, view.result))
    assert all(pane.editor.document().documentMargin() == 0
               for pane in (*view.sources, view.result))
    assert view.left_gutter.parentWidget().handleWidth() == 0
    assert view.overview.mapToGlobal(QPoint(0, 0)).x() >= view.sources[2].mapToGlobal(
        QPoint(view.sources[2].width(), 0)).x()
    assert not view.sources[0].isVisible()
    assert view.left_gutter.button.isVisible()
    qtbot.mouseClick(view.left_gutter.button, Qt.MouseButton.LeftButton)
    assert view.result.text() == "L\n"
    assert view.sources[1].text() == "L\n"
    assert "0 unresolved" in view.footer.text()
    assert not view.left_gutter.button.isVisible()
    assert not view.right_gutter.button.isVisible()
    view.result.editor.undo()
    assert "1 unresolved" in view.footer.text()
    window.tabs.setCurrentWidget(window.file_comparison)
    assert window.toolbar.isVisible()
    assert not window.merge_toolbar.isVisible()


def test_gutter_has_action_for_each_change_block(qtbot, tmp_path):
    paths = tuple(tmp_path / name for name in ("base.txt", "left.txt", "right.txt"))
    for path, content in zip(paths, (
        "title: Diffly demo\nmode: standard\nshared: base\ntail: unchanged\nstatus: ready\n",
        "title: Left title\nmode: standard\nshared: left\ntail: unchanged\nstatus: ready\n",
        "title: Diffly demo\nmode: standard\nshared: right\ntail: unchanged\nstatus: complete\n",
    )):
        path.write_text(content)
    window = MainWindow(merge_enabled=True)
    qtbot.addWidget(window)
    view = window.merge_view
    window.tabs.setCurrentWidget(view)
    window.show()
    view.open_paths(*paths)
    qtbot.waitUntil(lambda: not view.busy and len(view._regions) == 3, timeout=5000)
    assert len(view.left_gutter.buttons) == len(view.right_gutter.buttons) == 3
    status_source = view.sources[1].editor.document().findBlockByNumber(4)
    status_cursor = QTextCursor(status_source)
    source_y = view.sources[1].editor.viewport().mapToGlobal(
        view.sources[1].editor.cursorRect(status_cursor).topLeft()
    ).y()
    arrow_y = view.left_gutter.buttons[2].mapToGlobal(view.left_gutter.buttons[2].rect().topLeft()).y()
    assert abs(source_y - arrow_y) < 5
    assert "title: Left title" in view.result.text()
    assert not view.left_gutter.buttons[0].isVisible()
    assert view.right_gutter.buttons[0].isVisible()
    qtbot.mouseClick(view.right_gutter.buttons[0], Qt.MouseButton.LeftButton)
    assert "title: Diffly demo" in view.result.text()
    assert "1 unresolved" in view.footer.text()
    assert not view.right_gutter.buttons[0].isVisible()
    assert not view.left_gutter.buttons[0].isVisible()
    view.result.editor.undo()
    assert "title: Left title" in view.result.text()
    assert view.right_gutter.buttons[0].isVisible()
    view.result.editor.redo()
    assert "title: Diffly demo" in view.result.text()
    assert not view.right_gutter.buttons[0].isVisible()
    assert view.left_gutter.buttons[2].isVisible()
    assert not view.right_gutter.buttons[2].isVisible()
    qtbot.mouseClick(view.left_gutter.buttons[2], Qt.MouseButton.LeftButton)
    assert "status: ready" in view.result.text()
    assert not view.left_gutter.buttons[2].isVisible()
    assert not view.right_gutter.buttons[2].isVisible()
    assert "1 unresolved" in view.footer.text()
    view.result.editor.undo()
    assert "status: complete" in view.result.text()
    assert view.left_gutter.buttons[2].isVisible()


def test_large_python_fixture_covers_scroll_conflicts_and_alignment(qtbot):
    fixture = Path(__file__).parents[1] / "fixtures" / "merge"
    window = MainWindow(merge_enabled=True)
    qtbot.addWidget(window)
    view = window.merge_view
    view.open_paths(*(fixture / name for name in ("base.txt", "left.txt", "right.txt")))
    qtbot.waitUntil(lambda: not view.busy and len(view._regions) == 31, timeout=5000)
    assert len(view._conflicts) == 21
    assert len(view.result.text().splitlines()) > 230
    assert any(lines >= 5 for _, lines in view.sources[1].editor.placeholder_ranges)
    assert any(lines >= 10 for _, lines in view.sources[2].editor.placeholder_ranges)
    assert any(lines >= 2 for _, lines in view.result.editor.placeholder_ranges)
    assert len(view.left_gutter.buttons) == len(view.right_gutter.buttons) == 31


def test_undo_restores_transfer_arrows_for_each_fixture_region(qtbot):
    fixture = Path(__file__).parents[1] / "fixtures" / "merge"
    window = MainWindow(merge_enabled=True)
    qtbot.addWidget(window)
    view = window.merge_view
    view.open_paths(*(fixture / name for name in ("base.txt", "left.txt", "right.txt")))
    qtbot.waitUntil(lambda: not view.busy and len(view._regions) == 31, timeout=5000)
    for index, region in enumerate(view._regions):
        before = tuple(view.region_action_available(region, side) for side in ("left", "right"))
        side = "right" if before[1] else "left"
        if not any(before):
            continue
        view.use_region(index, side)
        view.result.editor.undo()
        after = tuple(view.region_action_available(region, side) for side in ("left", "right"))
        assert after == before, f"arrow state after undo differs in region {index + 1}"


def test_undo_restores_arrows_after_several_transfers(qtbot):
    fixture = Path(__file__).parents[1] / "fixtures" / "merge"
    window = MainWindow(merge_enabled=True)
    qtbot.addWidget(window)
    view = window.merge_view
    view.open_paths(*(fixture / name for name in ("base.txt", "left.txt", "right.txt")))
    qtbot.waitUntil(lambda: not view.busy and len(view._regions) == 31, timeout=5000)
    choices = ((0, "right"), (3, "left"), (6, "right"))
    initial = view.result.text()
    for index, side in choices:
        assert view.region_action_available(view._regions[index], side)
        view.use_region(index, side)
        assert not view.region_action_available(view._regions[index], side)
    for index, side in reversed(choices):
        view.result.editor.undo()
        assert view.region_action_available(view._regions[index], side)
    assert view.result.text() == initial


def test_undo_restores_arrow_when_conflict_uses_default_left_text(qtbot, tmp_path):
    paths = tuple(tmp_path / name for name in ("base", "left", "right"))
    for path, text in zip(paths, ("before\n", "left\n", "right\n")):
        path.write_text(text)
    window = MainWindow(merge_enabled=True)
    qtbot.addWidget(window)
    view = window.merge_view
    view.open_paths(*paths)
    qtbot.waitUntil(lambda: not view.busy and len(view._conflicts) == 1, timeout=5000)
    region = view._regions[0]
    assert view.region_action_available(region, "left")
    view.use_region(0, "left")
    assert not view.region_action_available(region, "left")
    view.result.editor.undo()
    assert view.region_action_available(region, "left")


def test_ctrl_z_after_clicking_gutter_restores_arrow(qtbot, tmp_path):
    paths = tuple(tmp_path / name for name in ("base", "left", "right"))
    for path, text in zip(paths, ("A\nB\n", "A\nL\n", "A\nR\n")):
        path.write_text(text)
    window = MainWindow(merge_enabled=True)
    window.resize(1000, 700)
    qtbot.addWidget(window)
    window.tabs.setCurrentWidget(window.merge_view)
    window.show()
    view = window.merge_view
    view.open_paths(*paths)
    qtbot.waitUntil(lambda: not view.busy and len(view._regions) == 1, timeout=5000)
    button = view.right_gutter.button
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert not button.isVisible()
    qtbot.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert button.isVisible()


def test_undo_after_grouped_typing_restores_region_state(qtbot, tmp_path):
    paths = tuple(tmp_path / name for name in ("base", "left", "right"))
    for path, text in zip(paths, ("A\nB\n", "A\nL\n", "A\nR\n")):
        path.write_text(text)
    window = MainWindow(merge_enabled=True)
    qtbot.addWidget(window)
    view = window.merge_view
    view.open_paths(*paths)
    qtbot.waitUntil(lambda: not view.busy and len(view._regions) == 1, timeout=5000)
    view.use_region(0, "right")
    region = view._regions[0]
    assert not view.region_action_available(region, "left")
    cursor = view.result.editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    view.result.editor.setTextCursor(cursor)
    qtbot.keyClicks(view.result.editor, "abc")
    view.result.editor.undo()
    assert view.result.text() == "A\nR\n"
    assert not view.region_action_available(region, "left")
    view.result.editor.undo()
    assert view.result.text() == "A\nL\n"
    assert view.region_action_available(region, "right")
    view.result.editor.redo()
    assert view.result.text() == "A\nR\n"
    assert not view.region_action_available(region, "left")
    view.result.editor.redo()
    assert view.result.text() == "A\nR\nabc"


def test_scrolling_any_merge_editor_moves_all_three(qtbot):
    fixture = Path(__file__).parents[1] / "fixtures" / "merge"
    window = MainWindow(merge_enabled=True)
    window.resize(1100, 720)
    qtbot.addWidget(window)
    window.tabs.setCurrentWidget(window.merge_view)
    window.show()
    view = window.merge_view
    view.open_paths(*(fixture / name for name in ("base.txt", "left.txt", "right.txt")))
    qtbot.waitUntil(lambda: not view.busy and len(view._regions) == 31, timeout=5000)
    panes = (view.sources[1], view.result, view.sources[2])
    for source, line in zip(panes, (60, 120, 180)):
        source.editor.verticalScrollBar().setValue(line)
        row = source.editor.visual_row(source.editor.verticalScrollBar().value())
        for target in panes:
            if target is not source:
                assert target.editor.verticalScrollBar().value() == target.editor.block_for_visual_row(row)
    marker = "# CASE 19: nested_overlap_with_shared_edges"
    tops = []
    for pane in panes:
        line = pane.text().splitlines().index(marker)
        tops.append(pane.editor.line_top(line))
    assert max(tops) - min(tops) < 2
    before = tuple(pane.editor.verticalScrollBar().value() for pane in panes)
    target = panes[2].editor.viewport()
    wheel = QWheelEvent(QPointF(10, 10), QPointF(target.mapToGlobal(QPoint(10, 10))),
                        QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
    QApplication.sendEvent(target, wheel)
    assert all(pane.editor.verticalScrollBar().value() > value
               for pane, value in zip(panes, before))
    qtbot.mouseClick(view.overview, Qt.MouseButton.LeftButton,
                     pos=QPoint(8, view.overview.height() - 5))
    assert all(pane.editor.verticalScrollBar().value() > value
               for pane, value in zip(panes, before))
    bottom = view.result.editor.verticalScrollBar().value()
    qtbot.mousePress(view.overview, Qt.MouseButton.LeftButton,
                     pos=QPoint(8, view.overview.height() - 5))
    qtbot.mouseMove(view.overview, pos=QPoint(8, 5))
    qtbot.mouseRelease(view.overview, Qt.MouseButton.LeftButton, pos=QPoint(8, 5))
    assert view.result.editor.verticalScrollBar().value() < bottom
