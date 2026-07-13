from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ConfigError(RuntimeError):
    pass


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_config() -> dict[str, Any]:
    root = get_project_root()
    config_path = root / "config" / "settings.json"
    example_path = root / "config" / "settings.example.json"

    if not config_path.exists():
        if example_path.exists():
            raise ConfigError(
                "Config file not found: config/settings.json. "
                "Copy config/settings.example.json to config/settings.json first."
            )
        raise ConfigError(f"Config file not found: {config_path}")

    try:
        with config_path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError as error:
        raise ConfigError(f"Invalid JSON in config/settings.json: {error}") from error


def resolve_project_path(path_from_config: str) -> Path:
    return get_project_root() / path_from_config


def get_nested(config: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = config
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current
