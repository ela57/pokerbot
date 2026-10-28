import hashlib
import json
import random
from dataclasses import FrozenInstanceError

import pytest

from pokerpractice.bots.base import (
    Bot,
    CheckCallBot,
    Observation,
    PublicSeat,
)
from pokerpractice.bots.roster import OpponentSpec
from pokerpractice.config import RulesConfig, TimingConfig
from pokerpractice.engine import Action, ActionKind, LegalActions
from pokerpractice.randomness import hand_deck, stream_seed
from pokerpractice.scoring import icm_values
from pokerpractice.tournament import run_sng


def _roster(players=8):
    return tuple(OpponentSpec("check_call", {}) for _ in range(players - 1))


def test_random_streams_are_purpose_separated_and_repeatable():
    assert stream_seed(12, "deal", 1) == stream_seed(12, "deal", 1)
    assert stream_seed(12, "deal", 1) != stream_seed(12, "bot", 1)
    assert hand_deck(12, 1) == hand_deck(12, 1)


def test_roster_factories_receive_params_and_strength(monkeypatch):
    from pokerpractice.bots import roster

    received = {}

    def factory(rng, params, strength):
        received.update(params=params, strength=strength)
        return CheckCallBot(rng)

    monkeypatch.setitem(roster.BOT_REGISTRY, "configured", factory)
    roster.make_bot(
        "configured",
        random.Random(3),
        params={"open_pct": 22},
        strength="strong",
    )
    assert received == {"params": {"open_pct": 22}, "strength": "strong"}


def test_random_legal_actions_preserve_chips_for_many_sngs():
    from pokerpractice.bots.roster import OpponentSpec

    roster = tuple(
        OpponentSpec("random" if index % 2 else "check_call", {})
        for index in range(7)
    )
    rules = RulesConfig(hand_cap=25)
    timing = TimingConfig(mode="dev", hard_timeout_ms=0)
    for seed in range(5):
        result = run_sng(seed, rules, timing, roster)
        assert not result.failed
        assert sum(result.final_stacks) == rules.players * rules.starting_stack
        assert all(
            sum(hand["ending_stacks"]) == rules.players * rules.starting_stack
            for hand in result.hand_records
        )
        assert all(row["failure_type"] != "invalid_action" for row in result.decision_records)


def test_hidden_information_and_frozen_detached_observations():
    rules = RulesConfig(players=3, hand_cap=4)
    result = run_sng(
        45,
        rules,
        TimingConfig(mode="dev", hard_timeout_ms=0),
        _roster(3),
    )
    row = result.decision_records[0]
    observation = json.dumps(row["observation"], sort_keys=True)
    assert "deck" not in observation
    assert "seed" not in observation
    assert "check_call" not in observation
    assert "random" not in observation
    assert "hole_cards" in observation
    assert "opp-" in observation
    acting_seat = row["seat"]
    for seat, cards in result.hand_records[0]["hole_cards"].items():
        if seat != acting_seat:
            assert all(card not in observation for card in cards)

    own = Observation(
        hole_cards=("As", "Kd"),
        board=(),
        street="preflop",
        my_seat=0,
        button_seat=1,
        actor_seat=0,
        seats=(
            PublicSeat(0, "opp-00000000", 100, 0, 0, False, False, True, "BTN"),
            PublicSeat(1, "opp-00000001", 100, 0, 0, False, False, True, "BB"),
        ),
        pot_total=0,
        pot_breakdown=(),
        small_blind=1,
        big_blind=2,
        level=0,
        players_remaining=2,
        to_call=0,
        legal=LegalActions(True, True, False, 0, None, None),
        action_history=(),
        hand_number=1,
        hands_remaining=1,
        payouts=(1.0, 0.0),
    )
    with pytest.raises(FrozenInstanceError):
        own.seats[0].stack = 0


def test_deterministic_replay_and_paired_deal_schedule():
    rules = RulesConfig(players=3, hand_cap=5)
    timing = TimingConfig(mode="dev", hard_timeout_ms=0)
    first = run_sng(83, rules, timing, _roster(3))
    second = run_sng(83, rules, timing, _roster(3))
    assert first.hand_records == second.hand_records
    assert first.hand_records[0]["hole_cards"] == second.hand_records[0]["hole_cards"]
    assert first.hand_records[0]["board"] == second.hand_records[0]["board"]

    def hands_content_hash(result):
        content = "".join(
            json.dumps(hand, separators=(",", ":")) + "\n"
            for hand in result.hand_records
        )
        return hashlib.sha256(content.encode()).hexdigest()

    assert hands_content_hash(first) == hands_content_hash(second)


