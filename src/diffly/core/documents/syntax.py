"""Pygments tokenization for background syntax jobs, using Python offsets."""

import re
from functools import lru_cache
from pathlib import Path

from pygments.lexers import get_lexer_by_name
from pygments.token import Comment, Generic, Keyword, Name, Number, String

TokenSpan = tuple[int, int, int, str]
_LANGUAGES = {
    ".json": "json", ".jsonc": "json", ".py": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".php": "php", ".go": "go", ".rs": "rust",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".md": "markdown", ".markdown": "markdown",
}
_SHELL_FILES = {".bashrc", ".bash_profile", ".bash_login", ".zshrc", ".zprofile"}


def language_for(path: Path | None, sample: str) -> str | None:
    if path is not None:
        if path.name.lower() in _SHELL_FILES:
            return "bash"
        language = _LANGUAGES.get(path.suffix.lower())
        if language or path.suffix:
            return language
    sample = sample.lstrip()[:512]
    for pattern, language in (
        (r"#![^\n]*\bpython[\d.]*\b", "python"),
        (r"#![^\n]*\b(?:bash|sh|zsh)\b", "bash"),
        (r"#![^\n]*\bnode\b", "javascript"),
        (r"<\?php\b", "php"),
        (r"(?:async\s+)?def\s|class\s|from\s|import\s", "python"),
        (r"(?:export\s+)?(?:const|let|var|function)\s", "javascript"),
        (r"package\s+\w+", "go"),
        (r"(?:pub\s+)?(?:fn|impl|use|mod)\s", "rust"),
    ):
        if re.match(pattern, sample):
            return language
    if sample.startswith(("{", "[")):
        return "json"
    return None


@lru_cache(maxsize=16)
def _lexer(language: str, php_inline: bool = False):
    return get_lexer_by_name(language, startinline=php_inline)


def _token_kind(token_type) -> str | None:
    if token_type in Comment:
        return "comment"
    if token_type in String:
        return "string"
    if token_type in Number:
        return "number"
    if token_type in Keyword or token_type in Name.Builtin or token_type in Generic.Strong:
        return "keyword"
    if token_type in Name.Tag or token_type in Name.Attribute:
        return "key"
    if (token_type in Name.Function or token_type in Name.Class
            or token_type in Generic.Heading or token_type in Generic.Subheading):
        return "function"
    if token_type in Generic.Emph:
        return "string"
    return None


def tokenize_document(text: str, language: str | None, revision: int) -> tuple[int, str | None, tuple[TokenSpan, ...]]:
    if language not in _LANGUAGES.values():
        return revision, language, ()
    lexer = _lexer(language, language == "php" and "<?" not in text)
    spans = []
    line, column = 0, 0
    # Bypass whitespace normalization so tabs, leading blank lines and Unicode
    # retain their exact document positions. Qt converts to UTF-16 on display.
    # Several lexers require a final newline to recognize an EOF line comment.
    # The synthetic newline produces no span and never changes real offsets.
    source = text if text.endswith("\n") else text + "\n"
    for _, token_type, value in lexer.get_tokens_unprocessed(source):
        kind = _token_kind(token_type)
        parts = value.split("\n")
        for index, part in enumerate(parts):
            if index:
                line, column = line + 1, 0
            end = column + len(part)
            if kind is not None and end > column:
                spans.append((line, column, end, kind))
            column = end
    return revision, language, tuple(spans)
