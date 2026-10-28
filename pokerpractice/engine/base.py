from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ActionKind(StrEnum):
    FOLD = "FOLD"
    CHECK = "CHECK"
    CALL = "CALL"
    RAISE = "RAISE"


@dataclass(frozen=True, slots=True)
class Action:
    kind: ActionKind
    amount: int | None = None
    diag: dict | None = None


@dataclass(frozen=True, slots=True)
class LegalActions:
    can_fold: bool
    can_check: bool
    can_call: bool
    call_amount: int
    min_raise_to: int | None
    max_raise_to: int | None


@dataclass(frozen=True, slots=True)
class NeutralActionEvent:
    seat: int
    street: str
    kind: str
    amount: int | None


@dataclass(frozen=True, slots=True)
class PotResult:
    amount: int
    eligible_seats: tuple[int, ...]
    winners: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class HandResult:
    starting_stacks: tuple[int, ...]
    ending_stacks: tuple[int, ...]
    hole_cards: tuple[tuple[str, ...], ...]
    board: tuple[str, ...]
    actions: tuple[NeutralActionEvent, ...]
    pots: tuple[PotResult, ...]
    refunds: tuple[tuple[int, int], ...]
    showdown_cards: tuple[tuple[int, tuple[str, ...]], ...]
    showdown_ranks: tuple[tuple[int, str], ...]
    chip_deltas: tuple[int, ...]


class GameAdapter(Protocol):
    def start_hand(
        self,
        stacks: tuple[int, ...],
        live_seats: tuple[int, ...],
        button_seat: int,
        small_blind: int,
        big_blind: int,
        cards_by_seat: dict[int, tuple[str, str]],
        board_cards: tuple[str, ...],
        burn_cards: tuple[str, ...],
    ) -> None: ...

    def actor_seat(self) -> int | None: ...

    def legal_actions(self) -> LegalActions: ...

    def apply(self, action: Action) -> NeutralActionEvent: ...

    def advance(self) -> None: ...

    def public_view(self) -> dict: ...

    def hand_result(self) -> HandResult: ...
