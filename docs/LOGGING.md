# Logs and schemas

Each run directory is unique and has a schema-versioned `manifest.json` with resolved rules/timing/logging config, seeds, Python/dependency versions, opponent params, hero bot snapshot/hash, git revision if available, and the optional user label.

- `hands.jsonl.gz`: privileged hand records; hole cards, board, all actions, chip deltas, pots, showdown cards, eliminations, and scoring updates.
- `decisions.jsonl.gz`: agent-visible observation, legal actions, requested/applied actions, latency, failures, and optional diagnostics. `full` keeps all bots' decisions; `hero` keeps only hero decisions; `summary` suppresses detailed JSONL.
- `results.sqlite`: `sngs`, `hand_summaries`, and indexed decision metadata tables.
- `shards/`: worker-isolated compressed SNG records. Parent merges the index and detail streams after workers finish.

The phase-1 schema is intentionally small. It records the complete action history and the exact hero-facing observations; phase 2 expands the query/report and replay views.
