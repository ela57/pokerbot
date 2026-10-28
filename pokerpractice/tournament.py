from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import time
from dataclasses import asdict, dataclass

from pokerpractice.bots.base import (
    Bot,
    Observation,
    PublicAction,
    PublicHandResult,
    PublicSeat,
    TableStartInfo,
)
from pokerpractice.bots.roster import OpponentSpec, make_bot
from pokerpractice.config import RulesConfig, TimingConfig
from pokerpractice.engine.base import Action, ActionKind, HandResult, NeutralActionEvent
from pokerpractice.engine.pokerkit_holdem import PokerKitHoldemAdapter
from pokerpractice.randomness import hand_deck, stream
from pokerpractice.scoring import award_simultaneous_busts, icm_values
from pokerpractice.timing import call_bot


@dataclass(frozen=True, slots=True)
class SNGResult:
    seed: int
    hero_seat: int
    hero_finish: int | None
    hero_points: float
    hero_final_stack: int
    hero_busted: bool
    hands_played: int
    failed: bool
    points_by_seat: tuple[float, ...]
    final_stacks: tuple[int, ...]
    hand_records: tuple[dict, ...]
    hand_summaries: tuple[dict, ...]
    decision_records: tuple[dict, ...]
    stats: dict[str, dict]
    elapsed_ns: int


def _anon_id(rng: random.Random) -> str:
    return f"opp-{rng.getrandbits(32):08x}"


def _positions(live_seats: tuple[int, ...], button: int) -> dict[int, str]:
    clockwise = live_seats[live_seats.index(button) :] + live_seats[: live_seats.index(button)]
    n = len(clockwise)
    if n == 2:
        return {clockwise[0]: "BTN/SB", clockwise[1]: "BB"}
    result = {clockwise[0]: "BTN", clockwise[1]: "SB", clockwise[2]: "BB"}
    remaining = n - 3
    for offset, seat in enumerate(clockwise[3:]):
        result[seat] = "CO" if offset == remaining - 1 else "UTG" if offset == 0 else f"MP{offset}"
    return result


def _stack_bucket(stack: int, big_blind: int) -> str:
    depth = stack / max(big_blind, 1)
    if depth < 10:
        return "<10"
    if depth < 20:
        return "10-20"
    if depth < 40:
        return "20-40"
    if depth < 100:
        return "40-100"
    return "100+"


def _action_dict(action: Action | None) -> dict | None:
    if action is None:
        return None
    return {"kind": action.kind.value, "amount": action.amount, "diag": action.diag}


def _event_dict(event: NeutralActionEvent) -> dict:
    return {
        "seat": event.seat,
        "street": event.street,
        "kind": event.kind,
        "amount": event.amount,
    }


def _hand_hash(records: list[dict]) -> str:
    body = "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in records)
    return hashlib.sha256(body.encode()).hexdigest()


def _make_observation(
    adapter: PokerKitHoldemAdapter,
    action_history: list[PublicAction],
    seat: int,
    button: int,
    anon_ids: tuple[str, ...],
    live_seats: tuple[int, ...],
    positions: dict[int, str],
    hole_cards: dict[int, tuple[str, str]],
    hand_number: int,
    rules: RulesConfig,
    small_blind: int,
    big_blind: int,
    level: int,
) -> Observation:
    public = adapter.public_view()
    current_actor = public["actor_seat"]
    if current_actor is None:
        raise RuntimeError("cannot build an observation without an actor")
    seats = tuple(
        PublicSeat(
            seat=index,
            anon_id=anon_ids[index],
            stack=public["stacks"][index],
            bet_this_street=public["bets"][index],
            total_contributed=public["contributions"][index],
            folded=public["folded"][index],
            all_in=public["all_in"][index],
            active=public["active"][index],
            position=positions.get(index, "OUT"),
        )
        for index in range(len(public["stacks"]))
    )
    return Observation(
        hole_cards=hole_cards[seat],
        board=public["board"],
        street=public["street"],
        my_seat=seat,
        button_seat=button,
        actor_seat=current_actor,
        seats=seats,
        pot_total=public["pot_total"],
        pot_breakdown=tuple(
            (row["amount"], row["eligible_seats"]) for row in public["pots"]
        ),
        small_blind=small_blind,
        big_blind=big_blind,
        level=level,
        players_remaining=len(live_seats),
        to_call=public["to_call"],
        legal=adapter.legal_actions(),
        action_history=tuple(action_history),
        hand_number=hand_number,
        hands_remaining=max(0, rules.hand_cap - hand_number),
        payouts=rules.payouts,
    )


