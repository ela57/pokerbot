from __future__ import annotations

import gzip
import json
import math
import random
import sqlite3
import statistics
from pathlib import Path

import eval7


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def _sng_rows(run_dir: Path) -> list[tuple]:
    with sqlite3.connect(run_dir / "results.sqlite") as connection:
        return connection.execute(
            "SELECT seed, hero_finish, points, final_stack, busted, hands_played, failed "
            "FROM sngs ORDER BY seed"
        ).fetchall()


def _shard_payloads(run_dir: Path):
    for path in sorted((run_dir / "shards").glob("worker-*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                yield json.loads(line)


def _stats_payloads(run_dir: Path) -> list[dict]:
    with sqlite3.connect(run_dir / "results.sqlite") as connection:
        try:
            rows = connection.execute(
                "SELECT stats_json FROM sngs WHERE failed=0 AND stats_json IS NOT NULL"
            ).fetchall()
        except sqlite3.OperationalError:
            return [
                payload["summary"].get("stats", {})
                for payload in _shard_payloads(run_dir)
                if not payload["summary"].get("failed")
            ]
    return [json.loads(row[0]) for row in rows]


def _run_log_level(run_dir: Path) -> str:
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        return manifest.get("config", {}).get("logging", {}).get("log_level", "full")
    except (OSError, json.JSONDecodeError):
        return "full"


def _mean_se(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    return statistics.mean(values), (
        statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else 0.0
    )


def report(run_dir: Path) -> None:
    rows = [row for row in _sng_rows(run_dir) if not row[6]]
    points = [float(row[2]) for row in rows]
    mean, se = _mean_se(points)
    print(f"SNGs: {len(rows)} completed, {len(_sng_rows(run_dir)) - len(rows)} failed")
    print(f"Mean points: {mean:.3f} ± {se:.3f} SE; 95% normal CI [{mean - 1.96 * se:.3f}, {mean + 1.96 * se:.3f}]")
    if not rows:
        return
    finishes = {}
    for row in rows:
        finishes[row[1]] = finishes.get(row[1], 0) + 1
    print("Finish distribution: " + ", ".join(
        f"{place}={count} ({count / len(rows):.1%})"
        for place, count in sorted(finishes.items(), key=lambda item: (item[0] is None, item[0] or 0))
    ))
    firsts = sum(row[1] == 1 for row in rows)
    busts = sum(bool(row[4]) for row in rows)
    print(f"First-place rate: {firsts}/{len(rows)} = {firsts / len(rows):.3%}")
    print(f"Bust rate: {busts}/{len(rows)} = {busts / len(rows):.3%}")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    hand_cap = manifest["config"]["rules"].get("hand_cap", 200)
    busted_stacks = [row[3] for row in rows if row[4]]
    cap_stacks = [row[3] for row in rows if not row[4] and row[5] >= hand_cap]
    winners = [row[3] for row in rows if not row[4] and row[5] < hand_cap]
    for label, values in (
        ("busted", busted_stacks), ("survived at hand cap", cap_stacks),
        ("won before cap", winners),
    ):
        if values:
            print(
                f"Final stacks ({label}; n={len(values)}): "
                f"min={min(values)}, median={statistics.median(values):.1f}, max={max(values)}"
            )
        else:
            print(f"Final stacks ({label}; n=0)")

    totals = {}
    for stats_payload in _stats_payloads(run_dir):
        for label, stat in stats_payload.items():
            acc = totals.setdefault(label, {
                "vpip": 0, "vpip_opp": 0, "pfr": 0, "pfr_opp": 0,
                "af_num": 0, "af_den": 0, "afq_num": 0, "afq_den": 0,
                "wtsd": 0, "wtsd_opp": 0, "wusd": 0, "wusd_opp": 0,
                "failures": 0, "failure_counts": {}, "positions": {}, "depths": {},
                "groups": {"by_position": {}, "by_stack_bucket": {}, "by_lineup": {}},
            })
            for target, source in (
                ("vpip", "vpip_count"), ("vpip_opp", "vpip_opportunities"),
                ("pfr", "pfr_count"), ("pfr_opp", "pfr_opportunities"),
                ("af_num", "af_numerator"), ("af_den", "af_denominator"),
                ("afq_num", "afq_numerator"), ("afq_den", "afq_denominator"),
                ("wtsd", "wtsd_count"), ("wtsd_opp", "wtsd_opportunities"),
                ("wusd", "wusd_count"), ("wusd_opp", "wusd_opportunities"),
            ):
                acc[target] += stat.get(source, 0)
            acc["failures"] += stat.get("failure_count", 0)
            for failure, count in stat.get("failure_counts", {}).items():
                acc["failure_counts"][failure] = (
                    acc["failure_counts"].get(failure, 0) + count
                )
            for source, dest in (("position_decisions", "positions"), ("stack_bucket_decisions", "depths")):
                for key, count in stat.get(source, {}).items():
                    acc[dest][key] = acc[dest].get(key, 0) + count
            for category in acc["groups"]:
                for key, group in stat.get(category, {}).items():
                    group_acc = acc["groups"][category].setdefault(
                        key,
                        {
                            "vpip_count": 0, "vpip_opportunities": 0,
                            "pfr_count": 0, "pfr_opportunities": 0,
                            "af_numerator": 0, "af_denominator": 0,
                            "afq_numerator": 0, "afq_denominator": 0,
                            "wtsd_count": 0, "wtsd_opportunities": 0,
                            "wusd_count": 0, "wusd_opportunities": 0,
                            "failure_count": 0,
                        },
                    )
                    for metric in group_acc:
                        group_acc[metric] += group.get(metric, 0)
    log_level = _run_log_level(run_dir)
    if log_level == "summary" or not totals:
        print(
            "Decision-level bot metrics and replay require --log-level hero or full; "
            "all-style decision latency and behavior reports require full."
        )
    elif log_level == "hero":
        print(
            "Only hero decisions are indexed/logged; opponent decision latency "
            "distributions require --log-level full."
        )
    print(
        "Definitions: VPIP=voluntary preflop call/raise per hand with a preflop decision; "
        "PFR=preflop raise per same opportunity; AF=(postflop bets+raises)/calls; "
        "AFq=(bets+raises)/all postflop decisions; WTSD=showdowns/saw-flop hands "
        "(not folded preflop); W$SD=hands winning chips at showdown/showdowns. "
        "Counts below are opportunities."
    )
    latency_by_label = {}
    failure_by_label = {}
    with sqlite3.connect(run_dir / "results.sqlite") as connection:
        for label, latency, failure_type in connection.execute(
            "SELECT bot_label, latency_ns, failure_type FROM decisions"
        ):
            latency_by_label.setdefault(label, []).append(latency)
            if failure_type:
                counts = failure_by_label.setdefault(label, {})
                counts[failure_type] = counts.get(failure_type, 0) + 1
    for label, stat in sorted(totals.items()):
        af = stat["af_num"] / stat["af_den"] if stat["af_den"] else 0.0
        afq = stat["afq_num"] / stat["afq_den"] if stat["afq_den"] else 0.0
        latencies = sorted(latency_by_label.get(label, ()))
        percentile = lambda q: (
            latencies[max(0, math.ceil(q * len(latencies)) - 1)] / 1000
            if latencies else 0.0
        )
        latency_text = (
            f"{statistics.median(latencies) / 1000 if latencies else 0:.1f}/"
            f"{percentile(.95):.1f}/{percentile(.99):.1f}/"
            f"{latencies[-1] / 1000 if latencies else 0:.1f}"
            if latencies
            else "not indexed; use --log-level full"
        )
        failures = failure_by_label.get(label, {}) or stat["failure_counts"]
        print(
            f"{label}: VPIP={stat['vpip']}/{stat['vpip_opp']} "
            f"({stat['vpip'] / stat['vpip_opp'] if stat['vpip_opp'] else 0:.3f}), "
            f"PFR={stat['pfr']}/{stat['pfr_opp']} "
            f"({stat['pfr'] / stat['pfr_opp'] if stat['pfr_opp'] else 0:.3f}), "
            f"AF={af:.3f} ({stat['af_den']} calls), AFq={afq:.3f} "
            f"({stat['afq_den']} decisions), WTSD={stat['wtsd']}/{stat['wtsd_opp']}, "
            f"W$SD={stat['wusd']}/{stat['wusd_opp']}, failures={failures}, "
            f"latency µs med/p95/p99/max={latency_text}"
        )
        print(f"  Decisions by position: {stat['positions']}; stack depth BB: {stat['depths']}")
        for category, title in (
            ("by_position", "position"),
            ("by_stack_bucket", "stack BB"),
            ("by_lineup", "opponent lineup"),
        ):
            values = []
            for group_key, group in sorted(stat["groups"][category].items()):
                vpip_rate = (
                    group["vpip_count"] / group["vpip_opportunities"]
                    if group["vpip_opportunities"] else 0
                )
                pfr_rate = (
                    group["pfr_count"] / group["pfr_opportunities"]
                    if group["pfr_opportunities"] else 0
                )
                af = (
                    group["af_numerator"] / group["af_denominator"]
                    if group["af_denominator"] else 0
                )
                afq = (
                    group["afq_numerator"] / group["afq_denominator"]
                    if group["afq_denominator"] else 0
                )
                wtsd = (
                    group["wtsd_count"] / group["wtsd_opportunities"]
                    if group["wtsd_opportunities"] else 0
                )
                wusd = (
                    group["wusd_count"] / group["wusd_opportunities"]
                    if group["wusd_opportunities"] else 0
                )
                values.append(
                    f"{group_key}: VPIP={vpip_rate:.3f} "
                    f"({group['vpip_opportunities']} opp), PFR={pfr_rate:.3f} "
                    f"({group['pfr_opportunities']} opp), "
                    f"AF={af:.3f} ({group['af_denominator']} calls), "
                    f"AFq={afq:.3f} ({group['afq_denominator']} decisions), "
                    f"WTSD={wtsd:.3f} ({group['wtsd_opportunities']} saw flop), "
                    f"W$SD={wusd:.3f} ({group['wusd_opportunities']} showdowns), "
                    f"failures={group['failure_count']}"
                )
            print(f"  By {title}: " + ("; ".join(values) if values else "n/a"))
    try:
        with sqlite3.connect(run_dir / "results.sqlite") as connection:
            lineups = connection.execute(
                "SELECT opponent_style_lineup, COUNT(DISTINCT seed), AVG(s.points) "
                "FROM decisions d JOIN sngs s USING(seed) "
                "WHERE d.bot_label='hero' AND s.failed=0 "
                "GROUP BY opponent_style_lineup"
            ).fetchall()
    except sqlite3.OperationalError:
        lineups = []
    for lineup, count, avg_points in lineups:
        print(f"Hero by opponent lineup {lineup}: n={count}, mean points={avg_points:.3f}")


def biggest_hands(run_dir: Path, count: int, losses: bool) -> None:
    order = "ASC" if losses else "DESC"
    with sqlite3.connect(run_dir / "results.sqlite") as connection:
        rows = connection.execute(
            f"SELECT seed, hand_number, hero_delta, hero_position, hero_stack_bb "
            f"FROM hand_summaries ORDER BY hero_delta {order} LIMIT ?",
            (count,),
        ).fetchall()
    title = "biggest losses" if losses else "biggest wins"
    print(f"Hero {title}:")
    for seed, hand_number, delta, position, depth in rows:
        print(f"seed={seed} hand={hand_number} delta={delta:+d} position={position} start_bb={depth:.1f}")


def filtered_decisions(run_dir: Path, *, street=None, position=None, stack_bucket=None, opponent_style=None, failure=None):
    query = (
        "SELECT seed, hand_number, seat, bot_label, street, position, stack_bucket, "
        "opponent_style_lineup, failure_type, latency_ns FROM decisions WHERE 1=1"
    )
    params = []
    for column, value in (
        ("street", street), ("position", position), ("stack_bucket", stack_bucket),
        ("failure_type", failure),
    ):
        if value:
            query += f" AND {column}=?"
            params.append(value)
    if opponent_style:
        query += " AND opponent_style_lineup LIKE ?"
        params.append(f"%{opponent_style}%")
    query += " ORDER BY seed, hand_number, seat LIMIT 500"
    with sqlite3.connect(run_dir / "results.sqlite") as connection:
        rows = connection.execute(query, params).fetchall()
    print("seed hand seat bot street position stack_bb_bucket lineup failure latency_us")
    for row in rows:
        print(*row[:7], row[7], row[8], f"{row[9] / 1000:.1f}")


def compare_runs(run_a: Path, run_b: Path, seed: int = 20260217) -> None:
    a = {row[0]: float(row[2]) for row in _sng_rows(run_a) if not row[6]}
    b = {row[0]: float(row[2]) for row in _sng_rows(run_b) if not row[6]}
    shared = sorted(a.keys() & b.keys())
    differences = [b[key] - a[key] for key in shared]
    if differences:
        mean, se = _mean_se(differences)
        rng = random.Random(seed)
        boot = sorted(
            statistics.mean(rng.choices(differences, k=len(differences)))
            for _ in range(2000)
        )
        print(
            f"Paired shared seeds: n={len(shared)}, mean B-A={mean:.3f}, "
            f"SE={se:.3f}, 95% normal CI [{mean - 1.96 * se:.3f}, {mean + 1.96 * se:.3f}], "
            f"bootstrap 95% CI [{boot[49]:.3f}, {boot[1949]:.3f}]"
        )
    only_a = [value for key, value in a.items() if key not in b]
    only_b = [value for key, value in b.items() if key not in a]
    mean_a, se_a = _mean_se(only_a or list(a.values()))
    mean_b, se_b = _mean_se(only_b or list(b.values()))
    diff = mean_b - mean_a
    unpaired_se = math.sqrt(se_a * se_a + se_b * se_b)
    print(
        f"Unpaired reference: A n={len(only_a) or len(a)} mean={mean_a:.3f}; "
        f"B n={len(only_b) or len(b)} mean={mean_b:.3f}; B-A={diff:.3f} "
        f"± {1.96 * unpaired_se:.3f} 95% normal CI"
    )
    print("Caveat: shared seeds preserve initial deals and seats, but game states diverge after the first differing decision.")
    levels = {_run_log_level(run_a), _run_log_level(run_b)}
    if "summary" in levels:
        print(
            "Outcome comparison is available from summary logs; decision review "
            "requires hero/full logs, and all-style behavior/latency metrics require full."
        )
    elif "hero" in levels:
        print(
            "Outcome comparison is available from hero logs; opponent behavior and "
            "latency metrics require full logs."
        )


def aggregate_six(run_dirs: list[Path]) -> None:
    if len(run_dirs) != 6:
        raise ValueError("aggregate6 requires exactly six run directories")
    means = []
    for run_dir in run_dirs:
        rows = [row for row in _sng_rows(run_dir) if not row[6]]
        means.append(statistics.mean(float(row[2]) for row in rows) if rows else 0.0)
    raw = sum((index + 1) * value for index, value in enumerate(means))
    print(f"Round means: {means}")
    print(f"Raw weighted sum: {raw:.3f}")
    print(f"Normalized weighted mean: {raw / 21:.3f}")
    print("Normalization follows weights 1..6; the official formula's normalization remains unknown.")


def _cards_equity(
    hero: list[str],
    board: list[str],
    opponents: list[list[str]] | None,
    samples: int,
    seed: int,
    opponent_count: int = 1,
) -> float:
    rng = random.Random(seed)
    deck = [rank + suit for rank in "23456789TJQKA" for suit in "shdc"]
    known = set(hero + board + [card for hand in (opponents or []) for card in hand])
    if opponents is None:
        opponent_count = max(1, opponent_count)
    else:
        opponent_count = len(opponents)
    if not opponent_count:
        return 1.0
    wins = 0.0
    for _ in range(samples):
        used = set(known)
        sampled_opponents = []
        if opponents is None:
            for _opponent in range(opponent_count):
                available = [card for card in deck if card not in used]
                hand = rng.sample(available, 2)
                used.update(hand)
                sampled_opponents.append(hand)
        else:
            sampled_opponents = opponents
        available = [card for card in deck if card not in used]
        runout = rng.sample(available, 5 - len(board))
        final_board = [eval7.Card(card) for card in board + runout]
        hero_rank = eval7.evaluate([eval7.Card(card) for card in hero] + final_board)
        other_ranks = [
            eval7.evaluate([eval7.Card(card) for card in hand] + final_board)
            for hand in sampled_opponents
        ]
        best = max([hero_rank, *other_ranks])
        if hero_rank == best:
            wins += 1 / (1 + sum(rank == best for rank in other_ranks))
    return wins / samples


def _hole_cards_for(hand: dict, seat: int) -> list[str]:
    cards = hand["hole_cards"]
    if isinstance(cards, dict):
        return cards.get(str(seat), cards.get(seat, []))
    return cards[seat]


def replay_hand(
    run_dir: Path,
    seed: int,
    hand_number: int,
    *,
    omniscient: bool = False,
    show_equity: bool = False,
) -> None:
    hands = [
        row for row in _jsonl(run_dir / "hands.jsonl.gz")
        if row["seed"] == seed and row["hand_number"] == hand_number
    ]
    if not hands:
        raise ValueError("hand not found; replay requires a full or hero log")
    hand = hands[0]
    hero_seat = hand["hero_seat"]
    decisions = [
        row for row in _jsonl(run_dir / "decisions.jsonl.gz")
        if row["seed"] == seed and row["hand_number"] == hand_number
    ]
    if not omniscient:
        decisions = [row for row in decisions if row["bot_label"] == "hero"]
    print(f"seed={seed} hand={hand_number} button={hand['button']} blinds={hand['blinds']}")
    print(f"Mode: {'OMNISCIENT' if omniscient else 'HERO VIEW'}")
    if omniscient:
        print(f"All hole cards: {hand['hole_cards']}")
    else:
        print(f"Your hole cards: {_hole_cards_for(hand, hero_seat)}")
    print("Public action sequence:")
    for event in hand["actions"]:
        print(
            f"  seat {event['seat']} {event['street']}: "
            f"{event['kind']} {event.get('amount', '')}".rstrip()
        )
    for step, decision in enumerate(decisions, 1):
        observation = decision.get("observation")
        if observation is None:
            continue
        print(f"\n--- Hero decision {step}: {observation['street']} ---")
        if omniscient:
            print("Omniscient state (includes private cards):")
            print(json.dumps({
                "observation": observation,
                "all_hole_cards": hand["hole_cards"],
                "applied_action": decision["applied_action"],
            }, indent=2, default=str))
        else:
            print("Exact observation given to the hero:")
            print(json.dumps(observation, indent=2, default=str))
            print(f"Applied action: {decision['applied_action']}")
        flags = []
        legal = observation["legal"]
        action = decision["applied_action"]
        action_kind = action["kind"].lower()
        if action_kind == "fold" and legal["can_check"]:
            flags.append("DEMONSTRABLE MISTAKE: folded when checking was free")
        if decision.get("failure_type"):
            flags.append(
                f"DEMONSTRABLE MISTAKE: fallback/invalid/timeout action ({decision['failure_type']})"
            )
        if omniscient and observation["street"] == "river" and action_kind in {"call", "raise"}:
            hero_view = observation["seats"][observation["my_seat"]]
            all_in = (
                legal["call_amount"] >= hero_view["stack"]
                if action_kind == "call"
                else action.get("amount", 0) >= hero_view["bet_this_street"] + hero_view["stack"]
            )
            if all_in:
                opponent_cards = [
                    _hole_cards_for(hand, seat)
                    for seat in range(len(observation["seats"]))
                    if seat != hero_seat and _hole_cards_for(hand, seat)
                ]
                if opponent_cards:
                    hero_rank = eval7.evaluate(
                        [eval7.Card(card) for card in observation["hole_cards"] + observation["board"]]
                    )
                    opponent_ranks = [
                        eval7.evaluate([eval7.Card(card) for card in cards + hand["board"]])
                        for cards in opponent_cards
                    ]
                    if hero_rank < max(opponent_ranks):
                        flags.append(
                            "DEMONSTRABLE MISTAKE (HINDSIGHT): river all-in call/raise had 0% "
                            "equity against shown hands"
                        )
        if flags:
            for flag in flags:
                print(flag)
        else:
            print("Outcome, not a demonstrable mistake under the review rules.")
        if show_equity:
            hero_cards = observation["hole_cards"]
            board = observation["board"]
            opponents = None
            opponent_count = sum(
                row["active"] for row in observation["seats"]
            ) - 1
            if omniscient:
                active = {
                    row["seat"] for row in observation["seats"] if row["active"]
                } - {hero_seat}
                opponents = [
                    _hole_cards_for(hand, seat) for seat in sorted(active)
                    if _hole_cards_for(hand, seat)
                ]
                opponent_count = len(opponents)
            label = (
                "omniscient equity vs actual opponent hands"
                if omniscient else "hero-view equity vs uniform random active-opponent hands"
            )
            equity = _cards_equity(
                hero_cards, board, opponents, 200, seed + step, opponent_count
            )
            print(f"Offline estimate only — {label}: {equity:.1%}")
    print(f"Board: {hand['board']}")
    print(f"Shown showdown cards: {hand['showdown']}")
    print("The hand result is an outcome; only the explicit flags above are demonstrable mistakes.")
