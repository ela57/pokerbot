from __future__ import annotations

import signal
import threading
import time
from dataclasses import dataclass

from pokerpractice.bots.base import Bot, Observation
from pokerpractice.config import TimingConfig
from pokerpractice.engine.base import Action, ActionKind


class HardDecisionTimeout(TimeoutError):
    pass


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    requested: Action | None
    applied: Action
    latency_ns: int
    failure_type: str | None
    exception_text: str | None


def _fallback(obs: Observation) -> Action:
    return Action(ActionKind.CHECK if obs.legal.can_check else ActionKind.FOLD)


def _is_valid(action: object, obs: Observation) -> bool:
    if not isinstance(action, Action):
        return False
    if action.kind == ActionKind.CHECK:
        return obs.legal.can_check
    if action.kind == ActionKind.CALL:
        return obs.legal.can_call
    if action.kind == ActionKind.FOLD:
        return obs.legal.can_fold
    if action.kind == ActionKind.RAISE:
        return (
            action.amount is not None
            and obs.legal.min_raise_to is not None
            and obs.legal.max_raise_to is not None
            and obs.legal.min_raise_to <= action.amount <= obs.legal.max_raise_to
        )
    return False


def call_bot(bot: Bot, obs: Observation, config: TimingConfig) -> DecisionOutcome:
    started = 0
    elapsed = 0
    requested = None
    failure_type = None
    exception_text = None
    timer_active = (
        config.mode == "competition"
        and config.hard_timeout_ms > 0
        and hasattr(signal, "setitimer")
        and threading.current_thread() is threading.main_thread()
    )
    previous_handler = None
    previous_timer = None
    try:
        if timer_active:
            previous_handler = signal.getsignal(signal.SIGALRM)

            def interrupt(signum, frame):
                raise HardDecisionTimeout("hard per-decision timeout")

            previous_timer = signal.getitimer(signal.ITIMER_REAL)
            signal.signal(signal.SIGALRM, interrupt)
            signal.setitimer(signal.ITIMER_REAL, config.hard_timeout_ms / 1000)
        started = time.perf_counter_ns()
        requested = bot.act(obs)
        elapsed = time.perf_counter_ns() - started
    except HardDecisionTimeout as exc:
        elapsed = time.perf_counter_ns() - started if started else 0
        failure_type = "timeout_hard"
        exception_text = str(exc)
    except BaseException as exc:
        elapsed = time.perf_counter_ns() - started if started else 0
        failure_type = "exception"
        exception_text = f"{type(exc).__name__}: {exc}"
    finally:
        if timer_active:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer and previous_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, *previous_timer)
    if failure_type is None and not _is_valid(requested, obs):
        failure_type = "invalid_action"
        exception_text = "bot returned an action outside the legal bounds"
    if (
        failure_type is None
        and config.mode == "competition"
        and elapsed > config.budget_ms * 1_000_000
    ):
        failure_type = "timeout_soft"
        exception_text = f"decision took {elapsed / 1_000_000:.3f} ms"
    applied = _fallback(obs) if failure_type else requested
    if not isinstance(applied, Action):
        applied = _fallback(obs)
    return DecisionOutcome(
        requested=requested if isinstance(requested, Action) else None,
        applied=applied,
        latency_ns=elapsed,
        failure_type=failure_type,
        exception_text=exception_text,
    )
