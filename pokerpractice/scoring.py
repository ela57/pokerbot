from __future__ import annotations

from functools import lru_cache
def icm_values(stacks: tuple[int, ...], payouts: tuple[float, ...]) -> tuple[float, ...]:
    """Malmuth-Harville ICM via memoized exact subset dynamic programming."""
    n = len(stacks)
    if n == 0:
        return ()
    if len(payouts) < n:
        raise ValueError("need at least one payout per survivor")
    if any(stack < 0 for stack in stacks):
        raise ValueError("stacks cannot be negative")

    @lru_cache(maxsize=None)
    def expected(mask: int, place: int) -> tuple[float, ...]:
        members = tuple(i for i in range(n) if mask & (1 << i))
        result = [0.0] * n
        total = sum(stacks[i] for i in members)
        if len(members) == 1:
            result[members[0]] = float(payouts[place])
            return tuple(result)
        if total == 0:
            # A zero-chip state is not expected, but equal probabilities make
            # the function defined for all nonnegative inputs.
            probabilities = {i: 1 / len(members) for i in members}
        else:
            probabilities = {i: stacks[i] / total for i in members}
        for winner in members:
            probability = probabilities[winner]
            result[winner] += probability * payouts[place]
            rest = expected(mask ^ (1 << winner), place + 1)
            for i in members:
                if i != winner:
                    result[i] += probability * rest[i]
        return tuple(result)

    return expected((1 << n) - 1, 0)


def award_simultaneous_busts(
    busting_seats: tuple[int, ...],
    stacks_at_hand_start: tuple[int, ...],
    payouts: tuple[float, ...],
    alive_before: int,
) -> dict[int, float]:
    if not busting_seats:
        return {}
    positions = list(range(alive_before - len(busting_seats) + 1, alive_before + 1))
    ranked = sorted(
        busting_seats,
        key=lambda seat: (-stacks_at_hand_start[seat], seat),
    )
    awards: dict[int, float] = {}
    cursor = 0
    while cursor < len(ranked):
        end = cursor + 1
        stack = stacks_at_hand_start[ranked[cursor]]
        while end < len(ranked) and stacks_at_hand_start[ranked[end]] == stack:
            end += 1
        tied_seats = ranked[cursor:end]
        tied_places = positions[cursor:end]
        points = sum(payouts[position - 1] for position in tied_places) / len(tied_seats)
        awards.update({seat: points for seat in tied_seats})
        cursor = end
    return awards


def standard_error(values: tuple[float, ...]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    if len(values) < 2:
        return mean, 0.0
    sample_variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return mean, (sample_variance**0.5) / (len(values) ** 0.5)


def icm_hand_example() -> tuple[float, ...]:
    """Helper for the documented 50/30/20 three-player example."""
    return icm_values((50, 30, 20), (50, 30, 20))
