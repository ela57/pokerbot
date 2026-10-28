from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any

from pokerpractice.engine.base import Action, ActionKind, LegalActions


@dataclass(frozen=True, slots=True)
class PublicSeat:
    seat: int
    anon_id: str
    stack: int
    bet_this_street: int
    total_contributed: int
    folded: bool
    all_in: bool
    active: bool
    position: str


@dataclass(frozen=True, slots=True)
class PublicAction:
    seat_anon: str
    street: str
    kind: str
    amount: int | None


@dataclass(frozen=True, slots=True)
class Observation:
    hole_cards: tuple[str, ...]
    board: tuple[str, ...]
    street: str
    my_seat: int
    button_seat: int
    actor_seat: int
    seats: tuple[PublicSeat, ...]
    pot_total: int
    pot_breakdown: tuple[tuple[int, tuple[int, ...]], ...]
    small_blind: int
    big_blind: int
    level: int
    players_remaining: int
    to_call: int
    legal: LegalActions
    action_history: tuple[PublicAction, ...]
    hand_number: int
    hands_remaining: int
    payouts: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class TableStartInfo:
    my_seat: int
    seat_count: int
    starting_stack: int
    payouts: tuple[float, ...]
    anon_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PublicHandResult:
    hand_number: int
    chip_changes: tuple[tuple[int, int], ...]
    showdown_cards: tuple[tuple[int, tuple[str, ...]], ...]
    board: tuple[str, ...]


class Bot(ABC):
    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.last_diagnostics: dict[str, Any] | None = None

    def on_table_start(self, info: TableStartInfo) -> None:
        pass

    @abstractmethod
    def act(self, obs: Observation) -> Action:
        raise NotImplementedError

    def on_hand_end(self, result: PublicHandResult) -> None:
        pass


class CheckCallBot(Bot):
    def act(self, obs: Observation) -> Action:
        if obs.legal.can_check:
            return Action(ActionKind.CHECK)
        if obs.legal.can_call:
            return Action(ActionKind.CALL)
        return Action(ActionKind.FOLD)


class RandomLegalBot(Bot):
    def act(self, obs: Observation) -> Action:
        actions: list[Action] = []
        if obs.legal.can_check:
            actions.append(Action(ActionKind.CHECK))
        if obs.legal.can_call:
            actions.append(Action(ActionKind.CALL))
        if obs.legal.can_fold:
            actions.append(Action(ActionKind.FOLD))
        if obs.legal.min_raise_to is not None and obs.legal.max_raise_to is not None:
            actions.append(
                Action(
                    ActionKind.RAISE,
                    self.rng.randint(obs.legal.min_raise_to, obs.legal.max_raise_to),
                )
            )
        return self.rng.choice(actions) if actions else Action(ActionKind.FOLD)


def observation_dict(obs: Observation) -> dict[str, Any]:
    """Convert frozen observation primitives into its exact agent-visible JSON form."""
    return asdict(obs)
