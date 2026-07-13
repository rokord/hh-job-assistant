from __future__ import annotations


def say(message: str = "") -> None:
    print(message, flush=True)


def ask(message: str) -> str:
    return input(message).strip().lower()


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split()).strip()
