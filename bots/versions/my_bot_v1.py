"""The one file to edit when developing your hero strategy."""

from pokerpractice.bots.base import Bot, Observation, PublicHandResult, TableStartInfo
from pokerpractice.engine.base import Action, ActionKind


class MyBot(Bot):
    def __init__(self, rng=None) -> None:
        super().__init__(rng)
        self.hands_seen = 0

    def on_table_start(self, info: TableStartInfo) -> None:
        self.hands_seen = 0

    def act(self, obs: Observation) -> Action:
        self.last_diagnostics = {
            "reason": "practice default",
            "hands_seen": self.hands_seen,
        }

        # Preflop selection: replace this simple rule with your starting-hand logic.
        if obs.street == "preflop":
            if obs.legal.can_check:
                return Action(ActionKind.CHECK)
            if obs.legal.can_call:
                return Action(ActionKind.CALL)
            return Action(ActionKind.FOLD)

        # Postflop decisions: use obs.hole_cards and obs.board for your hand logic.
        if obs.legal.can_check:
            return Action(ActionKind.CHECK)

        # Sizing: every RAISE amount is a raise-to total for this street.
        # This example avoids raises; choose an integer between these bounds.
        if obs.legal.min_raise_to is not None and obs.legal.max_raise_to is not None:
            self.last_diagnostics = {"reason": "example postflop call"}

        # Memory: instance fields persist within this SNG and reset for the next one.
        return Action(ActionKind.CALL if obs.legal.can_call else ActionKind.FOLD)

    def on_hand_end(self, result: PublicHandResult) -> None:
        self.hands_seen += 1
