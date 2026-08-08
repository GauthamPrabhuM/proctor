"""Optional user configuration.

proctor runs with zero configuration. A config file only exists so that price
changes do not require editing the source: point ``PROCTOR_CONFIG`` at a JSON
file, or drop one at ``~/.config/proctor/config.json``.

See ``docs/configuration.md`` for the schema.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .pricing import PriceBook

log = logging.getLogger(__name__)

__all__ = ["Config", "default_config_path", "load_config"]

ENV_VAR = "PROCTOR_CONFIG"


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "proctor" / "config.json"


class ConfigError(Exception):
    """Raised when a config file exists but cannot be used."""


@dataclass
class Config:
    log_dirs: list[str] = field(default_factory=list)
    days: int | None = None
    top: int | None = None
    pricing: dict[str, dict[str, float]] = field(default_factory=dict)
    source: Path | None = None

    def price_book(self) -> PriceBook:
        if not self.pricing:
            return PriceBook()
        return PriceBook.from_overrides(self.pricing)


def _validate(raw: Any, path: Path) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a JSON object")

    pricing = raw.get("pricing", {})
    if not isinstance(pricing, dict):
        raise ConfigError(f"{path}: 'pricing' must be an object")
    for pattern, spec in pricing.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"{path}: pricing['{pattern}'] must be an object")
        missing = {"input", "output"} - set(spec)
        if missing:
            raise ConfigError(
                f"{path}: pricing['{pattern}'] is missing {sorted(missing)}"
            )

    log_dirs = raw.get("log_dirs", [])
    if isinstance(log_dirs, str):
        log_dirs = [log_dirs]
    if not isinstance(log_dirs, list):
        raise ConfigError(f"{path}: 'log_dirs' must be a string or list of strings")

    return Config(
        log_dirs=[str(d) for d in log_dirs],
        days=raw.get("days"),
        top=raw.get("top"),
        pricing=pricing,
        source=path,
    )


def load_config(explicit: Path | None = None) -> Config:
    """Load config from ``explicit``, ``$PROCTOR_CONFIG``, or the default path.

    A missing file is not an error unless it was named explicitly.
    """
    candidates: list[tuple] = []
    if explicit is not None:
        candidates.append((Path(explicit).expanduser(), True))
    env = os.environ.get(ENV_VAR)
    if env:
        candidates.append((Path(env).expanduser(), True))
    candidates.append((default_config_path(), False))

    for path, required in candidates:
        if not path.is_file():
            if required:
                raise ConfigError(f"config file not found: {path}")
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"{path}: {exc}") from exc
        log.debug("loaded config from %s", path)
        return _validate(raw, path)

    return Config()
