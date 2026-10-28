from __future__ import annotations

import multiprocessing as mp
import sqlite3
import time
from pathlib import Path

from pokerpractice.bots.roster import OpponentSpec
from pokerpractice.config import RulesConfig, TimingConfig
from pokerpractice.logging_store import merge_shards, write_worker_shard
from pokerpractice.tournament import SNGResult, run_sng


def _short_summary(result: SNGResult) -> dict:
    return {
        "seed": result.seed,
        "hero_seat": result.hero_seat,
        "hero_finish": result.hero_finish,
        "hero_points": result.hero_points,
        "hero_final_stack": result.hero_final_stack,
        "hero_busted": result.hero_busted,
        "hands_played": result.hands_played,
        "failed": result.failed,
        "points_by_seat": result.points_by_seat,
        "final_stacks": result.final_stacks,
        "stats": result.stats,
        "elapsed_ns": result.elapsed_ns,
    }


def _worker(
    result_queue,
    task_index: int,
    worker_id: int,
    seed: int,
    rules: RulesConfig,
    timing: TimingConfig,
    roster: tuple[OpponentSpec, ...],
    run_dir: str,
    log_level: str,
) -> None:
    try:
        result = run_sng(seed, rules, timing, roster)
        write_worker_shard(Path(run_dir), worker_id, result, log_level)
        result_queue.put({"task_index": task_index, "summary": _short_summary(result)})
    except BaseException as exc:
        result_queue.put(
            {
                "task_index": task_index,
                "worker_error": f"{type(exc).__name__}: {exc}",
            }
        )


def failed_result(seed: int) -> SNGResult:
    return SNGResult(
        seed=seed,
        hero_seat=-1,
        hero_finish=None,
        hero_points=0.0,
        hero_final_stack=0,
        hero_busted=False,
        hands_played=0,
        failed=True,
        points_by_seat=(),
        final_stacks=(),
        hand_records=(),
        decision_records=(),
        stats={},
        elapsed_ns=0,
    )


def run_batch(
    seeds: list[int],
    workers: int,
    rules: RulesConfig,
    timing: TimingConfig,
    roster: tuple[OpponentSpec, ...],
    run_dir: Path,
    log_level: str = "full",
) -> tuple[list[dict], float]:
    if workers < 1:
        raise ValueError("workers must be at least 1")
    context = mp.get_context("spawn")
    result_queue = context.Queue()
    pending = iter(enumerate(seeds))
    active: dict[int, tuple[mp.Process, int, int, float]] = {}
    results: dict[int, dict] = {}
    start = time.perf_counter()
    next_task = True

    def start_worker(slot: int) -> bool:
        nonlocal next_task
        try:
            task_index, seed = next(pending)
        except StopIteration:
            next_task = False
            return False
        process = context.Process(
            target=_worker,
            args=(
                result_queue, task_index, slot, seed, rules, timing, roster,
                str(run_dir), log_level,
            ),
        )
        process.start()
        active[slot] = (process, task_index, seed, time.monotonic())
        return True

    for slot in range(min(workers, len(seeds))):
        start_worker(slot)
    while active:
        now = time.monotonic()
        deadlines = [
            max(0.0, launched + timing.sng_wall_timeout_s - now)
            for _, _, _, launched in active.values()
        ]
        try:
            message = result_queue.get(timeout=min(0.05, min(deadlines, default=0.05)))
        except Exception:
            message = None
        if message is not None:
            index = message["task_index"]
            results[index] = message.get("summary", {"failed": True, "worker_error": message.get("worker_error")})
            for slot, (process, task_index, _, _) in tuple(active.items()):
                if task_index == index:
                    process.join(timeout=1)
                    if process.is_alive():
                        process.terminate()
                        process.join()
                    del active[slot]
                    if next_task:
                        start_worker(slot)
                    break
        now = time.monotonic()
        for slot, (process, index, seed, launched) in tuple(active.items()):
            if now - launched > timing.sng_wall_timeout_s and process.is_alive():
                process.terminate()
                process.join()
                results[index] = {"failed": True, "worker_error": "sng_wall_timeout"}
                del active[slot]
                if next_task:
                    start_worker(slot)
            elif not process.is_alive() and index not in results:
                process.join()
                # Worker died before reporting. Mark the run failed instead of blocking.
                results[index] = {"failed": True, "worker_error": f"worker_exit_{process.exitcode}"}
                del active[slot]
                if next_task:
                    start_worker(slot)
    result_queue.close()
    for index, seed in enumerate(seeds):
        results.setdefault(index, {"seed": seed, "failed": True, "worker_error": "missing_result"})
        results[index].setdefault("seed", seed)
    merge_shards(run_dir, log_level)
    with sqlite3.connect(run_dir / "results.sqlite") as connection:
        for index, row in results.items():
            if row.get("failed"):
                connection.execute(
                    "INSERT OR REPLACE INTO sngs VALUES (?, -1, NULL, 0, 0, 0, 0, 1)",
                    (row.get("seed", seeds[index]),),
                )
        connection.commit()
    elapsed = time.perf_counter() - start
    return [results[index] for index in range(len(seeds))], elapsed
