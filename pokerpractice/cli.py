from __future__ import annotations

import argparse
import math
import os
import sqlite3
import statistics
import tempfile
from dataclasses import asdict
from pathlib import Path

from pokerpractice.batch import run_batch
from pokerpractice.bots.roster import OpponentSpec
from pokerpractice.config import load_configs
from pokerpractice.logging_store import create_run_dir, write_direct_result, write_manifest
from pokerpractice.tournament import run_sng


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--seed-set", choices=("tuning", "eval"), default="tuning")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--label")
    parser.add_argument("--log-level", choices=("full", "hero", "summary"))
    parser.add_argument("--config-dir", type=Path, default=_root() / "configs")
    parser.add_argument("--hero", type=Path, default=_root() / "bots" / "my_bot.py")


def _seed_base(seed_set: str) -> int:
    return 1_000_000 if seed_set == "tuning" else 9_000_000


def _specs(rows: list[dict]) -> tuple[OpponentSpec, ...]:
    return tuple(
        OpponentSpec(
            style=str(row["style"]),
            params=dict(row.get("params", {})),
            strength=str(row.get("strength", "normal")),
        )
        for row in rows
    )


def _manifest(
    run_dir, rules, timing, logging_config, roster, seeds, label, root, hero_path
) -> None:
    write_manifest(
        run_dir,
        asdict(rules),
        asdict(timing),
        logging_config,
        [{"style": spec.style, "params": spec.params, "strength": spec.strength} for spec in roster],
        seeds,
        label,
        root,
        hero_path,
    )


def _print_results(rows: list[dict], elapsed: float, run_dir: Path) -> None:
    completed = [row for row in rows if not row.get("failed", False)]
    hands = sum(row.get("hands_played", 0) for row in completed)
    print(f"Run directory: {run_dir}")
    print(f"SNGs: {len(completed)} completed, {len(rows) - len(completed)} failed")
    print(f"Throughput: {len(completed) / elapsed:.2f} SNGs/s; {hands / elapsed:.2f} hands/s")
    if completed:
        points = [row["hero_points"] for row in completed]
        mean = statistics.mean(points)
        se = statistics.stdev(points) / len(points) ** 0.5 if len(points) > 1 else 0.0
        print(f"Hero mean points: {mean:.3f} ± {se:.3f} SE (n={len(completed)})")
        print(f"Hero mean hands played: {statistics.mean(row['hands_played'] for row in completed):.1f}")
    print(
        "Stats definitions: VPIP = voluntary preflop call/raise per hand with a "
        "preflop decision (walks excluded); PFR = preflop raise per same opportunity; "
        "AF = postflop bets+raises/calls; AFq = postflop bets+raises over all "
        "postflop decisions; WTSD = showdowns/saw-flop hands for players not folded preflop; "
        "W$SD = hands winning any chips at showdown/showdowns. Positions are BTN, SB, BB, "
        "UTG.., CO by live-seat count (heads-up: BTN/SB, BB); stack BB buckets are "
        "<10, 10-20, 20-40, 40-100, 100+. Counts are decision opportunities."
    )
    aggregates: dict[str, dict] = {}
    latency_by_label: dict[str, list[int]] = {}
    try:
        with sqlite3.connect(run_dir / "results.sqlite") as connection:
            for label, latency in connection.execute(
                "SELECT bot_label, latency_ns FROM decisions"
            ):
                latency_by_label.setdefault(label, []).append(latency)
    except sqlite3.Error:
        pass
    for row in completed:
        for label, stat in row.get("stats", {}).items():
            aggregate = aggregates.setdefault(
                label,
                {
                    "vpip_count": 0, "vpip_opportunities": 0,
                    "pfr_count": 0, "pfr_opportunities": 0,
                    "af_numerator": 0, "af_denominator": 0,
                    "afq_numerator": 0, "afq_denominator": 0,
                    "wtsd_count": 0, "wtsd_opportunities": 0,
                    "wusd_count": 0, "wusd_opportunities": 0,
                    "latencies_ns": [], "failure_counts": {},
                    "position_decisions": {}, "stack_bucket_decisions": {},
                },
            )
            for key in (
                "vpip_count", "vpip_opportunities", "pfr_count", "pfr_opportunities",
                "af_numerator", "af_denominator", "afq_numerator", "afq_denominator",
                "wtsd_count", "wtsd_opportunities", "wusd_count", "wusd_opportunities",
            ):
                aggregate[key] += stat.get(key, 0)
            for key in ("position_decisions", "stack_bucket_decisions"):
                for name, count in stat.get(key, {}).items():
                    aggregate[key][name] = aggregate[key].get(name, 0) + count
            for failure, count in stat.get("failure_counts", {}).items():
                aggregate["failure_counts"][failure] = (
                    aggregate["failure_counts"].get(failure, 0) + count
                )
    for label, aggregate in aggregates.items():
        aggregate["latencies_ns"] = latency_by_label.get(label, [])
    for label, stat in sorted(aggregates.items()):
        latencies = sorted(stat["latencies_ns"])

        def percentile(q):
            if not latencies:
                return 0.0
            return latencies[max(0, math.ceil(q * len(latencies)) - 1)] / 1000

        vpip = (
            f"{stat['vpip_count'] / stat['vpip_opportunities']:.3f}"
            if stat["vpip_opportunities"] else "n/a"
        )
        pfr = (
            f"{stat['pfr_count'] / stat['pfr_opportunities']:.3f}"
            if stat["pfr_opportunities"] else "n/a"
        )
        af = (
            f"{stat['af_numerator'] / stat['af_denominator']:.3f}"
            if stat["af_denominator"] else "∞" if stat["af_numerator"] else "0.000"
        )
        afq = (
            stat["afq_numerator"] / stat["afq_denominator"]
            if stat["afq_denominator"] else 0.0
        )
        wtsd = (
            f"{stat['wtsd_count'] / stat['wtsd_opportunities']:.3f}"
            if stat["wtsd_opportunities"] else "n/a"
        )
        wusd = (
            f"{stat['wusd_count'] / stat['wusd_opportunities']:.3f}"
            if stat["wusd_opportunities"] else "n/a"
        )
        failure_text = ", ".join(
            f"{key}={stat['failure_counts'].get(key, 0)}"
            for key in ("exception", "invalid_action", "timeout_soft", "timeout_hard")
        )
        print(
            f"{label}: VPIP {vpip} ({stat['vpip_opportunities']} opp), "
            f"PFR {pfr} ({stat['pfr_opportunities']} opp), "
            f"AF {af} ({stat['af_denominator']} calls), "
            f"AFq {afq:.3f} ({stat['afq_denominator']} decisions), "
            f"WTSD {wtsd} ({stat['wtsd_opportunities']} saw flop), "
            f"W$SD {wusd} ({stat['wusd_opportunities']} showdowns); "
            f"latency µs med/p95/p99/max="
            f"{statistics.median(latencies) / 1000 if latencies else 0:.1f}/"
            f"{percentile(.95):.1f}/{percentile(.99):.1f}/"
            f"{latencies[-1] / 1000 if latencies else 0:.1f}; failures {failure_text}"
        )
        position_counts = stat["position_decisions"]
        position_order = ["BTN", "BTN/SB", "SB", "BB", "UTG", "MP1", "MP2", "MP3", "CO"]
        position_text = ", ".join(
            f"{position}={position_counts[position]}"
            for position in position_order
            if position in position_counts
        )
        bucket_counts = stat["stack_bucket_decisions"]
        bucket_text = ", ".join(
            f"{bucket}={bucket_counts[bucket]}"
            for bucket in ("<10", "10-20", "20-40", "40-100", "100+")
            if bucket in bucket_counts
        )
        print(f"  Position decisions: {position_text}; stack BB decisions: {bucket_text}")


