from __future__ import annotations

import itertools
import json
import random
from dataclasses import dataclass, fields, replace
from functools import lru_cache
from pathlib import Path

import eval7

from pokerpractice.bots.base import Bot, Observation
from pokerpractice.engine.base import Action, ActionKind
from pokerpractice.randomness import STANDARD_DECK


@dataclass(frozen=True, slots=True)
class NitParams:
    open_pct: float = 8.0
    call_pct: float = 12.0
    reraise_pct: float = 3.0
    aggression_frequency: float = 0.22
    bluff_frequency: float = 0.01
    call_strength: float = 0.72
    value_threshold: float = 0.82
    bet_pot_fraction: float = 0.55


@dataclass(frozen=True, slots=True)
class TAGParams:
    open_pct: float = 22.0
    call_pct: float = 30.0
    reraise_pct: float = 9.0
    aggression_frequency: float = 0.48
    bluff_frequency: float = 0.07
    call_strength: float = 0.53
    value_threshold: float = 0.73
    bet_pot_fraction: float = 0.65


@dataclass(frozen=True, slots=True)
class LAGParams:
    open_pct: float = 39.0
    call_pct: float = 49.0
    reraise_pct: float = 17.0
    aggression_frequency: float = 0.73
    bluff_frequency: float = 0.21
    call_strength: float = 0.42
    value_threshold: float = 0.66
    bet_pot_fraction: float = 0.82


@dataclass(frozen=True, slots=True)
class CallingStationParams:
    enter_pct: float = 57.0
    call_pct: float = 67.0
    raise_strength: float = 0.88
    raise_frequency: float = 0.18
    call_strength: float = 0.16
    bluff_frequency: float = 0.01
    bet_pot_fraction: float = 0.45


@dataclass(frozen=True, slots=True)
class ManiacParams:
    open_pct: float = 76.0
    call_pct: float = 88.0
    reraise_pct: float = 40.0
    aggression_frequency: float = 0.94
    bluff_frequency: float = 0.48
    call_strength: float = 0.31
    value_threshold: float = 0.58
    bet_pot_fraction: float = 1.35


@dataclass(frozen=True, slots=True)
class EquityParams:
    open_pct: float = 30.0
    call_pct: float = 40.0
    reraise_pct: float = 11.0
    assumed_call_range_pct: float = 48.0
    assumed_raise_range_pct: float = 16.0
    monte_carlo_samples: int = 30
    call_equity_margin: float = 0.025
    value_bet_threshold: float = 0.72
    aggression_frequency: float = 0.42
    bluff_frequency: float = 0.03
    bet_pot_fraction: float = 0.68


@dataclass(frozen=True, slots=True)
class ShortStackParams:
    push_bb: float = 15.0
    push_pct: float = 25.0
    call_push_pct: float = 14.0
    deep_open_pct: float = 22.0
    deep_call_pct: float = 30.0
    deep_reraise_pct: float = 8.0
    bubble_risk: float = 0.18
    aggression_frequency: float = 0.45
    bluff_frequency: float = 0.05
    call_strength: float = 0.53
    value_threshold: float = 0.74
    bet_pot_fraction: float = 0.65


def _load_preflop_table() -> dict:
    path = Path(__file__).resolve().parents[3] / "data" / "preflop_rank.json"
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


@lru_cache(maxsize=1)
def _preflop_percentiles() -> dict[str, float]:
    return _load_preflop_table()["percentiles"]


def hand_class(cards: tuple[str, str] | list[str]) -> str:
    ranks = "23456789TJQKA"
    first, second = sorted((card[0] for card in cards), key=ranks.index, reverse=True)
    if first == second:
        return first + second
    suited = cards[0][1] == cards[1][1]
    return first + second + ("s" if suited else "o")


def preflop_percentile(cards: tuple[str, str] | list[str]) -> float:
    return _preflop_percentiles()[hand_class(cards)]


