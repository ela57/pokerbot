import json
import sqlite3

from pokerpractice.analysis import compare_runs, report
from pokerpractice.bots.roster import OpponentSpec
from pokerpractice.config import RulesConfig, TimingConfig
from pokerpractice.logging_store import (
    _initialize_db,
    merge_shards,
    write_worker_shard,
)
from pokerpractice.tournament import run_sng


def _saved_run(root, name, level, result, keep_shards=False):
    run_dir = root / name
    (run_dir / "shards").mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({
            "config": {
                "rules": {"hand_cap": 4},
                "logging": {"log_level": level},
            },
        }),
        encoding="utf-8",
    )
    _initialize_db(run_dir / "results.sqlite")
    write_worker_shard(run_dir, 0, result, level)
    merge_shards(run_dir, level, keep_shards=keep_shards)
    return run_dir


def test_log_levels_filter_decisions_and_analysis_survives_shard_cleanup(
    tmp_path, capsys
):
    result = run_sng(
        72,
        RulesConfig(players=3, hand_cap=4, payouts=(100, 60, 40)),
        TimingConfig(mode="dev", hard_timeout_ms=0),
        tuple(OpponentSpec("check_call", {}) for _ in range(2)),
    )
    assert not result.failed
    hero = _saved_run(tmp_path, "hero", "hero", result)
    summary = _saved_run(tmp_path, "summary", "summary", result)

    with sqlite3.connect(hero / "results.sqlite") as connection:
        hero_rows = connection.execute("SELECT bot_label FROM decisions").fetchall()
        assert hero_rows
        assert {row[0] for row in hero_rows} == {"hero"}
        assert len(hero_rows) == sum(
            row["bot_label"] == "hero" for row in result.decision_records
        )
        assert connection.execute("SELECT COUNT(*) FROM hand_summaries").fetchone()[0] == len(
            result.hand_summaries
        )
        assert connection.execute("SELECT stats_json FROM sngs").fetchone()[0]
    assert not (hero / "shards").exists()
    assert (hero / "hands.jsonl.gz").exists()
    assert (hero / "decisions.jsonl.gz").exists()

    with sqlite3.connect(summary / "results.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM sngs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM hand_summaries").fetchone()[0] == len(
            result.hand_summaries
        )
        assert connection.execute("SELECT stats_json FROM sngs").fetchone()[0] is None
    assert not (summary / "shards").exists()
    assert not (summary / "hands.jsonl.gz").exists()
    assert not (summary / "decisions.jsonl.gz").exists()

    report(hero)
    assert "Only hero decisions are indexed/logged" in capsys.readouterr().out
    report(summary)
    assert "Decision-level bot metrics and replay require" in capsys.readouterr().out
    compare_runs(hero, summary)
    assert "Outcome comparison is available from summary logs" in capsys.readouterr().out

    retained = _saved_run(tmp_path, "retained", "summary", result, keep_shards=True)
    assert (retained / "shards").is_dir()
