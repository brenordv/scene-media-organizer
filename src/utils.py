import os
from typing import Optional, Union


def to_int(value: Optional[Union[str, int]], default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def get_env(name: str) -> Optional[str]:
    value = os.environ.get(name)
    if value is not None:
        value = value.strip()
    return value or None


def to_bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}