def _configured_params(param_type, values: dict, strength: str):
    if strength not in {"weak", "normal", "strong"}:
        raise ValueError(f"unknown strength preset: {strength}")
    params = param_type(**values)
    if strength == "normal":
        return params
    range_factor = 1.15 if strength == "weak" else 0.90
    aggression_factor = 0.80 if strength == "weak" else 1.15
    threshold_delta = -0.06 if strength == "weak" else 0.04
    changes = {}
    range_fields = {
        "open_pct", "call_pct", "reraise_pct", "enter_pct", "push_pct",
        "call_push_pct", "deep_open_pct", "deep_call_pct", "deep_reraise_pct",
    }
    aggression_fields = {"aggression_frequency", "bluff_frequency", "raise_frequency"}
    threshold_fields = {"call_strength", "raise_strength", "value_threshold", "value_bet_threshold"}
    for item in fields(params):
        value = getattr(params, item.name)
        if item.name in range_fields:
            value *= range_factor
        elif item.name in aggression_fields:
            value *= aggression_factor
        elif item.name in threshold_fields:
            value += threshold_delta
        elif item.name == "call_equity_margin":
            value += 0.035 if strength == "weak" else -0.01
        elif item.name == "monte_carlo_samples" and strength == "weak":
            value = max(1, round(value * 0.75))
        changes[item.name] = value
    if "monte_carlo_samples" in changes:
        changes["monte_carlo_samples"] = min(150, changes["monte_carlo_samples"])
    return replace(params, **changes)


def _position_factor(position: str) -> float:
    if position in {"BTN", "BTN/SB"}:
        return 1.35
    if position == "CO":
        return 1.2
    if position == "SB":
        return 1.0
    if position == "BB":
        return 0.9
    if position.startswith("UTG"):
        return 0.72
    return 0.95


def _legal_fallback(obs: Observation) -> Action:
    if obs.legal.can_check:
        return Action(ActionKind.CHECK)
    if obs.legal.can_call:
        return Action(ActionKind.CALL)
    return Action(ActionKind.FOLD)


def _raise_to(obs: Observation, fraction: float) -> Action | None:
    if obs.legal.min_raise_to is None or obs.legal.max_raise_to is None:
        return None
    current_bet = obs.seats[obs.my_seat].bet_this_street
    call_total = current_bet + obs.legal.call_amount
    increment = max(obs.big_blind, int((obs.pot_total + obs.legal.call_amount) * fraction))
    target = min(obs.legal.max_raise_to, max(obs.legal.min_raise_to, call_total + increment))
    return Action(ActionKind.RAISE, target)


def _preflop_action(
    bot: Bot,
    obs: Observation,
    *,
    open_pct: float,
    call_pct: float,
    reraise_pct: float,
    aggression: float,
    bet_pot_fraction: float,
    style: str,
) -> Action:
    percentile = preflop_percentile(obs.hole_cards)
    factor = _position_factor(obs.seats[obs.my_seat].position)
    raise_seen = any(
        action.street == "preflop" and action.kind == "raise"
        for action in obs.action_history
    )
    if raise_seen:
        if percentile <= reraise_pct * factor and bot.rng.random() < aggression:
            action = _raise_to(obs, bet_pot_fraction)
            if action:
                bot.last_diagnostics = {
                    "style": style, "hand_class": hand_class(obs.hole_cards),
                    "preflop_percentile": percentile, "reason": "reraise range",
                }
                return action
        if obs.legal.can_call and percentile <= call_pct * factor:
            bot.last_diagnostics = {
                "style": style, "hand_class": hand_class(obs.hole_cards),
                "preflop_percentile": percentile, "reason": "continue range",
            }
            return Action(ActionKind.CALL)
        bot.last_diagnostics = {
            "style": style, "hand_class": hand_class(obs.hole_cards),
            "preflop_percentile": percentile, "reason": "outside continue range",
        }
        return _legal_fallback(obs) if obs.legal.can_check else Action(ActionKind.FOLD)
    if percentile <= open_pct * factor:
        if bot.rng.random() < aggression:
            action = _raise_to(obs, bet_pot_fraction)
            if action:
                bot.last_diagnostics = {
                    "style": style, "hand_class": hand_class(obs.hole_cards),
                    "preflop_percentile": percentile, "reason": "opening range",
                }
                return action
        bot.last_diagnostics = {
            "style": style, "hand_class": hand_class(obs.hole_cards),
            "preflop_percentile": percentile, "reason": "passive open",
        }
        return _legal_fallback(obs)
    if obs.legal.can_call and percentile <= call_pct * factor:
        bot.last_diagnostics = {
            "style": style, "hand_class": hand_class(obs.hole_cards),
            "preflop_percentile": percentile, "reason": "limp/call range",
        }
        return Action(ActionKind.CALL)
    bot.last_diagnostics = {
        "style": style, "hand_class": hand_class(obs.hole_cards),
        "preflop_percentile": percentile, "reason": "outside entry range",
    }
    return _legal_fallback(obs) if obs.legal.can_check else Action(ActionKind.FOLD)


