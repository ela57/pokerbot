# Logs and schemas

Each run directory is unique and has a schema-versioned `manifest.json` with resolved rules/timing/logging config, seeds, Python/dependency versions, opponent params, hero bot snapshot/hash, git revision if available, and the optional user label.

- `hands.jsonl.gz`: privileged hand records; hole cards, board, all actions, chip deltas, pots, showdown cards, eliminations, and scoring updates.
- `decisions.jsonl.gz`: agent-visible observation, legal actions, requested/applied actions, latency, failures, and optional diagnostics. `full` keeps all bots' decisions; `hero` keeps only hero decisions; `summary` suppresses detailed JSONL.
- `results.sqlite`: `sngs`, `hand_summaries`, and indexed decision metadata (including opponent-style lineup, failures, and per-decision latency).
- `shards/`: one compressed, atomically finalized SNG record per task. Persistent workers never share file handles; the parent merges result and index streams after workers finish.

`full` contains all decisions and private hand data; `hero` contains private hand data plus only the hero's exact observations; `summary` writes SQLite indexes and summary statistics without detailed JSONL. The hero replay prints the exact stored observation at each decision. Omniscient replay explicitly includes private hands and hindsight flags; offline equity is calculated after the run and labelled separately from bot decisions.
