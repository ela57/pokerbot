# Poker Practice

Poker Practice is a local, deterministic Texas Hold'em simulation for developing and comparing bots. PokerKit 0.7.7 owns betting, side pots, refunds, and showdown resolution; the practice runner owns tournaments, schedules, observations, timing, and logs.

The competition's full rules are not available. The sample schedule, payouts, 8-player table, 200-hand cap, and 1 ms budget are **ASSUMPTIONS for practice**, not official specifications. Defaults are in `configs/`. The button uses a moving-button simplification: after a hand it advances to the next live seat, and the next live seats post SB/BB; there is no dead-button rule. The default has no antes or rake.

## Install and run

Python 3.12 is used for the checked-in `uv.lock`:

```sh
uv sync --extra dev
.venv/bin/poker-practice run --seed 1000000 --label v1
.venv/bin/poker-practice batch -n 50 --workers 8 --seed-set tuning --label v1
```

Use `poker-practice` directly when the virtual environment is activated. `python -m pokerpractice` is equivalent. Tuning seeds start at 1,000,000; held-out `eval` seeds start at 9,000,000. Keep evaluation seeds out of strategy tuning.

Edit **`pokerpractice/bots/my_bot.py`**. Its `MyBot` class implements `act(obs) -> Action`, with optional `on_table_start` and `on_hand_end` hooks. The sample comments mark the preflop, postflop, sizing, and memory sections. Actions use integer chips; a `RAISE` amount is always the total street contribution after the action ("raise to"), including an opening bet.

The other seven seats currently use phase-1 `check_call` and `random` placeholders. The roster loader is registry-based; phase 2 can register styles without changing the tournament runner. Bot RNGs are independent of dealing, and each SNG constructs fresh bot instances.

## Inspecting and comparing practice runs

Runs are written to unique `runs/<UTC timestamp>_<short id>/` directories; an existing directory is never overwritten. A run contains a manifest and immutable source/config snapshot, `hands.jsonl.gz` (privileged hand data), `decisions.jsonl.gz` (agent-visible observation records, according to log level), `results.sqlite`, and worker shards for batches.

Example development loop:

1. Run `poker-practice batch -n 100 --seed 1000000 --label v1`.
2. Inspect `hands.jsonl.gz` and `decisions.jsonl.gz` for a losing hand. The decision record contains the exact observation available to the bot; a loss by itself does not prove a bad decision.
3. Edit `pokerpractice/bots/my_bot.py`.
4. Run the same seed range with `--label v2`, then compare summary rows in each `results.sqlite`.

Phase 1 intentionally exposes only `run` and `batch`; replay/report/compare/profile tooling and the seven researched strategy opponents are phase-2 work. Supported log levels are `full`, `hero`, and `summary`. Large batches default to hero decisions; pass `--log-level full` to retain all decision observations. Summary-only still writes SQLite indexes.

## Limits

- The default opponents are deliberately simple placeholders. Results do not indicate optimal play or performance against real competitors.
- A deterministic paired seed shares seats, button schedule, RNG streams, and absolute-seat card assignments. Once actions differ, subsequent game states naturally diverge.
- Competition timing uses a local soft check after `act()` and an optional Unix `setitimer` interrupt. The interrupt cannot reliably preempt native C code, and these mechanisms are not an organizer sandbox.
- PokerKit is used as the source of truth for betting and showdown pots. Odd-chip behavior follows PokerKit's verified default; see `docs/DESIGN.md` and the adapter tests.
- This build does not implement external network or AI calls.
