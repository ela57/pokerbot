from __future__ import annotations

import multiprocessing as mp
import queue
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
        "stats": {
            label: {
                key: value for key, value in stat.items()
                if key != "latencies_ns"
            }
            for label, stat in result.stats.items()
        },
        "elapsed_ns": result.elapsed_ns,
    }


def _worker(
    task_queue,
    result_queue,
    worker_id: int,
    generation: int,
    rules: RulesConfig,
    timing: TimingConfig,
    roster: tuple[OpponentSpec, ...],
    run_dir: str,
    log_level: str,
    hero_path: str,
) -> None:
    while True:
        task = task_queue.get()
        if task is None:
            return
        task_index, seed = task
        result_queue.put(
            {
                "event": "started",
                "worker_id": worker_id,
                "generation": generation,
                "task_index": task_index,
                "seed": seed,
            }
        )
        try:
            result = run_sng(
                seed, rules, timing, roster, hero_path=hero_path, log_level=log_level
            )
            write_worker_shard(
                Path(run_dir),
                worker_id,
                result,
                log_level,
                task_index=task_index,
                generation=generation,
            )
            message = {"summary": _short_summary(result)}
        except Exception as exc:
            message = {
                "summary": {
                    "seed": seed,
                    "failed": True,
                    "worker_error": f"{type(exc).__name__}: {exc}",
                }
            }
        result_queue.put(
            {
                "event": "finished",
                "worker_id": worker_id,
                "generation": generation,
                "task_index": task_index,
                **message,
            }
        )


def run_batch(
    seeds: list[int],
    workers: int,
    rules: RulesConfig,
    timing: TimingConfig,
    roster: tuple[OpponentSpec, ...],
    run_dir: Path,
    log_level: str = "full",
    hero_path: str | Path = "bots/my_bot.py",
) -> tuple[list[dict], float]:
    if workers < 1:
        raise ValueError("workers must be at least 1")
    context = mp.get_context("spawn")
    task_queue = context.Queue()
    result_queue = context.Queue()
    for task in enumerate(seeds):
        task_queue.put(task)

    processes: dict[int, tuple[mp.Process, int]] = {}
    active: dict[int, tuple[int, int, float]] = {}
    generations: dict[int, int] = {}
    results: dict[int, dict] = {}
    start = time.perf_counter()
    worker_count = min(workers, len(seeds))

    def start_worker(slot: int) -> None:
        generation = generations.get(slot, -1) + 1
        generations[slot] = generation
        process = context.Process(
            target=_worker,
            args=(
                task_queue,
                result_queue,
                slot,
                generation,
                rules,
                timing,
                roster,
                str(run_dir),
                log_level,
                str(hero_path),
            ),
        )
        process.start()
        processes[slot] = (process, generation)

    for slot in range(worker_count):
        start_worker(slot)

    while len(results) < len(seeds):
        try:
            message = result_queue.get(timeout=0.05)
        except queue.Empty:
            message = None

        if message is not None:
            slot = message["worker_id"]
            generation = message["generation"]
            current = processes.get(slot)
            if current is not None and current[1] == generation:
                index = message["task_index"]
                if message["event"] == "started":
                    active[slot] = (index, message["seed"], time.monotonic())
                elif message["event"] == "finished":
                    results[index] = message["summary"]
                    active.pop(slot, None)

        now = time.monotonic()
        for slot, (index, seed, started) in tuple(active.items()):
            if now - started > timing.sng_wall_timeout_s:
                process, generation = processes[slot]
                if process.is_alive():
                    process.terminate()
                process.join()
                results[index] = {
                    "seed": seed,
                    "failed": True,
                    "worker_error": "sng_wall_timeout",
                }
                active.pop(slot, None)
                start_worker(slot)

        for slot, (process, generation) in tuple(processes.items()):
            if process.is_alive():
                continue
            process.join()
            processes.pop(slot, None)
            job = active.pop(slot, None)
            if job is not None:
                index, seed, _ = job
                results[index] = {
                    "seed": seed,
                    "failed": True,
                    "worker_error": f"worker_exit_{process.exitcode}",
                }
            if len(results) < len(seeds):
                start_worker(slot)

    for process, _ in processes.values():
        if process.is_alive():
            task_queue.put(None)
    for process, _ in processes.values():
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join()

    task_queue.close()
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
