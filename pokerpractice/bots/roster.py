from __future__ import annotations

import random
import hashlib
import importlib.util
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

import yaml

from .base import Bot
from .opponents import CheckCallOpponent, RandomLegalOpponent
from .opponents.strategies import STRATEGY_FACTORIES


@dataclass(frozen=True, slots=True)
class OpponentSpec:
    style: str
    params: dict
    strength: str = "normal"


BotFactory = Callable[[random.Random, dict, str], Bot]


def _check_call_factory(rng: random.Random, params: dict, strength: str) -> Bot:
    return CheckCallOpponent(rng)


def _random_factory(rng: random.Random, params: dict, strength: str) -> Bot:
    return RandomLegalOpponent(rng)


BOT_REGISTRY: dict[str, BotFactory] = {
    "check_call": _check_call_factory,
    "random": _random_factory,
    **STRATEGY_FACTORIES,
}


@lru_cache(maxsize=16)
def _hero_class(path: str, mtime_ns: int, size: int) -> type[Bot]:
    module_name = f"pokerpractice_user_hero_{hashlib.sha256(path.encode()).hexdigest()[:16]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load hero bot from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    bot_class = getattr(module, "MyBot", None)
    if not isinstance(bot_class, type) or not issubclass(bot_class, Bot):
        raise TypeError(f"{path} must define a MyBot subclass of Bot")
    return bot_class


def _load_hero(path: str | Path | None, rng: random.Random) -> Bot:
    source = Path(path) if path is not None else (
        Path(__file__).resolve().parents[2] / "bots" / "my_bot.py"
    )
    source = source.resolve()
    stat = source.stat()
    return _hero_class(str(source), stat.st_mtime_ns, stat.st_size)(rng)


def register_bot(style: str, factory: BotFactory) -> None:
    """Register a strategy without changing roster loading or tournament code."""
    if not style or style == "hero":
        raise ValueError("opponent style must be a non-empty non-hero name")
    BOT_REGISTRY[style] = factory


def load_roster(path: str) -> tuple[OpponentSpec, ...]:
    with open(path, encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    return tuple(
        OpponentSpec(
            style=str(row["style"]),
            params=dict(row.get("params", {})),
            strength=str(row.get("strength", "normal")),
        )
        for row in data.get("opponents", [])
    )


def make_bot(
    style: str,
    rng: random.Random,
    hero: bool = False,
    params: dict | None = None,
    strength: str = "normal",
    hero_path: str | Path | None = None,
) -> Bot:
    if hero:
        return _load_hero(hero_path, rng)
    try:
        return BOT_REGISTRY[style](rng, params or {}, strength)
    except KeyError as exc:
        raise ValueError(f"unregistered opponent style: {style}") from exc
