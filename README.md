# Poker Practice

Poker Practice is a local, deterministic Texas Hold'em simulation for developing and comparing bots. PokerKit 0.7.7 owns betting, side pots, refunds, and showdown resolution; the practice runner owns tournaments, schedules, observations, timing, and logs.

The competition's full rules are not available. The sample schedule, payouts, 8-player table, 200-hand cap, and 1 ms budget are **ASSUMPTIONS for practice**, not official specifications. Defaults are in `configs/`. The button uses a moving-button simplification: after a hand it advances to the next live seat, and the next live seats post SB/BB; there is no dead-button rule. The default has no antes or rake.

## Install and run

Install the locked Python 3.12 environment with `uv`:

```sh
uv python install 3.12
uv sync --python 3.12 --extra dev
uv run poker-practice run --seed 1000000 --label first-run
uv run poker-practice batch -n 50 --workers 8 --seed-set tuning --label first-batch
```

Use `uv run poker-practice ...` or activate `.venv` and run `poker-practice ...`; `python -m pokerpractice` is equivalent. Tuning seeds start at 1,000,000; held-out `eval` seeds start at 9,000,000. Keep evaluation seeds out of strategy tuning.

Edit **`bots/my_bot.py`**. Its `MyBot` class implements `act(obs) -> Action`, with optional `on_table_start` and `on_hand_end` hooks. The sample comments mark the preflop, postflop, sizing, and memory sections. Actions use integer chips; a `RAISE` amount is always the total street contribution after the action ("raise to"), including an opening bet. `--hero PATH` loads any saved `MyBot` subclass and snapshots/hashes the exact file used.

The default roster is Nit, TAG, LAG, calling station, maniac, equity/pot-odds, and short-stack tournament baseline. Each has an explicit parameter dataclass and weak/normal/strong preset. `configs/roster.yaml` can repeat a style seven times. The equity bot's postflop Monte Carlo is bounded to 150 samples and defaults to 30. Bot RNGs are independent of dealing, and each SNG constructs fresh bot instances.

## Inspecting and comparing practice runs

Runs are written to unique `runs/<UTC timestamp>_<short id>/` directories; an existing directory is never overwritten. Every run contains a manifest, immutable source/config snapshot, and `results.sqlite`. `full` and `hero` also write hand and decision JSONL; `summary` writes neither JSONL file. Worker shards are removed after a successful merge by default; `--keep-shards` preserves them for debugging.

Example end-to-end development loop:

1. Run the checked-in short example as v1 on 200 tuning seeds:
   ```sh
   uv run poker-practice batch -n 200 --workers 8 --seed 1000000 --hero bots/versions/my_bot_v1.py --label v1 --log-level hero
   ```
   Save the printed run directory as `RUN1`.
2. Find its biggest loss and note the seed/hand number:
   ```sh
   uv run poker-practice hands "$RUN1" --biggest-losses 1
   ```
3. Replay only the information the hero saw:
   ```sh
   uv run poker-practice replay "$RUN1" --sng 1000000 --hand 12 --view hero
   ```
   Substitute the seed and hand reported by `hands`. Optional offline equity uses `--equity`; it is explicitly labelled and is not part of the bot's decision.
4. The current `bots/my_bot.py` is v2: it widens the v1 postflop value-bet condition to include top pair. Run that documented edit on exactly the same seed range:
   ```sh
   uv run poker-practice batch -n 200 --workers 8 --seed 1000000 --hero bots/my_bot.py --label v2 --log-level hero
   ```
   Save this run directory as `RUN2`.
5. Compare paired seeds and review the uncertainty/divergence caveat:
   ```sh
   uv run poker-practice compare "$RUN1" "$RUN2"
   ```

Useful analysis commands are `report RUN`, `decisions RUN --street river --position BTN`, `hands RUN --biggest-wins 10`, `replay RUN --sng SEED --hand HAND --view omniscient`, `profile -n 200`, `bench -n 100`, and `aggregate6 RUN1 RUN2 RUN3 RUN4 RUN5 RUN6`. Use `--log-level full|hero|summary`; large batches default to hero decisions. `report` and `compare` always retain outcome analysis. Summary logs omit decision, behavior, latency, and replay detail; hero logs retain the hero's observations and hero decision index, while all-style decision latency distributions require full logs.

Measured size of a 100-SNG run directory (bytes; same seeds and roster):

| Log level | 100 SNGs | Projection for 10,000 SNGs |
|---|---:|---:|
| full | 51,245,523 | 5,124,552,300 (5.12 GB) |
| hero | 12,306,295 | 1,230,629,500 (1.23 GB) |
| summary | 885,901 | 88,590,100 (88.6 MB) |

## Limits

- The default opponents are configurable practice heuristics, not optimal play or proxies for real competitors.
- A deterministic paired seed shares seats, button schedule, RNG streams, and absolute-seat card assignments. Once actions differ, subsequent game states naturally diverge.
- Competition timing uses a local soft check after `act()` and an optional Unix `setitimer` interrupt. The interrupt cannot reliably preempt native C code, and these mechanisms are not an organizer sandbox.
- Short-stack push/fold and payout-risk adjustments are documented heuristics, not Nash ranges or a substitute for exact ICM decisions during a hand.
- PokerKit is used as the source of truth for betting and showdown pots. Odd-chip behavior follows PokerKit's verified default; see `docs/DESIGN.md` and the adapter tests.
- This build does not implement external network or AI calls.
