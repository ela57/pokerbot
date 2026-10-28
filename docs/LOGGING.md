# Logs and schemas

Each run directory is unique and has a schema-versioned `manifest.json` with resolved rules/timing/logging config, seeds, Python/dependency versions, opponent params, hero bot snapshot/hash, git revision if available, and the optional user label.

| Log level | Hands JSONL | Decisions JSONL | SQLite decisions | SQLite summaries |
|---|---|---|---|---|
| `full` | all private hand data | all bots | all bots | SNG results and hero hand summaries |
| `hero` | all private hand data | hero observations only | hero only | SNG results and hero hand summaries |
| `summary` | none | none | no rows | SNG results and hero hand summaries |

`results.sqlite` has `sngs`, `hand_summaries`, and `decisions` tables at every level. At `hero`, the `sngs` rows also retain compact behavior aggregates for outcome reports; raw decision/latency distributions for opponents require `full`. At `summary`, no decision rows or per-decision JSONL are written; reports and paired comparisons retain tournament outcomes, while decision behavior and replay require `hero` or `full`.

Worker output is first written to atomically finalized, per-task files under `shards/`. After a successful merge, `shards/` is removed by default. Pass `--keep-shards` to any run, batch, profile, or bench command to preserve those inputs. A failed merge does not delete them.

The hero replay prints the exact stored observation at each decision. Omniscient replay explicitly includes private hands and hindsight flags; offline equity is calculated after the run and labelled separately from bot decisions.

Measured run-directory sizes for 100 SNGs (bytes; same seeds and default roster):

| Log level | 100 SNGs | Projection for 10,000 SNGs |
|---|---:|---:|
| `full` | 51,245,523 | 5,124,552,300 (5.12 GB) |
| `hero` | 12,306,295 | 1,230,629,500 (1.23 GB) |
| `summary` | 885,901 | 88,590,100 (88.6 MB) |
