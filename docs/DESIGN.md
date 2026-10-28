# Design decisions

## Adapter boundary

`engine/base.py` defines neutral action, legal-bound, hand-result, and event records plus the `GameAdapter` protocol. `engine/pokerkit_holdem.py` is the phase-1 Hold'em adapter. Tournament orchestration and persistence consume neutral result/event data; PokerKit-specific objects stay inside the adapter.

Seat mapping is explicit: live seats are ordered clockwise beginning immediately after the button and ending with the button. That matches PokerKit's multiway SB/BB order and its heads-up BB-at-index-0/button-SB-at-index-1 behavior. Cards come from the runner's deterministic deck schedule and are assigned by absolute seat before being dealt to PokerKit.

## Engine choice

PokerKit 0.7.7 is MIT-licensed and supports Python >=3.11. Its no-limit Hold'em state machine covers betting, minimum raises, short all-in reopening, side pots, refunds, and hand ranking. The pinned API's `complete_bet_or_raise_to` uses raise-to amounts. The project targets Python 3.12 rather than the environment's older system Python. A custom engine would duplicate difficult all-in and side-pot rules and is not warranted.

PokerKit receives ante/blind posting, bet collection, burning, showdown, hand-killing, and chip push/pull automations. Hole cards and board cards are supplied explicitly from the runner schedule. No side-pot payout calculation is reimplemented in the runner.

Odd-chip resolution is checked against PokerKit's actual behavior in the adapter tests; the test and result are the source of truth if a later PokerKit version changes.

## Determinism and hidden information

Each SNG derives SHA-256-based independent `random.Random` streams for seating, each bot, and each hand's shuffled deck. Absolute seats receive `deck[2*s:2*s+2]`, and the board is `deck[16:21]`. Frozen observations contain primitive tuples and dataclasses only; no PokerKit state, deck, other players' cards, strategy names, or seeds cross into bot code.

## Current scope

Phase 1 includes a practice tournament, generic opponent registry, simple placeholder opponents, run/batch commands, timings, and structured logs. The seven researched opponents and richer analysis/replay commands are phase 2.