def test_button_moves_after_each_hand():
    rules = RulesConfig(players=3, hand_cap=3)
    result = run_sng(
        17,
        rules,
        TimingConfig(mode="dev", hard_timeout_ms=0),
        _roster(3),
    )
    buttons = [hand["button"] for hand in result.hand_records]
    assert len(buttons) == 3
    assert buttons[1] == (buttons[0] + 1) % 3
    assert buttons[2] == (buttons[1] + 1) % 3


def test_elimination_advances_button_to_next_live_seat():
    rules = RulesConfig(
        players=3,
        starting_stack=20,
        blind_levels=((10, 20),),
        payouts=(100, 60, 40),
        hand_cap=4,
    )
    result = run_sng(
        17,
        rules,
        TimingConfig(mode="dev", hard_timeout_ms=0),
        _roster(3),
    )
    assert result.hand_records[0]["eliminations"]
    if len(result.hand_records) > 1:
        first = result.hand_records[0]
        live_after = {
            seat for seat, stack in enumerate(first["ending_stacks"]) if stack > 0
        }
        expected = next(
            (first["button"] + offset) % rules.players
            for offset in range(1, rules.players + 1)
            if (first["button"] + offset) % rules.players in live_after
        )
        assert result.hand_records[1]["button"] == expected


def test_bot_instances_are_fresh_per_sng_and_memory_persists_per_table(monkeypatch):
    from pokerpractice import tournament

    instances = []

    class MemoryBot(Bot):
        def __init__(self, rng=None):
            super().__init__(rng)
            self.count = 0
            instances.append(self)

        def act(self, obs):
            self.count += 1
            return Action(ActionKind.CHECK if obs.legal.can_check else ActionKind.CALL)

    def factory(style, rng, hero=False, params=None, strength="normal"):
        return MemoryBot(rng) if hero else CheckCallBot(rng)

    monkeypatch.setattr(tournament, "make_bot", factory)
    rules = RulesConfig(players=3, hand_cap=2)
    timing = TimingConfig(mode="dev", hard_timeout_ms=0)
    roster = _roster(3)
    run_sng(1, rules, timing, roster)
    first_hero = next(bot for bot in instances if isinstance(bot, MemoryBot))
    assert first_hero.count > 0
    instances.clear()
    run_sng(2, rules, timing, roster)
    second_hero = next(bot for bot in instances if isinstance(bot, MemoryBot))
    assert second_hero is not first_hero
    assert second_hero.count > 0


def test_paired_versions_receive_identical_first_hand_cards(monkeypatch):
    from pokerpractice import tournament

    class CheckBot(Bot):
        def act(self, obs):
            return Action(ActionKind.CHECK if obs.legal.can_check else ActionKind.CALL)

    class RaiseOnceBot(CheckBot):
        def __init__(self, rng=None):
            super().__init__(rng)
            self.raised = False

        def act(self, obs):
            if (
                not self.raised
                and obs.street == "preflop"
                and obs.legal.min_raise_to is not None
            ):
                self.raised = True
                return Action(ActionKind.RAISE, obs.legal.min_raise_to)
            return super().act(obs)

    original = tournament.make_bot

    def run_with(factory):
        monkeypatch.setattr(
            tournament,
            "make_bot",
            lambda style, rng, hero=False, params=None, strength="normal": (
                factory(rng)
                if hero
                else original(
                    style, rng, hero=False, params=params, strength=strength
                )
            ),
        )
        return run_sng(
            808,
            RulesConfig(players=3, hand_cap=1),
            TimingConfig(mode="dev", hard_timeout_ms=0),
            _roster(3),
        )

    check_version = run_with(CheckBot)
    raise_version = run_with(RaiseOnceBot)
    assert check_version.hand_records[0]["hole_cards"] == raise_version.hand_records[0]["hole_cards"]
    assert check_version.hand_records[0]["board"] == raise_version.hand_records[0]["board"]


def test_payouts_are_preserved_on_sng_completion():
    rules = RulesConfig(players=3, hand_cap=3)
    result = run_sng(
        101,
        rules,
        TimingConfig(mode="dev", hard_timeout_ms=0),
        _roster(3),
    )
    assert sum(result.points_by_seat) == pytest.approx(sum(rules.payouts[: rules.players]))


def test_hand_cap_settles_survivors_with_icm():
    rules = RulesConfig(
        players=3,
        payouts=(100, 60, 40),
        hand_cap=1,
    )
    result = run_sng(
        702,
        rules,
        TimingConfig(mode="dev", hard_timeout_ms=0),
        _roster(3),
    )
    assert not result.hand_records[0]["eliminations"]
    assert result.points_by_seat == pytest.approx(
        icm_values(result.final_stacks, rules.payouts)
    )
