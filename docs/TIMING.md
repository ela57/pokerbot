# Timing and failures

`dev` mode omits a decision budget but still catches and records exceptions and invalid actions. `competition` mode defaults to a 1 ms soft limit: only `act()` is measured, and an exception, invalid action, or over-budget decision is replaced with CHECK when legal and otherwise FOLD. Failure types are `exception`, `invalid_action`, `timeout_soft`, and `timeout_hard`.

On Unix, competition mode can use `signal.setitimer(ITIMER_REAL)` for the separate per-decision hard interrupt (50 ms by default). It only interrupts the worker's main Python thread and cannot reliably interrupt a native C call. The batch parent starts SNGs in spawned worker processes and terminates an SNG that exceeds `sng_wall_timeout_s` (120 s default); failed SNGs are marked and excluded from means.

These are local diagnostics and not an organizer sandbox. Engine work, serialization, and disk writes are outside the action timer. Bot-side expensive analysis belongs outside `act()` or must have an explicitly bounded workload.
