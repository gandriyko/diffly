"""Persist named and recent comparison pairs without storing document contents."""

import json
import os
import tempfile
from pathlib import Path

from diffly.gui.theme import user_config_path


def store_path() -> Path:
    return user_config_path().with_name("sessions.json")


def load_sessions() -> dict:
    path = store_path()
    if not path.exists():
        return {"saved": [], "recent": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid sessions file")
    result = {}
    for key in ("saved", "recent"):
        entries = data.get(key, [])
        if not isinstance(entries, list):
            raise ValueError("Invalid sessions file")
        result[key] = [entry for entry in entries if _valid(entry) and
                       (key != "saved" or isinstance(entry.get("name"), str))]
    return result


def _valid(entry: object) -> bool:
    return (isinstance(entry, dict) and entry.get("kind") in ("files", "directories")
            and isinstance(entry.get("left"), str) and isinstance(entry.get("right"), str)
            and bool(entry["left"]) and bool(entry["right"]))


def save_sessions(data: dict) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".sessions-", delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def remember(data: dict, entry: dict) -> None:
    pair = (entry["kind"], entry["left"], entry["right"])
    data["recent"] = [entry] + [item for item in data["recent"]
                                if (item["kind"], item["left"], item["right"]) != pair][:9]