def _stats(decisions: list[dict], hands: list[dict]) -> dict[str, dict]:
    grouped: dict[str, list[dict]] = {}
    for row in decisions:
        key = row["bot_label"]
        grouped.setdefault(key, []).append(row)
    output = {}
    for label, rows in grouped.items():
        preflop = [row for row in rows if row["street"] == "preflop"]
        postflop = [row for row in rows if row["street"] != "preflop"]
        eligible_hands = {(row["seat"], row["hand_number"]) for row in preflop}
        vpip_hands = {
            (row["seat"], row["hand_number"])
            for row in preflop
            if row["applied_action"]["kind"] in ("CALL", "RAISE")
        }
        pfr_hands = {
            (row["seat"], row["hand_number"])
            for row in preflop
            if row["applied_action"]["kind"] == "RAISE"
        }
        raises_bets = sum(row["applied_action"]["kind"] == "RAISE" for row in postflop)
        calls = sum(row["applied_action"]["kind"] == "CALL" for row in postflop)
        checks = sum(row["applied_action"]["kind"] == "CHECK" for row in postflop)
        folds = sum(row["applied_action"]["kind"] == "FOLD" for row in postflop)
        positions = {}
        stack_buckets = {}
        for row in rows:
            positions[row["position"]] = positions.get(row["position"], 0) + 1
            stack_buckets[row["stack_bucket"]] = (
                stack_buckets.get(row["stack_bucket"], 0) + 1
            )
        seats = {row["seat"] for row in rows}
        hands_saw_flop = 0
        hands_reaching_showdown = 0
        hands_won_at_showdown = 0
        for hand in hands:
            folded_preflop = {
                event["seat"]
                for event in hand["actions"]
                if event["street"] == "preflop" and event["kind"] == "fold"
            }
            folded_any = {
                event["seat"] for event in hand["actions"] if event["kind"] == "fold"
            }
            has_showdown = bool(hand["showdown_ranks"])
            for seat in seats:
                if hand["starting_stacks"][seat] <= 0 or seat in folded_preflop:
                    continue
                if len(hand["board"]) >= 3:
                    hands_saw_flop += 1
                    if has_showdown and seat not in folded_any:
                        hands_reaching_showdown += 1
                        if any(seat in pot["winners"] for pot in hand["pots"]):
                            hands_won_at_showdown += 1
        latencies = sorted(row["latency_ns"] for row in rows)

        def quantile(q: float) -> float:
            if not latencies:
                return 0.0
            index = max(0, min(len(latencies) - 1, math.ceil(q * len(latencies)) - 1))
            return latencies[index] / 1000

        output[label] = {
            "vpip": len(vpip_hands) / len(eligible_hands) if eligible_hands else 0.0,
            "vpip_count": len(vpip_hands),
            "vpip_opportunities": len(eligible_hands),
            "pfr": len(pfr_hands) / len(eligible_hands) if eligible_hands else 0.0,
            "pfr_count": len(pfr_hands),
            "pfr_opportunities": len(eligible_hands),
            "af": raises_bets / calls if calls else None if raises_bets else 0.0,
            "af_numerator": raises_bets,
            "af_denominator": calls,
            "afq": raises_bets / (raises_bets + calls + checks + folds)
            if raises_bets + calls + checks + folds
            else 0.0,
            "afq_numerator": raises_bets,
            "afq_denominator": raises_bets + calls + checks + folds,
            "postflop_decisions": raises_bets + calls + checks + folds,
            "wtsd": hands_reaching_showdown / hands_saw_flop if hands_saw_flop else 0.0,
            "wtsd_count": hands_reaching_showdown,
            "wtsd_opportunities": hands_saw_flop,
            "wusd": (
                hands_won_at_showdown / hands_reaching_showdown
                if hands_reaching_showdown
                else 0.0
            ),
            "wusd_count": hands_won_at_showdown,
            "wusd_opportunities": hands_reaching_showdown,
            "position_decisions": positions,
            "stack_bucket_decisions": stack_buckets,
            "latency_us": {
                "median": statistics.median(latencies) / 1000 if latencies else 0.0,
                "p95": quantile(0.95),
                "p99": quantile(0.99),
                "max": latencies[-1] / 1000 if latencies else 0.0,
                "count": len(latencies),
            },
            "latencies_ns": latencies,
            "failure_count": sum(row["failure_type"] is not None for row in rows),
            "failure_counts": {
                failure: sum(row["failure_type"] == failure for row in rows)
                for failure in ("exception", "invalid_action", "timeout_soft", "timeout_hard")
            },
        }
        first_preflop = {}
        for row in preflop:
            first_preflop.setdefault((row["seat"], row["hand_number"]), row)

        def group_key(field, row):
            if field == "lineup":
                return tuple(row["lineup"])
            return row[field]

        for field, output_key in (
            ("position", "by_position"),
            ("stack_bucket", "by_stack_bucket"),
            ("lineup", "by_lineup"),
        ):
            grouped = {}
            for row in rows:
                grouped.setdefault(group_key(field, row), []).append(row)
            group_stats = {}
            for key, selected in grouped.items():
                opportunities = {
                    hand_key
                    for hand_key, initial in first_preflop.items()
                    if group_key(field, initial) == key
                }
                opportunity_rows = [
                    row for row in preflop
                    if (row["seat"], row["hand_number"]) in opportunities
                ]
                vpip = {
                    hand_key
                    for hand_key in opportunities
                    if any(
                        (row["seat"], row["hand_number"]) == hand_key
                        and row["applied_action"]["kind"] in ("CALL", "RAISE")
                        for row in opportunity_rows
                    )
                }
                pfr = {
                    hand_key
                    for hand_key in opportunities
                    if any(
                        (row["seat"], row["hand_number"]) == hand_key
                        and row["applied_action"]["kind"] == "RAISE"
                        for row in opportunity_rows
                    )
                }
                selected_postflop = [row for row in selected if row["street"] != "preflop"]
                bets_raises = sum(
                    row["applied_action"]["kind"] == "RAISE"
                    for row in selected_postflop
                )
                selected_calls = sum(
                    row["applied_action"]["kind"] == "CALL"
                    for row in selected_postflop
                )
                selected_checks = sum(
                    row["applied_action"]["kind"] == "CHECK"
                    for row in selected_postflop
                )
                selected_folds = sum(
                    row["applied_action"]["kind"] == "FOLD"
                    for row in selected_postflop
                )
                saw_flop = showdowns = won_showdown = 0
                seats_in_group = {row["seat"] for row in selected}
                for hand in hands:
                    folded_preflop = {
                        event["seat"] for event in hand["actions"]
                        if event["street"] == "preflop" and event["kind"] == "FOLD"
                    }
                    folded_any = {
                        event["seat"] for event in hand["actions"]
                        if event["kind"] == "FOLD"
                    }
                    for seat in seats_in_group:
                        initial = first_preflop.get((seat, hand["hand_number"]))
                        if (
                            initial is None
                            or group_key(field, initial) != key
                            or hand["starting_stacks"][seat] <= 0
                            or seat in folded_preflop
                            or len(hand["board"]) < 3
                        ):
                            continue
                        saw_flop += 1
                        if hand["showdown_ranks"] and seat not in folded_any:
                            showdowns += 1
                            if any(seat in pot["winners"] for pot in hand["pots"]):
                                won_showdown += 1
                group_latencies = sorted(row["latency_ns"] for row in selected)

                def group_percentile(q):
                    if not group_latencies:
                        return 0.0
                    index = max(0, math.ceil(q * len(group_latencies)) - 1)
                    return group_latencies[index] / 1000

                key_text = "|".join(key) if isinstance(key, tuple) else str(key)
                group_stats[key_text] = {
                    "vpip_count": len(vpip),
                    "vpip_opportunities": len(opportunities),
                    "pfr_count": len(pfr),
                    "pfr_opportunities": len(opportunities),
                    "af_numerator": bets_raises,
                    "af_denominator": selected_calls,
                    "afq_numerator": bets_raises,
                    "afq_denominator": (
                        bets_raises + selected_calls + selected_checks + selected_folds
                    ),
                    "wtsd_count": showdowns,
                    "wtsd_opportunities": saw_flop,
                    "wusd_count": won_showdown,
                    "wusd_opportunities": showdowns,
                    "latency_us": {
                        "median": statistics.median(group_latencies) / 1000
                        if group_latencies else 0.0,
                        "p95": group_percentile(0.95),
                        "p99": group_percentile(0.99),
                        "max": group_latencies[-1] / 1000 if group_latencies else 0.0,
                        "count": len(group_latencies),
                    },
                    "failure_count": sum(
                        row["failure_type"] is not None for row in selected
                    ),
                }
            output[label][output_key] = group_stats
    return output


