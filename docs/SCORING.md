# Scoring assumptions

The default payout vector is **ASSUMPTION**: `[100, 60, 40, 25, 15, 10, 5, 0]`. Eliminations receive points for their finish positions. Players busted in the same hand are ranked by their stacks at the start of that hand; exact stack ties share the sum of the occupied payout positions equally.

At the hand cap, already awarded elimination points remain fixed. The `k` remaining players split the first `k` payout positions under Malmuth-Harville ICM: first-place probability is stack/total stack and later probabilities recurse on the remaining players. A memoized subset DP supports the configured table sizes. Chip proportion is not used as a substitute for ICM.

The SNG uses 200 hands by default (ASSUMPTION). The button moves to the next live seat after each hand; this is a simplification, not a dead-button implementation.
