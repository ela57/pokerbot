import time

from pokerpractice.bots.base import Bot, Observation, PublicSeat
from pokerpractice.config import TimingConfig
from pokerpractice.engine import Action, ActionKind, LegalActions
from pokerpractice.timing import call_bot


def _obs():
    return Observation(
        hole_cards=("As", "Kd"),
        board=(),
        street="preflop",
        my_seat=0,
        button_seat=1,
        actor_seat=0,
        seats=(
            PublicSeat(0, "opp-00000000", 100, 0, 0, False, False, True, "BTN"),
            PublicSeat(1, "opp-00000001", 100, 0, 0, False, False, True, "BB"),
        ),
        pot_total=3,
        pot_breakdown=(),
        small_blind=1,
        big_blind=2,
        level=0,
        players_remaining=2,
        to_call=0,
        legal=LegalActions(True, True, False, 0, None, None),
        action_history=(),
        hand_number=1,
        hands_remaining=199,
        payouts=(100.0, 60.0),
    )


class RaisingBot(Bot):
    def act(self, obs):
        raise RuntimeError("intentional")


class InvalidBot(Bot):
    def act(self, obs):
        return Action(ActionKind.RAISE, 999)


class SlowBot(Bot):
    def act(self, obs):
        time.sleep(0.01)
        return Action(ActionKind.CHECK)


class HangingBot(Bot):
    def act(self, obs):
        while True:
            pass


def test_exception_and_invalid_actions_fall_back_and_are_classified():
    config = TimingConfig(mode="dev", hard_timeout_ms=0)
    exceptional = call_bot(RaisingBot(), _obs(), config)
    invalid = call_bot(InvalidBot(), _obs(), config)
    assert exceptional.failure_type == "exception"
    assert exceptional.applied.kind == ActionKind.CHECK
    assert invalid.failure_type == "invalid_action"
    assert invalid.applied.kind == ActionKind.CHECK


def test_soft_timeout_falls_back():
    result = call_bot(
        SlowBot(),
        _obs(),
        TimingConfig(mode="competition", budget_ms=1, hard_timeout_ms=0),
    )
    assert result.failure_type == "timeout_soft"
    assert result.applied.kind == ActionKind.CHECK


def test_hard_timeout_interrupts_python_bot_on_unix():
    import signal

    if not hasattr(signal, "setitimer"):
        return
    result = call_bot(
        HangingBot(),
        _obs(),
        TimingConfig(mode="competition", budget_ms=100, hard_timeout_ms=10),
    )
    assert result.failure_type == "timeout_hard"
    assert result.applied.kind == ActionKind.CHECK


def test_batch_watchdog_marks_a_slow_sng_failed(tmp_path):
    from pokerpractice.batch import run_batch
    from pokerpractice.bots.roster import OpponentSpec
    from pokerpractice.config import RulesConfig
    from pokerpractice.logging_store import _initialize_db

    run_dir = tmp_path / "watchdog"
    run_dir.mkdir()
    (run_dir / "shards").mkdir()
    _initialize_db(run_dir / "results.sqlite")
    rows, _ = run_batch(
        [4_000_001],
        1,
        RulesConfig(hand_cap=200),
        TimingConfig(mode="dev", hard_timeout_ms=0, sng_wall_timeout_s=0.000001),
        tuple(OpponentSpec("check_call", {}) for _ in range(7)),
        run_dir,
    )
    assert rows[0]["failed"]


def test_run_directories_are_unique_and_existing_runs_are_preserved(tmp_path):
    from pokerpractice.logging_store import create_run_dir

    root = tmp_path / "runs"
    first = create_run_dir(root)
    marker = first / "marker"
    marker.write_text("keep", encoding="utf-8")
    second = create_run_dir(root)
    assert first != second
    assert marker.read_text(encoding="utf-8") == "keep"