_HAND_STRENGTH = {
    "High Card": 0.16,
    "Pair": 0.39,
    "Two Pair": 0.59,
    "Trips": 0.73,
    "Straight": 0.84,
    "Flush": 0.88,
    "Full House": 0.95,
    "Quads": 0.985,
    "Straight Flush": 0.998,
}


def made_hand_strength(obs: Observation) -> float:
    cards = tuple(eval7.Card(card) for card in (*obs.hole_cards, *obs.board))
    if len(cards) < 5:
        return 0.2
    category = eval7.handtype(eval7.evaluate(cards))
    strength = _HAND_STRENGTH.get(category, 0.2)
    board_ranks = {card[0] for card in obs.board}
    hole_ranks = {card[0] for card in obs.hole_cards}
    if category == "Pair" and hole_ranks & board_ranks:
        ranks = "23456789TJQKA"
        top_board = max(board_ranks, key=ranks.index)
        paired = max(hole_ranks & board_ranks, key=ranks.index)
        strength += 0.08 if ranks.index(paired) == ranks.index(top_board) else 0.02
    suit_counts = {}
    for card in (*obs.hole_cards, *obs.board):
        suit_counts[card[1]] = suit_counts.get(card[1], 0) + 1
    if len(obs.board) < 5 and max(suit_counts.values(), default=0) == 4:
        strength += 0.11
    rank_values = {rank: index + 2 for index, rank in enumerate("23456789TJQKA")}
    ranks = {rank_values[card[0]] for card in (*obs.hole_cards, *obs.board)}
    if 14 in ranks:
        ranks.add(1)
    if len(obs.board) < 5 and any(
        sum(rank in ranks for rank in range(start, start + 5)) == 4
        for start in range(1, 11)
    ):
        strength += 0.07
    return min(strength, 0.99)


def _postflop_action(
    bot: Bot,
    obs: Observation,
    *,
    call_strength: float,
    value_threshold: float,
    aggression: float,
    bluff_frequency: float,
    bet_pot_fraction: float,
    style: str,
) -> Action:
    strength = made_hand_strength(obs)
    price = obs.legal.call_amount / max(1, obs.pot_total + obs.legal.call_amount)
    if obs.legal.can_check:
        if (
            obs.legal.min_raise_to is not None
            and strength >= value_threshold
            and bot.rng.random() < aggression
        ):
            action = _raise_to(obs, bet_pot_fraction)
            if action:
                bot.last_diagnostics = {"style": style, "hand_strength": strength, "reason": "value bet"}
                return action
        if (
            obs.legal.min_raise_to is not None
            and strength < call_strength
            and bot.rng.random() < bluff_frequency
        ):
            action = _raise_to(obs, bet_pot_fraction)
            if action:
                bot.last_diagnostics = {"style": style, "hand_strength": strength, "reason": "bluff"}
                return action
        bot.last_diagnostics = {"style": style, "hand_strength": strength, "reason": "check"}
        return Action(ActionKind.CHECK)
    if not obs.legal.can_call:
        return _legal_fallback(obs)
    if (
        obs.legal.min_raise_to is not None
        and strength >= value_threshold
        and bot.rng.random() < aggression
    ):
        action = _raise_to(obs, bet_pot_fraction)
        if action:
            bot.last_diagnostics = {"style": style, "hand_strength": strength, "reason": "value raise"}
            return action
    if strength >= call_strength or strength >= price * 1.15:
        bot.last_diagnostics = {
            "style": style, "hand_strength": strength, "pot_odds": price, "reason": "call",
        }
        return Action(ActionKind.CALL)
    if obs.legal.min_raise_to is not None and bot.rng.random() < bluff_frequency:
        action = _raise_to(obs, bet_pot_fraction)
        if action:
            bot.last_diagnostics = {"style": style, "hand_strength": strength, "reason": "bluff raise"}
            return action
    bot.last_diagnostics = {
        "style": style, "hand_strength": strength, "pot_odds": price, "reason": "fold",
    }
    return Action(ActionKind.FOLD)


