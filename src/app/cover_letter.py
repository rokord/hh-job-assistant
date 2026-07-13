from __future__ import annotations

from pathlib import Path


def load_cover_letter(path: Path) -> str:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "Здравствуйте!\n\nМеня заинтересовала ваша вакансия. Буду рад обсудить детали.\n",
            encoding="utf-8",
        )
    return path.read_text(encoding="utf-8").strip()
