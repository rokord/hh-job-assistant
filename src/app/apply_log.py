from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def append_apply_log(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"created_at": datetime.now().isoformat(timespec="seconds"), **event}
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")
