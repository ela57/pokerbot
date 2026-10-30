import json
import sqlite3

from pokerpractice.analysis import report
from pokerpractice.logging_store import _initialize_db


def test_report_lineup_mean_weights_each_sng_once(tmp_path, capsys):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"config": {"rules": {"hand_cap": 200}}}),
        encoding="utf-8",
    )
    _initialize_db(run_dir / "results.sqlite")
    lineup = "calling_station|tag"

    with sqlite3.connect(run_dir / "results.sqlite") as connection:
        connection.executemany(
            """
            INSERT INTO sngs (
                seed, hero_seat, hero_finish, points, final_stack,
                busted, hands_played, failed, stats_json
            ) VALUES (?, 0, 1, ?, 1000, 0, 10, 0, NULL)
            """,
            [(1, 10), (2, 30)],
        )
        connection.executemany(
            """
            INSERT INTO decisions (seed, bot_label, opponent_style_lineup)
            VALUES (?, 'hero', ?)
            """,
            [(1, lineup)] + [(2, lineup)] * 5,
        )

    report(run_dir)

    output = capsys.readouterr().out
    lineup_line = next(
        line for line in output.splitlines()
        if line.startswith(f"Hero by opponent lineup {lineup}:")
    )
    assert "n=2, mean points=20.000" in lineup_line
    assert "26.667" not in lineup_line
