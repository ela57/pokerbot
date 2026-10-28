from __future__ import annotations

import warnings
from dataclasses import dataclass

from pokerkit import Automation, NoLimitTexasHoldem

from .base import (
    Action,
    ActionKind,
    HandResult,
    LegalActions,
    NeutralActionEvent,
    PotResult,
)


_AUTOMATIONS = tuple(
    automation
    for automation in Automation
    if automation
    not in (
        Automation.HOLE_DEALING,
        Automation.BOARD_DEALING,
        Automation.BET_COLLECTION,
        Automation.HAND_KILLING,
        Automation.CHIPS_PUSHING,
    )
)


def _text(card: object) -> str:
    return repr(card)


@dataclass
class PokerKitHoldemAdapter:
    """Hold'em adapter; PokerKit owns all betting and pot resolution."""

    def __post_init__(self) -> None:
        self.state = None
        self.seat_by_index: tuple[int, ...] = ()
        self.index_by_seat: dict[int, int] = {}
        self.starting_stacks: tuple[int, ...] = ()
        self.cards_by_seat: dict[int, tuple[str, str]] = {}
        self.board_schedule: tuple[str, ...] = ()
        self.burn_schedule: tuple[str, ...] = ()
        self.board_cursor = 0
        self.burn_cursor = 0
        self.action_events: list[NeutralActionEvent] = []
        self.last_raise: tuple[int, str] | None = None
        self.refunds_by_seat: dict[int, int] = {}
        self.pot_results: tuple[PotResult, ...] | None = None
        self.showdown_ranks: tuple[tuple[int, str], ...] = ()

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
    ) -> None:
        if len(live_seats) < 2:
            raise ValueError("Hold'em hand requires at least two live players")
        if button_seat not in live_seats:
            raise ValueError("button must be assigned to a live seat")
        button_position = live_seats.index(button_seat)
        clockwise = live_seats[button_position + 1 :] + live_seats[: button_position + 1]
        self.seat_by_index = clockwise
        self.index_by_seat = {seat: index for index, seat in enumerate(clockwise)}
        self.starting_stacks = tuple(stacks)
        self.cards_by_seat = dict(cards_by_seat)
        self.board_schedule = tuple(board_cards)
        self.burn_schedule = tuple(burn_cards)
        self.board_cursor = 0
        self.burn_cursor = 0
        self.action_events = []
        self.last_raise = None
        self.refunds_by_seat = {}
        self.pot_results = None
        self.showdown_ranks = ()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            self.state = NoLimitTexasHoldem.create_state(
                _AUTOMATIONS,
                True,
                0,
                (small_blind, big_blind),
                big_blind,
                [stacks[seat] for seat in self.seat_by_index],
                len(self.seat_by_index),
            )
            for index, seat in enumerate(self.seat_by_index):
                for card in self.cards_by_seat[seat]:
                    self.state.deal_hole(card, index)
            self.advance()

    def actor_seat(self) -> int | None:
        if self.state is None or self.state.street is None or self.state.actor_index is None:
            return None
        return self.seat_by_index[self.state.actor_index]

    def legal_actions(self) -> LegalActions:
        state = self._state()
        actor = state.actor_index
        if actor is None:
            raise RuntimeError("there is no acting player")
        call_amount = min(
            int(state.checking_or_calling_amount),
            int(state.stacks[actor]),
        )
        can_check_or_call = state.can_check_or_call()
        can_raise = state.can_complete_bet_or_raise_to()
        min_raise = (
            int(state.min_completion_betting_or_raising_to_amount)
            if can_raise
            else None
        )
        max_raise = (
            int(state.max_completion_betting_or_raising_to_amount)
            if can_raise
            else None
        )
        if (
            can_raise
            and min_raise is not None
            and max_raise is not None
            and min_raise > max_raise
            and max_raise == int(state.bets[actor] + state.stacks[actor])
            and state.can_complete_bet_or_raise_to(max_raise)
        ):
            min_raise = max_raise
        return LegalActions(
            can_fold=state.can_fold(),
            can_check=can_check_or_call and call_amount == 0,
            can_call=can_check_or_call and call_amount > 0,
            call_amount=call_amount,
            min_raise_to=min_raise,
            max_raise_to=max_raise,
        )

    def apply(self, action: Action) -> NeutralActionEvent:
        state = self._state()
        actor_index = state.actor_index
        if actor_index is None:
            raise RuntimeError("there is no acting player")
        seat = self.seat_by_index[actor_index]
        legal = self.legal_actions()
        street = self._street_name(state)
        if action.kind == ActionKind.FOLD and legal.can_fold:
            state.fold()
            event = NeutralActionEvent(seat, street, "fold", None)
        elif action.kind == ActionKind.CHECK and legal.can_check:
            state.check_or_call()
            event = NeutralActionEvent(seat, street, "check", 0)
        elif action.kind == ActionKind.CALL and legal.can_call:
            amount = legal.call_amount
            state.check_or_call()
            event = NeutralActionEvent(seat, street, "call", amount)
        elif (
            action.kind == ActionKind.RAISE
            and action.amount is not None
            and legal.min_raise_to is not None
            and legal.max_raise_to is not None
            and legal.min_raise_to <= action.amount <= legal.max_raise_to
            and state.can_complete_bet_or_raise_to(action.amount)
        ):
            state.complete_bet_or_raise_to(action.amount)
            event = NeutralActionEvent(seat, street, "raise", action.amount)
            self.last_raise = (seat, street)
        else:
            raise ValueError(f"illegal action: {action!r}")
        self.action_events.append(event)
        self.advance()
        return event

    def advance(self) -> None:
        state = self._state()
        while True:
            if state.can_collect_bets():
                street = (
                    self.action_events[-1].street
                    if self.action_events
                    else self._street_name(state)
                )
                if (
                    sum(state.statuses) == 1
                    and self.last_raise is not None
                    and self.last_raise[1] == street
                ):
                    winner_index = self.index_by_seat[self.last_raise[0]]
                    if state.statuses[winner_index]:
                        matched = max(
                            (
                                int(amount)
                                for index, amount in enumerate(state.bets)
                                if index != winner_index
                            ),
                            default=0,
                        )
                        unmatched = max(0, int(state.bets[winner_index]) - matched)
                        if unmatched:
                            self.refunds_by_seat[self.last_raise[0]] = (
                                self.refunds_by_seat.get(self.last_raise[0], 0)
                                + unmatched
                            )
                stacks_before = tuple(int(stack) for stack in state.stacks)
                state.collect_bets()
                for index, (before, after) in enumerate(
                    zip(stacks_before, state.stacks)
                ):
                    refund = int(after) - before
                    if refund > 0:
                        seat = self.seat_by_index[index]
                        self.refunds_by_seat[seat] = (
                            self.refunds_by_seat.get(seat, 0) + refund
                        )
            elif state.can_burn_card():
                if self.burn_cursor >= len(self.burn_schedule):
                    raise RuntimeError("burn schedule exhausted")
                state.burn_card(self.burn_schedule[self.burn_cursor])
                self.burn_cursor += 1
            elif state.can_deal_board():
                if self.board_cursor == 0:
                    count = 3
                else:
                    count = 1
                end = self.board_cursor + count
                if end > len(self.board_schedule):
                    raise RuntimeError("board schedule exhausted")
                state.deal_board("".join(self.board_schedule[self.board_cursor:end]))
                self.board_cursor = end
            elif state.can_kill_hand():
                self._capture_pots_and_ranks(state)
                state.kill_hand()
            elif state.can_push_chips():
                self._capture_pots_and_ranks(state)
                state.push_chips()
            else:
                return

    def public_view(self) -> dict:
        state = self._state()
        stacks = [0] * len(self.starting_stacks)
        bets = [0] * len(self.starting_stacks)
        folded = [True] * len(self.starting_stacks)
        for index, seat in enumerate(self.seat_by_index):
            stacks[seat] = int(state.stacks[index])
            bets[seat] = int(state.bets[index])
            folded[seat] = not bool(state.statuses[index])
        board = tuple(_text(card) for row in state.board_cards for card in row)
        contributions = tuple(
            self.starting_stacks[seat] - stacks[seat]
            for seat in range(len(stacks))
        )
        total_pot = int(state.total_pot_amount)
        return {
            "stacks": tuple(stacks),
            "bets": tuple(bets),
            "contributions": contributions,
            "folded": tuple(folded),
            "all_in": tuple(
                stacks[seat] == 0 and not folded[seat]
                for seat in range(len(stacks))
            ),
            "active": tuple(not folded[seat] for seat in range(len(stacks))),
            "board": board,
            "street": self._street_name(state),
            "actor_seat": self.actor_seat(),
            "to_call": (
                min(int(state.checking_or_calling_amount), int(state.stacks[state.actor_index]))
                if state.actor_index is not None
                else 0
            ),
            "pot_total": total_pot,
            "pots": tuple(
                {
                    "amount": int(pot.raked_amount + pot.unraked_amount),
                    "eligible_seats": tuple(
                        self.seat_by_index[index] for index in pot.player_indices
                    ),
                }
                for pot in state.pots
                if pot.raked_amount + pot.unraked_amount
            ),
        }

    def hand_result(self) -> HandResult:
        state = self._state()
        if state.street is not None:
            raise RuntimeError("hand has not completed")
        ending = [0] * len(self.starting_stacks)
        for index, seat in enumerate(self.seat_by_index):
            ending[seat] = int(state.stacks[index])
        board = tuple(_text(card) for row in state.board_cards for card in row)
        holes = tuple(tuple(self.cards_by_seat.get(seat, ())) for seat in range(len(ending)))
        if self.pot_results is None:
            self._capture_pots_and_ranks(state)
        shown = []
        for operation in state.operations:
            if type(operation).__name__ == "HoleCardsShowingOrMucking":
                cards = tuple(_text(card) for card in operation.hole_cards)
                if cards:
                    shown.append((self.seat_by_index[operation.player_index], cards))
        return HandResult(
            starting_stacks=self.starting_stacks,
            ending_stacks=tuple(ending),
            hole_cards=holes,
            board=board,
            actions=tuple(self.action_events),
            pots=self.pot_results,
            refunds=self._extract_refunds(),
            showdown_cards=tuple(shown),
            showdown_ranks=self.showdown_ranks,
            chip_deltas=tuple(end - start for start, end in zip(self.starting_stacks, ending)),
        )

    def _capture_pots_and_ranks(self, state) -> None:
        if self.pot_results is not None:
            return
        pots = tuple(state.pots)
        showdown_indices = {
            operation.player_index
            for operation in state.operations
            if type(operation).__name__ == "HoleCardsShowingOrMucking"
        }
        ranked_hands = {}
        if showdown_indices:
            hand_type = state.hand_types[0]
            board = "".join(
                _text(card) for row in state.board_cards for card in row
            )
            for index in sorted(showdown_indices):
                seat = self.seat_by_index[index]
                hole_cards = "".join(self.cards_by_seat[seat])
                ranked_hands[index] = hand_type.from_game(hole_cards, board)
        self.showdown_ranks = tuple(
            (self.seat_by_index[index], str(hand))
            for index, hand in sorted(ranked_hands.items())
        )
        results = []
        for pot in pots:
            amount = int(pot.raked_amount + pot.unraked_amount)
            if amount <= 0:
                continue
            eligible_indices = tuple(pot.player_indices)
            eligible = tuple(self.seat_by_index[index] for index in eligible_indices)
            hands = [
                (index, ranked_hands[index])
                for index in eligible_indices
                if index in ranked_hands
            ]
            if hands:
                best_hand = max(hand for _, hand in hands)
                winner_indices = tuple(
                    index for index, hand in hands if hand == best_hand
                )
            else:
                winner_indices = eligible_indices
            winners = tuple(self.seat_by_index[index] for index in winner_indices)
            results.append(PotResult(amount, eligible, winners))
        self.pot_results = tuple(results)

    def _extract_refunds(self) -> tuple[tuple[int, int], ...]:
        return tuple(
            (seat, amount)
            for seat, amount in sorted(self.refunds_by_seat.items())
            if amount > 0
        )

    def _final_stack_for_seat(self, state, seat: int) -> int:
        return int(state.stacks[self.index_by_seat[seat]])

    def _street_name(self, state) -> str:
        if state.street is None:
            return "complete"
        count = sum(len(row) for row in state.board_cards)
        return "preflop" if count == 0 else "flop" if count == 3 else "turn" if count == 4 else "river"

    def _state(self):
        if self.state is None:
            raise RuntimeError("start_hand must be called first")
        return self.state
