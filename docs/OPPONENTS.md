# Opponents: phase-2 implementation plan

Phase 1 runs only the `check_call` and `random` placeholder strategies. The generic registry and `OpponentSpec(style, params, strength)` loader are in place. The requested sourced strategy implementations are deferred to phase 2; no reference-derived strategy has been falsely presented as implemented or tested here.

Reference set to consult and map to implementation parameters in phase 2:

- Playing styles: https://www.pokerstars.com/poker/learn/lesson/other-poker-playing-styles/
- Countering aggressive opponents: https://www.pokerstars.com/poker/learn/strategies/dealing-with-aggression-strategies-to-counter-hyper-aggressive-players/
- Pot odds: https://www.pokerstars.com/poker/learn/lesson/pot-odds/
- Push/fold: https://www.pokerstars.com/poker/learn/strategies/push-or-fold/
- ICM: https://www.pokerstars.com/poker/learn/lesson/icm-and-its-impact-on-tournaments/

Phase 2 should fetch each page, record any unavailable page honestly, separate sourced concepts from project-chosen thresholds, and avoid copying third-party code or datasets.
