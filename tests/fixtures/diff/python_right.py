"""After: filter events and make the report order deterministic."""

from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    user: str
    kind: str
    value: int
    source: str = "web"


def normalize_user(name: str) -> str:
    return " ".join(name.split()).casefold()


def summarize(events: list[Event], *, excluded: set[str] | None = None) -> dict[str, dict[str, int]]:
    excluded = excluded or set()
    totals: dict[str, dict[str, int]] = defaultdict(dict)
    for event in events:
        if event.kind in excluded or event.value <= 0:
            continue
        user = normalize_user(event.user)
        totals[user][event.kind] = totals[user].get(event.kind, 0) + event.value
    return dict(totals)


def format_report(totals: dict[str, dict[str, int]]) -> list[str]:
    lines = ["Activity report"]
    for user, kinds in sorted(totals.items()):
        lines.append(f"{user}: {sum(kinds.values()):,}")
        for kind, value in sorted(kinds.items()):
            lines.append(f"  {kind}: {value:,}")
    return lines


def sample_events() -> list[Event]:
    return [
        Event(" Олена ", "view", 1),
        Event("Maksym", "view", 1),
        Event(" Олена ", "purchase", 7, "mobile"),
        Event("Maksym", "view", 1),
        Event("Maksym", "debug", 0),
    ]


if __name__ == "__main__":
    for line in format_report(summarize(sample_events(), excluded={"debug"})):
        print(line)
