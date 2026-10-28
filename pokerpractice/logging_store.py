from __future__ import annotations

import gzip
import hashlib
import importlib.metadata
import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pokerpractice import __version__
from pokerpractice.tournament import SNGResult


def create_run_dir(root: str | Path = "runs") -> Path:
    runs_root = Path(root)
    runs_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short_id = os.urandom(3).hex()
    path = runs_root / f"{timestamp}_{short_id}"
    path.mkdir(exist_ok=False)
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(
    run_dir: Path,
    rules: dict,
    timing: dict,
    logging_config: dict,
    roster: list[dict],
    seeds: list[int],
    label: str | None,
    source_root: Path,
) -> dict:
    snapshot = run_dir / "snapshot"
    snapshot.mkdir()
    snapshot_paths = [
        source_root / "pokerpractice" / "bots" / "my_bot.py",
        *sorted((source_root / "configs").glob("*.yaml")),
    ]
    hashes = {}
    for source in snapshot_paths:
        destination = snapshot / source.relative_to(source_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        hashes[str(destination.relative_to(run_dir))] = _sha256(destination)
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source_root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    versions = {"poker-practice": __version__}
    for package in ("pokerkit", "eval7", "PyYAML", "pytest"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    manifest = {
        "schema_version": 1,
        "config": {"rules": rules, "timing": timing, "logging": logging_config},
        "seeds": seeds,
        "python": os.sys.version,
        "dependencies": versions,
        "opponent_params": roster,
        "hero_bot": {
            "snapshot": "snapshot/pokerpractice/bots/my_bot.py",
            "sha256": hashes["snapshot/pokerpractice/bots/my_bot.py"],
        },
        "snapshot_hashes": hashes,
        "git_commit": commit,
        "label": label,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (run_dir / "shards").mkdir()
    _initialize_db(run_dir / "results.sqlite")
    return manifest


def _initialize_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE sngs (
                seed INTEGER PRIMARY KEY,
                hero_seat INTEGER,
                hero_finish INTEGER,
                points REAL,
                final_stack INTEGER,
                busted INTEGER,
                hands_played INTEGER,
                failed INTEGER
            );
            CREATE TABLE hand_summaries (
                seed INTEGER,
                hand_number INTEGER,
                button INTEGER,
                hero_delta INTEGER,
                hero_position TEXT,
                hero_stack_bb REAL,
                showdown INTEGER,
                eliminated_count INTEGER,
                PRIMARY KEY (seed, hand_number)
            );
            CREATE TABLE decisions (
                seed INTEGER,
                hand_number INTEGER,
                seat INTEGER,
                bot_label TEXT,
                street TEXT,
                position TEXT,
                stack_bucket TEXT,
                opponent_style_lineup TEXT,
                failure_type TEXT,
                latency_ns INTEGER
            );
            CREATE INDEX idx_decisions_street ON decisions(street);
            CREATE INDEX idx_decisions_position ON decisions(position);
            CREATE INDEX idx_decisions_stack_bucket ON decisions(stack_bucket);
            CREATE INDEX idx_decisions_lineup ON decisions(opponent_style_lineup);
            CREATE INDEX idx_decisions_failure_type ON decisions(failure_type);
            CREATE INDEX idx_decisions_latency ON decisions(latency_ns);
            """
        )


def _decision_index(row: dict) -> dict:
    return {
        key: row.get(key)
        for key in (
            "hand_number",
            "seat",
            "bot_label",
            "street",
            "position",
            "stack_bucket",
            "lineup",
            "failure_type",
            "latency_ns",
        )
    }


def write_worker_shard(run_dir: Path, worker_id: int, result: SNGResult, log_level: str) -> None:
    path = run_dir / "shards" / f"worker-{worker_id}.jsonl.gz"
    compact_stats = {
        label: {key: value for key, value in values.items() if key != "latencies_ns"}
        for label, values in result.stats.items()
    }
    payload = {
        "summary": {
            key: getattr(result, key)
            for key in (
                "seed", "hero_seat", "hero_finish", "hero_points", "hero_final_stack",
                "hero_busted", "hands_played", "failed", "points_by_seat",
                "final_stacks", "elapsed_ns",
            )
        },
        "hands": list(result.hand_records) if log_level != "summary" else [],
        "decisions": (
            [
                row
                for row in result.decision_records
                if log_level == "full" or row["bot_label"] == "hero"
            ]
            if log_level != "summary"
            else []
        ),
        "decision_index": [_decision_index(row) for row in result.decision_records],
    }
    payload["summary"]["stats"] = compact_stats
    with gzip.open(path, "at", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, separators=(",", ":"), default=_json_default) + "\n")


def _json_default(value: Any):
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _insert_payload(connection: sqlite3.Connection, payload: dict) -> None:
    summary = payload["summary"]
    connection.execute(
        "INSERT OR REPLACE INTO sngs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            summary["seed"], summary["hero_seat"], summary["hero_finish"],
            summary["hero_points"], summary["hero_final_stack"],
            int(summary["hero_busted"]), summary["hands_played"], int(summary["failed"]),
        ),
    )
    hero_seat = summary["hero_seat"]
    for hand in payload["hands"]:
        hero_delta = hand["chip_deltas"][hero_seat]
        start_stack = hand["starting_stacks"][hero_seat]
        blind = hand["blinds"][1]
        hero_decision = next(
            (
                row for row in payload["decision_index"]
                if row["hand_number"] == hand["hand_number"] and row["seat"] == hero_seat
            ),
            None,
        )
        connection.execute(
            "INSERT OR REPLACE INTO hand_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                summary["seed"], hand["hand_number"], hand["button"], hero_delta,
                hero_decision["position"] if hero_decision else None,
                start_stack / blind if blind else None,
                int(bool(hand["showdown_ranks"])),
                len(hand["eliminations"]),
            ),
        )
    connection.executemany(
        "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                summary["seed"], row["hand_number"], row["seat"], row["bot_label"],
                row["street"], row["position"], row["stack_bucket"],
                json.dumps(row["lineup"]), row["failure_type"], row["latency_ns"],
            )
            for row in payload["decision_index"]
        ),
    )


def merge_shards(run_dir: Path, log_level: str) -> None:
    hands_path = run_dir / "hands.jsonl.gz"
    decisions_path = run_dir / "decisions.jsonl.gz"
    hands_out = gzip.open(hands_path, "wt", encoding="utf-8") if log_level != "summary" else None
    decisions_out = gzip.open(decisions_path, "wt", encoding="utf-8") if log_level != "summary" else None
    try:
        with sqlite3.connect(run_dir / "results.sqlite") as connection:
            for shard in sorted((run_dir / "shards").glob("worker-*.jsonl.gz")):
                with gzip.open(shard, "rt", encoding="utf-8") as stream:
                    for line in stream:
                        payload = json.loads(line)
                        _insert_payload(connection, payload)
                        if hands_out:
                            for hand in payload["hands"]:
                                hands_out.write(json.dumps(hand, separators=(",", ":")) + "\n")
                        if decisions_out:
                            for row in payload["decisions"]:
                                decisions_out.write(json.dumps(row, separators=(",", ":")) + "\n")
            connection.commit()
    finally:
        if hands_out:
            hands_out.close()
        if decisions_out:
            decisions_out.close()


def write_direct_result(run_dir: Path, result: SNGResult, log_level: str) -> None:
    write_worker_shard(run_dir, 0, result, log_level)
    merge_shards(run_dir, log_level)
