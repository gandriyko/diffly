"""Before: summarize events while keeping their original order."""

from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    user: str
    kind: str
    value: int


def normalize_user(name: str) -> str:
    return name.strip().lower()


def summarize(events: list[Event]) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = defaultdict(dict)
    for event in events:
        user = normalize_user(event.user)
        totals[user][event.kind] = totals[user].get(event.kind, 0) + event.value
    return dict(totals)


def format_report(totals: dict[str, dict[str, int]]) -> list[str]:
    lines = []
    for user, kinds in totals.items():
        lines.append(f"{user}: {sum(kinds.values())}")
        for kind, value in kinds.items():
            lines.append(f"  {kind}: {value}")
    return lines


def sample_events() -> list[Event]:
    return [
        Event(" Олена ", "view", 1),
        Event("Maksym", "view", 1),
        Event(" Олена ", "purchase", 7),
        Event("Maksym", "view", 1),
    ]


if __name__ == "__main__":
    for line in format_report(summarize(sample_events())):
        print(line)
