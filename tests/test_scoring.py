import pytest

from pokerpractice.scoring import award_simultaneous_busts, icm_values


def test_equal_two_player_icm():
    assert icm_values((100, 100), (100, 60)) == pytest.approx((80, 80))


def test_icm_sums_to_remaining_prizes():
    values = icm_values((50, 30, 20), (50, 30, 20))
    assert sum(values) == pytest.approx(100)


def test_three_player_hand_computed_icm():
    # First-place probabilities are 1/2, 3/10, 1/5. The full values follow
    # by conditioning each later place on the first player being removed.
    assert icm_values((50, 30, 20), (50, 30, 20)) == pytest.approx(
        (38.39285714285714, 32.75, 28.85714285714286)
    )


def test_icm_is_not_chip_proportion_for_flat_top_payouts():
    # P1=4/5; P2=(1/10)(8/9)+(1/10)(8/9)=8/45; P3=1/45.
    values = icm_values((80, 10, 10), (50, 30, 20))
    assert values[0] == pytest.approx(412 / 9)
    assert values[0] < 80  # proportional share of the 100 prize pool


def test_simultaneous_eliminations_rank_starting_stacks_and_split_ties():
    awards = award_simultaneous_busts(
        (0, 1, 2), (80, 50, 50, 20), (100, 60, 40, 20), 4
    )
    assert awards[0] == 60
    assert awards[1] == pytest.approx(30)
    assert awards[2] == pytest.approx(30)


def test_simultaneous_tie_shares_the_occupied_payouts():
    awards = award_simultaneous_busts(
        (1, 2), (500, 100, 100, 0), (100, 60, 40, 20), 4
    )
    assert awards == {1: 30, 2: 30}