class NitBot(Bot):
    def __init__(self, rng: random.Random, params: NitParams) -> None:
        super().__init__(rng)
        self.params = params

    def act(self, obs: Observation) -> Action:
        if obs.street == "preflop":
            return _preflop_action(
                self, obs, open_pct=self.params.open_pct, call_pct=self.params.call_pct,
                reraise_pct=self.params.reraise_pct, aggression=self.params.aggression_frequency,
                bet_pot_fraction=self.params.bet_pot_fraction, style="nit",
            )
        return _postflop_action(
            self, obs, call_strength=self.params.call_strength,
            value_threshold=self.params.value_threshold,
            aggression=self.params.aggression_frequency, bluff_frequency=self.params.bluff_frequency,
            bet_pot_fraction=self.params.bet_pot_fraction, style="nit",
        )


class TAGBot(Bot):
    def __init__(self, rng: random.Random, params: TAGParams) -> None:
        super().__init__(rng)
        self.params = params

    def act(self, obs: Observation) -> Action:
        if obs.street == "preflop":
            return _preflop_action(
                self, obs, open_pct=self.params.open_pct, call_pct=self.params.call_pct,
                reraise_pct=self.params.reraise_pct, aggression=self.params.aggression_frequency,
                bet_pot_fraction=self.params.bet_pot_fraction, style="tag",
            )
        return _postflop_action(
            self, obs, call_strength=self.params.call_strength,
            value_threshold=self.params.value_threshold,
            aggression=self.params.aggression_frequency, bluff_frequency=self.params.bluff_frequency,
            bet_pot_fraction=self.params.bet_pot_fraction, style="tag",
        )


class LAGBot(Bot):
    def __init__(self, rng: random.Random, params: LAGParams) -> None:
        super().__init__(rng)
        self.params = params

    def act(self, obs: Observation) -> Action:
        if obs.street == "preflop":
            return _preflop_action(
                self, obs, open_pct=self.params.open_pct, call_pct=self.params.call_pct,
                reraise_pct=self.params.reraise_pct, aggression=self.params.aggression_frequency,
                bet_pot_fraction=self.params.bet_pot_fraction, style="lag",
            )
        return _postflop_action(
            self, obs, call_strength=self.params.call_strength,
            value_threshold=self.params.value_threshold,
            aggression=self.params.aggression_frequency, bluff_frequency=self.params.bluff_frequency,
            bet_pot_fraction=self.params.bet_pot_fraction, style="lag",
        )


