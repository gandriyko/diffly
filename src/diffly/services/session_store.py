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
    if not isinstance(entry, dict):
        return False
    kind = entry.get("kind")
    if kind not in ("files", "directories", "merge"):
        return False
    fields = ("base", "left", "right") if kind == "merge" else ("left", "right")
    return (all(isinstance(entry.get(key), str) and bool(entry[key]) for key in fields)
            and (kind != "merge" or entry.get("result") is None or
                 isinstance(entry["result"], str)))


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
    pair = (entry["kind"], entry.get("base"), entry["left"], entry["right"])
    data["recent"] = [entry] + [item for item in data["recent"]
                                if (item["kind"], item.get("base"), item["left"], item["right"]) != pair][:9]