def run_sng(
    seed: int,
    rules: RulesConfig,
    timing: TimingConfig,
    roster: tuple[OpponentSpec, ...],
    hero_path: str | Path | None = None,
    log_level: str = "full",
) -> SNGResult:
    started = time.perf_counter_ns()
    if rules.players != len(roster) + 1:
        raise ValueError("player count must equal hero plus roster entries")
    if log_level not in {"full", "hero", "summary"}:
        raise ValueError(f"unsupported log level: {log_level}")
    lineup = tuple(spec.style for spec in roster)
    seat_rng = stream(seed, "seating")
    seats = list(range(rules.players))
    seat_rng.shuffle(seats)
    hero_seat = seats[0]
    spec_order = list(roster)
    seat_rng.shuffle(spec_order)
    anon_ids_list = [_anon_id(seat_rng) for _ in range(rules.players)]
    anon_ids = tuple(anon_ids_list)
    button = seat_rng.randrange(rules.players)
    bots: list[Bot] = [None] * rules.players  # type: ignore[list-item]
    bot_labels = [""] * rules.players
    for seat in range(rules.players):
        if seat == hero_seat:
            if hero_path is None:
                bots[seat] = make_bot("hero", stream(seed, "bot", seat), hero=True)
            else:
                bots[seat] = make_bot(
                    "hero",
                    stream(seed, "bot", seat),
                    hero=True,
                    hero_path=hero_path,
                )
            bot_labels[seat] = "hero"
        else:
            spec = spec_order.pop()
            bots[seat] = make_bot(
                spec.style,
                stream(seed, "bot", seat),
                params=spec.params,
                strength=spec.strength,
            )
            bot_labels[seat] = spec.style
    for seat, bot in enumerate(bots):
        bot.on_table_start(
            TableStartInfo(
                my_seat=seat,
                seat_count=rules.players,
                starting_stack=rules.starting_stack,
                payouts=rules.payouts,
                anon_ids=anon_ids,
            )
        )

    stacks = [rules.starting_stack] * rules.players
    total_chips = sum(stacks)
    points = [0.0] * rules.players
    bust_finish: dict[int, int] = {}
    live = set(range(rules.players))
    hand_records: list[dict] = []
    hand_summaries: list[dict] = []
    decision_records: list[dict] = []
    stats_hand_records: list[dict] = []
    hand_number = 0
    adapter = PokerKitHoldemAdapter()
    while len(live) > 1 and hand_number < rules.hand_cap:
        hand_number += 1
        live_seats = tuple(sorted(live))
        if button not in live:
            button = next((button + offset) % rules.players for offset in range(1, rules.players + 1) if (button + offset) % rules.players in live)
        small_blind, big_blind = rules.blind_levels[
            min((hand_number - 1) // rules.blind_level_hands, len(rules.blind_levels) - 1)
        ]
        deck = hand_deck(seed, hand_number)
        hole_cards = {
            seat: (deck[2 * seat], deck[2 * seat + 1])
            for seat in live_seats
        }
        board_cards = tuple(deck[16:21])
        burn_cards = tuple(deck[21:24])
        starting = tuple(stacks)
        adapter.start_hand(
            starting,
            live_seats,
            button,
            small_blind,
            big_blind,
            hole_cards,
            board_cards,
            burn_cards,
        )
        positions = _positions(live_seats, button)
        public_history: list[PublicAction] = []
        while (actor_seat := adapter.actor_seat()) is not None:
            obs = _make_observation(
                adapter,
                public_history,
                actor_seat,
                button,
                anon_ids,
                live_seats,
                positions,
                hole_cards,
                hand_number,
                rules,
                small_blind,
                big_blind,
                (hand_number - 1) // rules.blind_level_hands,
            )
            outcome = call_bot(bots[actor_seat], obs, timing)
            applied = outcome.applied
            try:
                event = adapter.apply(applied)
            except (ValueError, RuntimeError):
                # Engine rejection is treated identically to an invalid bot action.
                applied = Action(ActionKind.CHECK if obs.legal.can_check else ActionKind.FOLD)
                event = adapter.apply(applied)
                outcome = outcome.__class__(
                    outcome.requested,
                    applied,
                    outcome.latency_ns,
                    "invalid_action",
                    "engine rejected requested action",
                )
            public_history.append(
                PublicAction(
                    seat_anon=anon_ids[event.seat],
                    street=event.street,
                    kind=event.kind,
                    amount=event.amount,
                )
            )
            detailed_log = log_level == "full" or (
                log_level == "hero" and actor_seat == hero_seat
            )
            decision = {
                "seed": seed,
                "hand_number": hand_number,
                "seat": actor_seat,
                "bot_label": bot_labels[actor_seat],
                "street": obs.street,
                "position": positions.get(actor_seat, "OUT"),
                "stack_bucket": _stack_bucket(obs.seats[actor_seat].stack, big_blind),
                "lineup": lineup,
                "applied_action": _action_dict(applied),
                "latency_ns": outcome.latency_ns,
                "failure_type": outcome.failure_type,
            }
            if detailed_log:
                observation = asdict(obs)
                event_data = _event_dict(event)
                decision.update(
                    observation=observation,
                    legal_actions=observation["legal"],
                    requested_action=_action_dict(outcome.requested),
                    exception_text=outcome.exception_text,
                    diagnostics=(
                        outcome.requested.diag
                        if outcome.requested and outcome.requested.diag is not None
                        else bots[actor_seat].last_diagnostics
                    ),
                    event=event_data,
                )
            decision_records.append(decision)
        hand_result = adapter.hand_result()
        if sum(hand_result.ending_stacks) != total_chips:
            raise AssertionError("chip conservation violated by engine result")
        stacks = list(hand_result.ending_stacks)
        busted = tuple(
            seat for seat in live_seats if starting[seat] > 0 and stacks[seat] == 0
        )
        score_updates: dict[int, float] = {}
        if busted:
            awards = award_simultaneous_busts(
                busted, starting, rules.payouts, len(live_seats)
            )
            for seat, value in awards.items():
                points[seat] = value
                score_updates[seat] = value
                tied_busts = sum(
                    starting[busted_seat] == starting[seat] for busted_seat in busted
                )
                lower_stacks = sum(
                    starting[busted_seat] < starting[seat] for busted_seat in busted
                )
                bust_finish[seat] = len(live_seats) - lower_stacks - tied_busts + 1
            live.difference_update(busted)
            if len(live) == 1:
                winner = next(iter(live))
                points[winner] = rules.payouts[0]
                score_updates[winner] = points[winner]
        stats_hand_records.append({
            "hand_number": hand_number,
            "actions": [_event_dict(event) for event in hand_result.actions],
            "starting_stacks": starting,
            "board": hand_result.board,
            "showdown_ranks": hand_result.showdown_ranks,
            "pots": [{"winners": pot.winners} for pot in hand_result.pots],
        })
        hand_summaries.append({
            "hand_number": hand_number,
            "button": button,
            "hero_delta": hand_result.chip_deltas[hero_seat],
            "hero_position": positions.get(hero_seat, "OUT"),
            "hero_stack_bb": starting[hero_seat] / big_blind if big_blind else None,
            "showdown": bool(hand_result.showdown_ranks),
            "eliminated_count": len(busted),
        })
        if log_level != "summary":
            private_hand = {
                "seed": seed,
                "hand_number": hand_number,
                "hero_seat": hero_seat,
                "button": button,
                "blinds": [small_blind, big_blind],
                "starting_stacks": starting,
                "ending_stacks": hand_result.ending_stacks,
                "hole_cards": hole_cards,
                "board": hand_result.board,
                "actions": [_event_dict(event) for event in hand_result.actions],
                "pots": [
                    {
                        "amount": pot.amount,
                        "eligible_seats": pot.eligible_seats,
                        "winners": pot.winners,
                    }
                    for pot in hand_result.pots
                ],
                "refunds": hand_result.refunds,
                "showdown": hand_result.showdown_cards,
                "showdown_ranks": hand_result.showdown_ranks,
                "chip_deltas": hand_result.chip_deltas,
                "eliminations": busted,
                "scoring_updates": score_updates,
                "showdown_seats": tuple(seat for seat, _ in hand_result.showdown_cards),
            }
            private_hand["hand_content_sha256"] = _hand_hash([private_hand])
            hand_records.append(private_hand)
        public_result = PublicHandResult(
            hand_number=hand_number,
            chip_changes=tuple(enumerate(hand_result.chip_deltas)),
            showdown_cards=hand_result.showdown_cards,
            board=hand_result.board,
        )
        for bot in bots:
            bot.on_hand_end(public_result)
        if len(live) > 1:
            button = next(
                (button + offset) % rules.players
                for offset in range(1, rules.players + 1)
                if (button + offset) % rules.players in live
            )
    if len(live) > 1:
        survivor_seats = tuple(sorted(live))
        survivor_scores = icm_values(
            tuple(stacks[seat] for seat in survivor_seats),
            rules.payouts[: len(survivor_seats)],
        )
        for seat, value in zip(survivor_seats, survivor_scores):
            points[seat] = value
    hero_busted = stacks[hero_seat] == 0
    hero_finish = bust_finish.get(hero_seat)
    if not hero_busted:
        hero_finish = 1 + sum(
            stacks[seat] > stacks[hero_seat] for seat in live if seat != hero_seat
        )
    stats = _stats(decision_records, stats_hand_records)
    return SNGResult(
        seed=seed,
        hero_seat=hero_seat,
        hero_finish=hero_finish,
        hero_points=points[hero_seat],
        hero_final_stack=stacks[hero_seat],
        hero_busted=hero_busted,
        hands_played=hand_number,
        failed=False,
        points_by_seat=tuple(points),
        final_stacks=tuple(stacks),
        hand_records=tuple(hand_records),
        hand_summaries=tuple(hand_summaries),
        decision_records=tuple(decision_records),
        stats=stats,
        elapsed_ns=time.perf_counter_ns() - started,
    )


def serialize_observation(observation: Observation) -> str:
    return json.dumps(asdict(observation), sort_keys=True)