class CallingStationBot(Bot):
    def __init__(self, rng: random.Random, params: CallingStationParams) -> None:
        super().__init__(rng)
        self.params = params

    def act(self, obs: Observation) -> Action:
        if obs.street == "preflop":
            percentile = preflop_percentile(obs.hole_cards)
            factor = _position_factor(obs.seats[obs.my_seat].position)
            if percentile <= self.params.enter_pct * factor:
                if obs.legal.can_call:
                    self.last_diagnostics = {"style": "calling_station", "preflop_percentile": percentile, "reason": "wide call"}
                    return Action(ActionKind.CALL)
                if (
                    obs.legal.min_raise_to is not None
                    and percentile <= self.params.call_pct * factor
                    and bot_roll(self, self.params.raise_frequency)
                ):
                    action = _raise_to(obs, self.params.bet_pot_fraction)
                    if action:
                        self.last_diagnostics = {"style": "calling_station", "preflop_percentile": percentile, "reason": "occasional open"}
                        return action
                if obs.legal.can_check:
                    self.last_diagnostics = {"style": "calling_station", "preflop_percentile": percentile, "reason": "check"}
                    return Action(ActionKind.CHECK)
            return _legal_fallback(obs)
        strength = made_hand_strength(obs)
        if obs.legal.can_check:
            if strength >= self.params.raise_strength and bot_roll(self, self.params.raise_frequency):
                action = _raise_to(obs, self.params.bet_pot_fraction)
                if action:
                    self.last_diagnostics = {"style": "calling_station", "hand_strength": strength, "reason": "strong value"}
                    return action
            self.last_diagnostics = {"style": "calling_station", "hand_strength": strength, "reason": "passive check"}
            return Action(ActionKind.CHECK)
        if obs.legal.can_call:
            price = obs.legal.call_amount / max(1, obs.pot_total + obs.legal.call_amount)
            if strength >= self.params.call_strength or price <= 0.12:
                self.last_diagnostics = {"style": "calling_station", "hand_strength": strength, "pot_odds": price, "reason": "wide call"}
                return Action(ActionKind.CALL)
        return _legal_fallback(obs)


class ManiacBot(Bot):
    def __init__(self, rng: random.Random, params: ManiacParams) -> None:
        super().__init__(rng)
        self.params = params

    def act(self, obs: Observation) -> Action:
        if obs.street == "preflop":
            return _preflop_action(
                self, obs, open_pct=self.params.open_pct, call_pct=self.params.call_pct,
                reraise_pct=self.params.reraise_pct, aggression=self.params.aggression_frequency,
                bet_pot_fraction=self.params.bet_pot_fraction, style="maniac",
            )
        strength = made_hand_strength(obs)
        if obs.legal.min_raise_to is not None:
            pressure = self.params.aggression_frequency * (0.55 + strength / 2)
            if self.rng.random() < pressure:
                action = _raise_to(obs, self.params.bet_pot_fraction)
                if action:
                    self.last_diagnostics = {
                        "style": "maniac", "hand_strength": strength,
                        "reason": "high-frequency pressure",
                    }
                    return action
        if obs.legal.can_check:
            self.last_diagnostics = {"style": "maniac", "hand_strength": strength, "reason": "check"}
            return Action(ActionKind.CHECK)
        if obs.legal.can_call and (
            strength >= self.params.call_strength or self.rng.random() < self.params.bluff_frequency
        ):
            self.last_diagnostics = {"style": "maniac", "hand_strength": strength, "reason": "wide call"}
            return Action(ActionKind.CALL)
        return _legal_fallback(obs)


def bot_roll(bot: Bot, probability: float) -> bool:
    return bot.rng.random() < probability


@lru_cache(maxsize=101)
def _range_combos(max_percentile: int) -> tuple[tuple[str, str], ...]:
    return tuple(
        combo
        for combo in itertools.combinations(STANDARD_DECK, 2)
        if preflop_percentile(combo) <= max_percentile
    )


_CARD_OBJECTS = {card: eval7.Card(card) for card in STANDARD_DECK}


