from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class RulesConfig:
    players: int = 8
    starting_stack: int = 2000
    blind_level_hands: int = 25
    blind_levels: tuple[tuple[int, int], ...] = (
        (10, 20), (15, 30), (25, 50), (50, 100),
        (75, 150), (100, 200), (150, 300), (200, 400),
    )
    antes: int = 0
    rake: int = 0
    payouts: tuple[float, ...] = (100, 60, 40, 25, 15, 10, 5, 0)
    hand_cap: int = 200


@dataclass(frozen=True, slots=True)
class TimingConfig:
    mode: str = "competition"
    budget_ms: float = 1.0
    hard_timeout_ms: float = 50.0
    sng_wall_timeout_s: float = 120.0


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def load_configs(config_dir: str | Path) -> tuple[RulesConfig, TimingConfig, dict, list[dict]]:
    root = Path(config_dir)
    default = _load_yaml(root / "default.yaml")
    rules_data = _load_yaml(root / default.get("rules", "rules.yaml"))
    timing_data = _load_yaml(root / default.get("timing", "timing.yaml"))
    logging_data = _load_yaml(root / default.get("logging", "logging.yaml"))
    roster_data = _load_yaml(root / default.get("roster", "roster.yaml"))
    rules_data["blind_levels"] = tuple(tuple(level) for level in rules_data["blind_levels"])
    rules_data["payouts"] = tuple(float(value) for value in rules_data["payouts"])
    rules = RulesConfig(**rules_data)
    timing = TimingConfig(**timing_data)
    return rules, timing, logging_data, list(roster_data.get("opponents", []))