def _bench(args, rules, timing, logging_config, roster, seeds) -> int:
    runs_root = _root() / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    for workers in sorted({1, 8}):
        with tempfile.TemporaryDirectory(prefix=f"bench-{workers}-", dir=runs_root) as path:
            run_dir = Path(path)
            _manifest(
                run_dir, rules, timing, logging_config, roster, seeds,
                f"bench-{workers}-workers", _root(), args.hero,
            )
            rows, elapsed = run_batch(
                seeds, workers, rules, timing, roster, run_dir,
                "summary", hero_path=args.hero,
            )
            completed = [row for row in rows if not row.get("failed")]
            hands = sum(row.get("hands_played", 0) for row in completed)
            sng_rate = len(completed) / max(elapsed, 1e-9)
            hand_rate = hands / max(elapsed, 1e-9)
            estimate = 10_000 / max(sng_rate, 1e-9)
            print(
                f"{workers} worker(s): {len(completed)}/{len(rows)} SNGs, "
                f"{hands} hands in {elapsed:.2f}s; {sng_rate:.2f} SNG/s, "
                f"{hand_rate:.2f} hands/s; estimated 10,000 SNGs: {estimate / 60:.1f} min"
            )
    print("Reference: phase-1 raw PokerKit achieved approximately 300 hands/s.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="poker-practice")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="run one sit-and-go")
    _add_common(run_parser)
    run_parser.add_argument("--inline", action="store_true", help="disable the process watchdog")
    batch_parser = commands.add_parser("batch", help="run a batch of sit-and-gos")
    _add_common(batch_parser)
    batch_parser.add_argument("-n", type=int, default=100)
    batch_parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    profile_parser = commands.add_parser("profile", help="profile opponent styles")
    _add_common(profile_parser)
    profile_parser.add_argument("-n", type=int, default=200)
    profile_parser.add_argument("--workers", type=int, default=1)
    bench_parser = commands.add_parser("bench", help="benchmark worker throughput")
    _add_common(bench_parser)
    bench_parser.add_argument("-n", type=int, default=100)
    bench_parser.add_argument("--workers", type=int, default=8)
    report_parser = commands.add_parser("report", help="report saved run metrics")
    report_parser.add_argument("run_dir", type=Path)
    hands_parser = commands.add_parser("hands", help="browse saved hands")
    hands_parser.add_argument("run_dir", type=Path)
    hands_parser.add_argument("--biggest-wins", type=int)
    hands_parser.add_argument("--biggest-losses", type=int)
    decisions_parser = commands.add_parser("decisions", help="filter saved decisions")
    decisions_parser.add_argument("run_dir", type=Path)
    decisions_parser.add_argument("--street")
    decisions_parser.add_argument("--position")
    decisions_parser.add_argument("--stack-bucket")
    decisions_parser.add_argument("--opponent-style")
    decisions_parser.add_argument("--failure")
    replay_parser = commands.add_parser("replay", help="replay a saved hand")
    replay_parser.add_argument("run_dir", type=Path)
    replay_parser.add_argument("--sng", type=int, required=True)
    replay_parser.add_argument("--hand", type=int, required=True)
    replay_parser.add_argument("--view", choices=("hero", "omniscient"), default="hero")
    replay_parser.add_argument("--equity", action="store_true")
    compare_parser = commands.add_parser("compare", help="compare two saved runs")
    compare_parser.add_argument("run_a", type=Path)
    compare_parser.add_argument("run_b", type=Path)
    compare_parser.add_argument("--bootstrap-seed", type=int, default=20260217)
    aggregate_parser = commands.add_parser("aggregate6", help="aggregate six saved runs")
    aggregate_parser.add_argument("run_dirs", nargs=6, type=Path)
    args = parser.parse_args(argv)
    if args.command in {"hands", "decisions", "report", "replay", "compare", "aggregate6"}:
        from pokerpractice import analysis

        if args.command == "report":
            analysis.report(args.run_dir)
        elif args.command == "hands":
            losses = args.biggest_losses is not None
            count = args.biggest_losses if losses else args.biggest_wins or 20
            analysis.biggest_hands(args.run_dir, count, losses)
        elif args.command == "decisions":
            analysis.filtered_decisions(
                args.run_dir, street=args.street, position=args.position,
                stack_bucket=args.stack_bucket, opponent_style=args.opponent_style,
                failure=args.failure,
            )
        elif args.command == "replay":
            analysis.replay_hand(
                args.run_dir, args.sng, args.hand,
                omniscient=args.view == "omniscient", show_equity=args.equity,
            )
        elif args.command == "compare":
            analysis.compare_runs(args.run_a, args.run_b, args.bootstrap_seed)
        else:
            analysis.aggregate_six(args.run_dirs)
        return 0
    if args.command in {"batch", "profile", "bench"} and args.n < 1:
        parser.error("-n must be at least 1")
    rules, timing, logging_config, roster_rows = load_configs(args.config_dir)
    roster = _specs(roster_rows)
    seed = args.seed if args.seed is not None else _seed_base(args.seed_set)
    seeds = [seed] if args.command == "run" else [seed + index for index in range(args.n)]
    if args.command == "bench":
        return _bench(args, rules, timing, logging_config, roster, seeds)
    log_level = args.log_level or (
        "summary"
        if args.command == "profile"
        else logging_config.get("default_batch_level", "hero")
        if args.command == "batch" and len(seeds) >= logging_config.get("default_batch_threshold", 1000)
        else logging_config.get("default_level", "full")
    )
    run_dir = create_run_dir(_root() / "runs")
    _manifest(
        run_dir,
        rules,
        timing,
        logging_config,
        roster,
        seeds,
        args.label,
        _root(),
        args.hero,
    )
    if args.command == "run" and args.inline:
        result = run_sng(
            seed, rules, timing, roster, hero_path=args.hero, log_level=log_level
        )
        write_direct_result(run_dir, result, log_level)
        rows = [{
            "failed": result.failed,
            "hands_played": result.hands_played,
            "hero_points": result.hero_points,
            "hero_finish": result.hero_finish,
            "hero_final_stack": result.hero_final_stack,
            "stats": result.stats,
        }]
        elapsed = max(result.elapsed_ns / 1_000_000_000, 1e-9)
    else:
        workers = 1 if args.command == "run" else args.workers
        rows, elapsed = run_batch(
            seeds,
            workers,
            rules,
            timing,
            roster,
            run_dir,
            log_level,
            hero_path=args.hero,
        )
    if args.command == "profile":
        print("Opponent profile (decision timing measured in competition mode):")
    _print_results(rows, elapsed, run_dir)
    if args.command == "run" and rows and not rows[0].get("failed", False):
        print(
            "Hero: "
            f"finish={rows[0].get('hero_finish', 'see results.sqlite')}, "
            f"points={rows[0].get('hero_points', 0):.3f}, "
            f"stack={rows[0].get('hero_final_stack', 0)}"
        )
    return 1 if any(row.get("failed", False) for row in rows) else 0