class EquityBot(Bot):
    def __init__(self, rng: random.Random, params: EquityParams) -> None:
        super().__init__(rng)
        self.params = params
        self.samples = max(1, min(150, int(params.monte_carlo_samples)))
        _preflop_percentiles()
        _range_combos(round(params.assumed_call_range_pct))
        _range_combos(round(params.assumed_raise_range_pct))

    def _assumed_ranges(self, obs: Observation, opponents) -> list[int]:
        ranges = []
        for opponent in opponents:
            previous = [
                action.kind
                for action in obs.action_history
                if action.seat_anon == opponent.anon_id and action.street == "preflop"
            ]
            if "raise" in previous:
                ranges.append(round(self.params.assumed_raise_range_pct))
            elif "call" in previous:
                ranges.append(round(self.params.assumed_call_range_pct))
            else:
                ranges.append(100)
        return ranges

    def _equity(self, obs: Observation) -> tuple[float, list[int]]:
        opponents = [
            seat for seat in obs.seats
            if seat.seat != obs.my_seat and seat.active
        ]
        if not opponents:
            return 1.0, []
        assumed_ranges = self._assumed_ranges(obs, opponents)
        known = set((*obs.hole_cards, *obs.board))
        missing_board = 5 - len(obs.board)
        equity = 0.0
        for _ in range(self.samples):
            used = set(known)
            opponent_hands = []
            for range_pct in assumed_ranges:
                candidates = _range_combos(max(1, min(100, range_pct)))
                chosen = None
                for _attempt in range(8):
                    candidate = self.rng.choice(candidates)
                    if candidate[0] not in used and candidate[1] not in used:
                        chosen = candidate
                        break
                if chosen is None:
                    available = [card for card in STANDARD_DECK if card not in used]
                    chosen = tuple(self.rng.sample(available, 2))
                used.update(chosen)
                opponent_hands.append(chosen)
            available = [card for card in STANDARD_DECK if card not in used]
            runout = self.rng.sample(available, missing_board)
            board = [_CARD_OBJECTS[card] for card in obs.board]
            board.extend(_CARD_OBJECTS[card] for card in runout)
            hero_rank = eval7.evaluate(
                [_CARD_OBJECTS[card] for card in obs.hole_cards] + board
            )
            ranks = [
                eval7.evaluate([_CARD_OBJECTS[card] for card in hand] + board)
                for hand in opponent_hands
            ]
            best = max([hero_rank, *ranks])
            if hero_rank == best:
                equity += 1 / (1 + sum(rank == best for rank in ranks))
        return equity / self.samples, assumed_ranges

    def act(self, obs: Observation) -> Action:
        if obs.street == "preflop":
            return _preflop_action(
                self, obs, open_pct=self.params.open_pct, call_pct=self.params.call_pct,
                reraise_pct=self.params.reraise_pct, aggression=self.params.aggression_frequency,
                bet_pot_fraction=self.params.bet_pot_fraction, style="equity",
            )
        equity, assumed_ranges = self._equity(obs)
        price = obs.legal.call_amount / max(1, obs.pot_total + obs.legal.call_amount)
        if obs.legal.can_check:
            if (
                obs.legal.min_raise_to is not None
                and equity >= self.params.value_bet_threshold
                and self.rng.random() < self.params.aggression_frequency
            ):
                action = _raise_to(obs, self.params.bet_pot_fraction)
                if action:
                    self.last_diagnostics = {
                        "style": "equity", "equity_vs_assumed_ranges": equity,
                        "assumed_range_percentiles": assumed_ranges,
                        "pot_odds": price, "samples": self.samples, "reason": "value bet",
                    }
                    return action
            self.last_diagnostics = {
                "style": "equity", "equity_vs_assumed_ranges": equity,
                "assumed_range_percentiles": assumed_ranges,
                "samples": self.samples, "reason": "check",
            }
            return Action(ActionKind.CHECK)
        if not obs.legal.can_call:
            return _legal_fallback(obs)
        if (
            obs.legal.min_raise_to is not None
            and equity >= max(self.params.value_bet_threshold, 0.72)
            and self.rng.random() < self.params.aggression_frequency
        ):
            action = _raise_to(obs, self.params.bet_pot_fraction)
            if action:
                self.last_diagnostics = {
                    "style": "equity", "equity_vs_assumed_ranges": equity,
                    "assumed_range_percentiles": assumed_ranges,
                    "pot_odds": price, "samples": self.samples, "reason": "value raise",
                }
                return action
        if equity >= price + self.params.call_equity_margin:
            self.last_diagnostics = {
                "style": "equity", "equity_vs_assumed_ranges": equity,
                "assumed_range_percentiles": assumed_ranges,
                "pot_odds": price, "samples": self.samples, "reason": "pot-odds call",
            }
            return Action(ActionKind.CALL)
        self.last_diagnostics = {
            "style": "equity", "equity_vs_assumed_ranges": equity,
            "assumed_range_percentiles": assumed_ranges,
            "pot_odds": price, "samples": self.samples, "reason": "fold",
        }
        return Action(ActionKind.FOLD)


