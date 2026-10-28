# Timing and failures

`dev` mode omits a decision budget but still catches and records exceptions and invalid actions. `competition` mode defaults to a 1 ms soft limit: only `act()` is measured, and an exception, invalid action, or over-budget decision is replaced with CHECK when legal and otherwise FOLD. Failure types are `exception`, `invalid_action`, `timeout_soft`, and `timeout_hard`.

On Unix, competition mode can use `signal.setitimer(ITIMER_REAL)` for the separate per-decision hard interrupt (50 ms by default). It only interrupts the worker's main Python thread and cannot reliably interrupt a native C call. Batch workers are persistent spawned processes that pull SNG tasks from a queue. The parent tracks a separate deadline per active SNG; on timeout it terminates that worker, records `sng_wall_timeout`, launches a replacement, and continues the remaining queue. Failed SNGs are counted and excluded from means.

These are local diagnostics and not an organizer sandbox. Engine work, serialization, and disk writes are outside the action timer. Bot-side expensive analysis belongs outside `act()` or must have an explicitly bounded workload. The equity opponent has a hard maximum of 150 Monte Carlo samples; the default is 30 and should be reduced if measured competition-mode p99 exceeds the 1 ms budget.

`profile` reports VPIP/PFR/AF/WTSD and per-style decision latency. `bench` measures one and eight spawned workers and estimates the wall time for 10,000 SNGs. Compare it with raw PokerKit only as a reference: tournament orchestration, opponent decisions, logging and process startup add work beyond the engine.
