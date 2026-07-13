from __future__ import annotations

from pathlib import Path

from app.textio import normalize_text


def load_company_blacklist(path: Path) -> set[str]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Одна компания на строку.\n", encoding="utf-8")
        return set()

    companies: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = normalize_text(line)
        if not clean or clean.startswith("#"):
            continue
        companies.add(clean.casefold())
    return companies


def is_company_blacklisted(company: str, blacklist: set[str]) -> bool:
    normalized = normalize_text(company).casefold()
    if not normalized:
        return False
    return normalized in blacklist


def add_company_to_blacklist(path: Path, company: str) -> None:
    clean = normalize_text(company)
    if not clean or clean == "Компания не найдена":
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_company_blacklist(path)
    if clean.casefold() in existing:
        return
    with path.open("a", encoding="utf-8") as file:
        file.write(f"\n{clean}")