class ShortStackBot(Bot):
    def __init__(self, rng: random.Random, params: ShortStackParams) -> None:
        super().__init__(rng)
        self.params = params

    def _risk_adjustment(self, obs: Observation) -> float:
        if obs.players_remaining > 3 or len(obs.payouts) < 2:
            return 1.0
        largest_jump = max(
            (obs.payouts[index] - obs.payouts[index + 1]
             for index in range(min(obs.players_remaining - 1, len(obs.payouts) - 1))),
            default=0.0,
        )
        if largest_jump <= 0:
            return 1.0
        hero_stack = obs.seats[obs.my_seat].stack
        if hero_stack >= max(seat.stack for seat in obs.seats if seat.active):
            return 1.0
        return max(0.35, 1.0 - self.params.bubble_risk)

    def act(self, obs: Observation) -> Action:
        if obs.street == "preflop":
            stack_bb = obs.seats[obs.my_seat].stack / max(1, obs.big_blind)
            percentile = preflop_percentile(obs.hole_cards)
            factor = _position_factor(obs.seats[obs.my_seat].position)
            risk = self._risk_adjustment(obs)
            if stack_bb <= self.params.push_bb:
                raise_seen = any(
                    action.street == "preflop" and action.kind == "raise"
                    for action in obs.action_history
                )
                if raise_seen:
                    if (
                        obs.legal.min_raise_to is not None
                        and percentile <= self.params.push_pct * factor * risk
                    ):
                        self.last_diagnostics = {
                            "style": "short_stack", "stack_bb": stack_bb,
                            "preflop_percentile": percentile, "risk_factor": risk,
                            "reason": "short-stack rejam heuristic",
                        }
                        return Action(ActionKind.RAISE, obs.legal.max_raise_to)
                    if percentile <= self.params.call_push_pct * factor * risk and obs.legal.can_call:
                        self.last_diagnostics = {
                            "style": "short_stack", "stack_bb": stack_bb,
                            "preflop_percentile": percentile, "risk_factor": risk,
                            "reason": "short-stack call heuristic",
                        }
                        return Action(ActionKind.CALL)
                    return _legal_fallback(obs) if obs.legal.can_check else Action(ActionKind.FOLD)
                if percentile <= self.params.push_pct * factor * risk and obs.legal.max_raise_to is not None:
                    self.last_diagnostics = {
                        "style": "short_stack", "stack_bb": stack_bb,
                        "preflop_percentile": percentile, "risk_factor": risk,
                        "reason": "position-aware push heuristic",
                    }
                    return Action(ActionKind.RAISE, obs.legal.max_raise_to)
                return _legal_fallback(obs)
            return _preflop_action(
                self, obs, open_pct=self.params.deep_open_pct, call_pct=self.params.deep_call_pct,
                reraise_pct=self.params.deep_reraise_pct,
                aggression=self.params.aggression_frequency,
                bet_pot_fraction=self.params.bet_pot_fraction, style="short_stack_deep",
            )
        return _postflop_action(
            self, obs, call_strength=self.params.call_strength,
            value_threshold=self.params.value_threshold,
            aggression=self.params.aggression_frequency, bluff_frequency=self.params.bluff_frequency,
            bet_pot_fraction=self.params.bet_pot_fraction, style="short_stack_deep",
        )


def _factory(bot_type, param_type):
    def make(rng: random.Random, params: dict, strength: str) -> Bot:
        effective = _configured_params(param_type, params, strength)
        return bot_type(rng, effective)

    return make


STRATEGY_FACTORIES = {
    "nit": _factory(NitBot, NitParams),
    "tag": _factory(TAGBot, TAGParams),
    "lag": _factory(LAGBot, LAGParams),
    "calling_station": _factory(CallingStationBot, CallingStationParams),
    "maniac": _factory(ManiacBot, ManiacParams),
    "equity": _factory(EquityBot, EquityParams),
    "short_stack": _factory(ShortStackBot, ShortStackParams),
}
