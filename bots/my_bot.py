"""A small, deliberately beatable Hold'em example."""

from pokerpractice.bots.base import Bot, Observation, PublicHandResult, TableStartInfo
from pokerpractice.engine.base import Action, ActionKind

RANKS = "23456789TJQKA"


class MyBot(Bot):
    def __init__(self, rng=None) -> None:
        super().__init__(rng)
        self.hands_seen, self.preflop_raises, self._history_seen = 0, {}, 0

    def on_table_start(self, info: TableStartInfo) -> None:
        self.hands_seen, self.preflop_raises, self._history_seen = 0, {}, 0

    def _remember(self, obs: Observation) -> None:
        # Memory: change this block to track other public opponent tendencies.
        for event in obs.action_history[self._history_seen:]:
            if event.street == "preflop" and event.kind == "raise":
                seat = event.seat_anon
                self.preflop_raises[seat] = self.preflop_raises.get(seat, 0) + 1
        self._history_seen = len(obs.action_history)

    def _raise(self, obs: Observation, target: int) -> Action | None:
        low, high = obs.legal.min_raise_to, obs.legal.max_raise_to
        if low is None or high is None:
            return None
        # Sizing: change targets here; a short all-in uses the legal maximum.
        return Action(ActionKind.RAISE, high if high < low else min(high, max(low, target)))

    def _done(self, obs: Observation, reason: str, action: Action) -> Action:
        self.last_diagnostics = {
            "reason": reason,
            "pot_odds": obs.to_call / max(1, obs.pot_total + obs.to_call),
        }
        return action

    def _hand_strength(self, obs: Observation) -> tuple[bool, bool, bool, bool]:
        hole = [RANKS.index(card[0]) for card in obs.hole_cards]
        board = [RANKS.index(card[0]) for card in obs.board]
        ranks = hole + board
        counts = {rank: ranks.count(rank) for rank in set(ranks)}
        pairs = sum(count >= 2 for count in counts.values())
        overpair = len(hole) == 2 and hole[0] == hole[1] > max(board, default=-1)
        top_pair = bool(board) and max(board) in hole
        two_pair = pairs >= 2 or max(counts.values(), default=0) >= 3
        return pairs > 0, two_pair, overpair, top_pair

    def act(self, obs: Observation) -> Action:
        self._remember(obs)

        # Preflop selection: adjust these intentionally narrow starting hands.
        if obs.street == "preflop":
            ranks = [RANKS.index(card[0]) for card in obs.hole_cards]
            suited = obs.hole_cards[0][1] == obs.hole_cards[1][1]
            premium = (ranks[0] == ranks[1] and ranks[0] >= 10) or (suited and sorted(ranks) == [11, 12])
            raise_to = [
                e.amount for e in obs.action_history if e.street == "preflop" and e.kind == "raise"
            ]
            raise_count = len(raise_to)
            if raise_count == 0 and premium:
                limpers = sum(e.street == "preflop" and e.kind == "call" for e in obs.action_history)
                action = self._raise(obs, (3 + limpers) * obs.big_blind)
                if action:
                    return self._done(obs, "premium preflop raise", action)
            medium_pair = ranks[0] == ranks[1] and 5 <= ranks[0] <= 8
            suited_broadway = suited and min(ranks) >= RANKS.index("J")
            if (
                raise_count == 1 and raise_to[0] <= 3 * obs.big_blind
                and obs.legal.can_call and (medium_pair or suited_broadway)
            ):
                return self._done(obs, "call one small raise", Action(ActionKind.CALL))
            fallback = Action(ActionKind.CHECK if obs.legal.can_check else ActionKind.FOLD)
            return self._done(obs, "check or fold preflop", fallback)

        # Postflop decisions: v2 widens value bets to include top pair.
        any_pair, two_pair, overpair, top_pair = self._hand_strength(obs)
        if (two_pair or overpair or top_pair) and obs.legal.min_raise_to is not None:
            seat = obs.seats[obs.my_seat]
            call_total = seat.bet_this_street + obs.to_call
            target = call_total + int((obs.pot_total + obs.to_call) * 2 / 3)
            action = self._raise(obs, target)
            if action:
                return self._done(obs, "value bet or raise", action)
        if obs.legal.can_check:
            return self._done(obs, "check", Action(ActionKind.CHECK))
        if any_pair and obs.legal.can_call and obs.to_call <= obs.pot_total / 2:
            return self._done(obs, "call small bet with a pair", Action(ActionKind.CALL))
        return self._done(obs, "fold weak hand", Action(ActionKind.FOLD))

    def on_hand_end(self, result: PublicHandResult) -> None:
        self.hands_seen += 1
        self._history_seen = 0
