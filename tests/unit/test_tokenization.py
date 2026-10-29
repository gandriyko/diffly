from pathlib import Path

import pytest

from diffly.core.documents.syntax import language_for, tokenize_document


@pytest.mark.parametrize(('filename', 'language', 'text', 'expected'), [
    ('a.json', 'json', '{"key": true, "count": 2}', {'key', 'keyword', 'number'}),
    ('a.py', 'python', 'def greet():\n    return "hi" # note', {'keyword', 'function', 'string', 'comment'}),
    ('a.js', 'javascript', 'const count = 42; // note', {'keyword', 'number', 'comment'}),
    ('a.php', 'php', '<?php function greet() { return "hi"; }', {'keyword', 'function', 'string'}),
    ('a.go', 'go', 'package main\nfunc main() { println(42) }', {'keyword', 'number'}),
    ('a.rs', 'rust', 'fn main() { let count = 42; } // note', {'keyword', 'function', 'number', 'comment'}),
    ('a.sh', 'bash', '#!/bin/bash\necho "hello" # note', {'keyword', 'string', 'comment'}),
    ('a.yaml', 'yaml', 'name: "Diffly"\n# note', {'key', 'string', 'comment'}),
    ('a.toml', 'toml', 'name = "Diffly"\ncount = 42 # note', {'string', 'number', 'comment'}),
    ('a.md', 'markdown', '# Heading\n**bold** and `code`', {'function', 'keyword', 'string'}),
])
def test_supported_languages(filename, language, text, expected):
    assert language_for(Path(filename), text) == language
    revision, actual_language, spans = tokenize_document(text, language, 7)
    assert (revision, actual_language) == (7, language)
    assert expected <= {kind for _, _, _, kind in spans}
    lines = text.split('\n')
    for line, start, end, _ in spans:
        assert 0 <= start < end <= len(lines[line])


@pytest.mark.parametrize(('filename', 'language'), [
    ('a.JSONC', 'json'), ('a.pyw', 'python'), ('a.mjs', 'javascript'),
    ('a.cjs', 'javascript'), ('a.yml', 'yaml'), ('a.markdown', 'markdown'),
    ('a.bash', 'bash'), ('.bashrc', 'bash'), ('.zshrc', 'bash'),
])
def test_filename_aliases(filename, language):
    assert language_for(Path(filename), '') == language


@pytest.mark.parametrize(('text', 'language'), [
    ('#!/usr/bin/env bash\necho hi', 'bash'),
    ('#!/usr/bin/python3\nprint(1)', 'python'),
    ('#!/usr/bin/env node\nconsole.log(1)', 'javascript'),
    ('<?php echo 1;', 'php'), ('const x = 1;', 'javascript'),
    ('package main', 'go'), ('fn main() {}', 'rust'),
])
def test_untitled_detection(text, language):
    assert language_for(None, text) == language


def test_exact_offsets_and_multiline_tokens():
    text = '\n\tconst emoji = "😀"; /* first\nsecond */\n'
    _, _, spans = tokenize_document(text, 'javascript', 1)
    assert (1, 1, 6, 'keyword') in spans
    assert (1, 15, 18, 'string') in spans
    assert (1, 20, 28, 'comment') in spans
    assert (2, 0, 9, 'comment') in spans
    assert tokenize_document('function f() { return "ok"; }', 'php', 1)[2]
    assert tokenize_document('plain', None, 1) == (1, None, ())
    assert language_for(Path('a.txt'), 'const x = 1;') is None
