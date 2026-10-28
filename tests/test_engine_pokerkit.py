from pokerpractice.engine import Action, ActionKind, PokerKitHoldemAdapter


def _start(
    stacks=(100, 100),
    live=(0, 1),
    button=1,
    blinds=(10, 20),
    holes=None,
    board=("2c", "3d", "4h", "5s", "9c"),
):
    holes = holes or {
        seat: cards for seat, cards in enumerate(
            (("As", "Kd"), ("Qh", "Js"), ("Tc", "Th"), ("8c", "8d"))
        )
    }
    adapter = PokerKitHoldemAdapter()
    adapter.start_hand(
        tuple(stacks),
        tuple(live),
        button,
        blinds[0],
        blinds[1],
        holes,
        tuple(board),
        ("6c", "7c", "8h"),
    )
    return adapter


def _finish_with_checks(adapter):
    while adapter.actor_seat() is not None:
        legal = adapter.legal_actions()
        action = Action(ActionKind.CHECK) if legal.can_check else Action(ActionKind.CALL)
        adapter.apply(action)
    return adapter.hand_result()


def test_heads_up_button_acts_first_preflop_and_big_blind_first_postflop():
    adapter = _start()
    before = adapter.public_view()["stacks"]
    view = adapter.public_view()
    view["stacks"] = (0, 0)
    assert adapter.public_view()["stacks"] == before
    assert adapter.actor_seat() == 1
    adapter.apply(Action(ActionKind.CALL))
    assert adapter.actor_seat() == 0
    adapter.apply(Action(ActionKind.CHECK))
    assert adapter.actor_seat() == 0


def test_minimum_open_is_two_big_blinds_and_raise_amount_is_raise_to():
    adapter = _start()
    legal = adapter.legal_actions()
    assert legal.min_raise_to == 40
    assert legal.max_raise_to == 100
    event = adapter.apply(Action(ActionKind.RAISE, 40))
    assert event.kind == "raise"
    assert event.amount == 40
    assert adapter.legal_actions().call_amount == 20


def test_short_all_in_raise_does_not_reopen_to_original_raiser():
    adapter = _start(
        stacks=(25, 100, 100),
        live=(0, 1, 2),
        button=2,
        blinds=(5, 10),
        holes={
            0: ("As", "Kd"),
            1: ("Qh", "Js"),
            2: ("Tc", "Th"),
        },
    )
    assert adapter.actor_seat() == 2
    adapter.apply(Action(ActionKind.RAISE, 20))
    assert adapter.actor_seat() == 0
    adapter.apply(Action(ActionKind.RAISE, 25))
    assert adapter.actor_seat() == 1
    adapter.apply(Action(ActionKind.CALL))
    assert adapter.actor_seat() == 2
    legal = adapter.legal_actions()
    assert legal.call_amount == 5
    assert legal.min_raise_to is None
    adapter.apply(Action(ActionKind.CALL))
    _finish_with_checks(adapter)


def test_three_way_all_in_creates_main_and_side_pot():
    adapter = _start(
        stacks=(100, 60, 30),
        live=(0, 1, 2),
        button=2,
        blinds=(1, 2),
        holes={
            0: ("As", "Ah"),
            1: ("Ks", "Kh"),
            2: ("Qs", "Qh"),
        },
    )
    assert adapter.actor_seat() == 2
    adapter.apply(Action(ActionKind.RAISE, 30))
    assert adapter.actor_seat() == 0
    adapter.apply(Action(ActionKind.RAISE, 100))
    assert adapter.actor_seat() == 1
    adapter.apply(Action(ActionKind.CALL))
    result = adapter.hand_result()
    assert sorted(pot.amount for pot in result.pots) == [60, 90]
    assert {
        pot.amount: (pot.eligible_seats, pot.winners)
        for pot in result.pots
    } == {
        60: ((0, 1), (0,)),
        90: ((0, 1, 2), (0,)),
    }
    assert result.refunds == ((0, 40),)
    assert sum(result.ending_stacks) == sum(result.starting_stacks)


def test_uncalled_bet_is_refunded_by_pokerkit():
    adapter = _start()
    adapter.apply(Action(ActionKind.RAISE, 40))
    adapter.apply(Action(ActionKind.FOLD))
    result = adapter.hand_result()
    assert result.ending_stacks == (80, 120)
    assert sum(amount for _, amount in result.refunds) == 20


def test_split_pot_odd_chip_goes_to_earliest_winner_in_pokerkit_order():
    adapter = _start(
        stacks=(1, 1, 1),
        live=(0, 1, 2),
        button=2,
        blinds=(1, 2),
        holes={
            0: ("As", "3s"),
            1: ("Ah", "4h"),
            2: ("3c", "4c"),
        },
        board=("Kc", "Kd", "Qc", "Qd", "2h"),
    )
    adapter.apply(Action(ActionKind.CALL))
    result = adapter.hand_result()
    assert result.ending_stacks == (2, 1, 0)
    assert sum(result.ending_stacks) == 3
    assert result.pots[0].amount == 3
    assert result.pots[0].winners == (0, 1)


def test_showdown_uses_pokerkit_ranking():
    adapter = _start(
        holes={0: ("As", "Ah"), 1: ("Ks", "Kh")},
        board=("2c", "3d", "4h", "5s", "9c"),
    )
    adapter.apply(Action(ActionKind.CALL))
    adapter.apply(Action(ActionKind.CHECK))
    result = _finish_with_checks(adapter)
    assert result.ending_stacks == (120, 80)
    assert dict(result.showdown_ranks)[0].startswith("Straight")
