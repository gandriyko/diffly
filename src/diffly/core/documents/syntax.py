"""Pure Python tokenization for background syntax jobs.

Coordinates are line number and Python character offsets. Qt conversion is done
only when the result reaches the GUI thread.
"""

import io
import keyword
import re
import token
import tokenize
from pathlib import Path

TokenSpan = tuple[int, int, int, str]
_JSON_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|\b(?:true|false|null)\b|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?')


def language_for(path: Path | None, sample: str) -> str | None:
    if path is not None:
        suffix = path.suffix.lower()
        return {".json": "json", ".jsonc": "json", ".py": "python", ".pyw": "python"}.get(suffix)
    sample = sample.lstrip()[:512]
    if sample.startswith(("{", "[")):
        return "json"
    if re.match(r'(?:#!.*python|(?:async\s+)?def\s|class\s|from\s|import\s)', sample):
        return "python"
    return None


def tokenize_document(text: str, language: str | None, revision: int) -> tuple[int, str | None, tuple[TokenSpan, ...]]:
    if language == "json":
        spans = []
        for line_number, line in enumerate(text.splitlines()):
            for match in _JSON_TOKEN.finditer(line):
                value = match.group()
                if value.startswith('"'):
                    kind = "key" if re.match(r'\s*:', line[match.end():]) else "string"
                elif value in ("true", "false", "null"):
                    kind = "keyword"
                else:
                    kind = "number"
                spans.append((line_number, match.start(), match.end(), kind))
        return revision, language, tuple(spans)
    if language == "python":
        spans = []
        lines = text.splitlines()
        try:
            tokens = tokenize.generate_tokens(io.StringIO(text).readline)
            previous = None
            for item in tokens:
                kind = None
                if item.type == token.STRING:
                    kind = "string"
                elif item.type == token.NUMBER:
                    kind = "number"
                elif item.type == tokenize.COMMENT:
                    kind = "comment"
                elif item.type == token.NAME:
                    if item.string in keyword.kwlist or item.string in ("True", "False", "None"):
                        kind = "keyword"
                    elif previous in ("def", "class"):
                        kind = "function"
                    previous = item.string
                elif item.type not in (tokenize.NL, token.NEWLINE, token.INDENT, token.DEDENT):
                    previous = None
                if kind is None:
                    continue
                first, last = item.start[0] - 1, item.end[0] - 1
                for line_number in range(first, last + 1):
                    if line_number >= len(lines):
                        break
                    start = item.start[1] if line_number == first else 0
                    end = item.end[1] if line_number == last else len(lines[line_number])
                    if end > start:
                        spans.append((line_number, start, end, kind))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            pass  # Keep tokens already collected while the user is typing.
        return revision, language, tuple(spans)
    return revision, language, ()
