from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

import yaml

from .base import Bot
from .my_bot import MyBot
from .opponents import CheckCallOpponent, RandomLegalOpponent


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
}


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
) -> Bot:
    if hero:
        return MyBot(rng)
    try:
        return BOT_REGISTRY[style](rng, params or {}, strength)
    except KeyError as exc:
        raise ValueError(f"unregistered opponent style: {style}") from exc
