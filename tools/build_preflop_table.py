from __future__ import annotations

import argparse
import importlib.metadata
import itertools
import json
import random
from pathlib import Path

import eval7


RANKS = "23456789TJQKA"
SUITS = "shdc"


def hand_classes() -> dict[str, tuple[str, str]]:
    classes = {}
    for high_index in range(len(RANKS) - 1, -1, -1):
        high = RANKS[high_index]
        for low_index in range(high_index, -1, -1):
            low = RANKS[low_index]
            if high == low:
                classes[high + low] = (high + "s", high + "h")
            else:
                classes[high + low + "s"] = (high + "s", low + "s")
                classes[high + low + "o"] = (high + "s", low + "h")
    return classes


def generate(samples: int, seed: int) -> dict:
    if samples < 1:
        raise ValueError("samples must be positive")
    rng = random.Random(seed)
    deck = [rank + suit for rank, suit in itertools.product(RANKS, SUITS)]
    equities = {}
    for hand_class, hero_hand in hand_classes().items():
        remaining = [card for card in deck if card not in hero_hand]
        wins = ties = 0
        hero_cards = [eval7.Card(card) for card in hero_hand]
        for _ in range(samples):
            draw = rng.sample(remaining, 7)
            opponent_cards = [eval7.Card(card) for card in draw[:2]]
            board = [eval7.Card(card) for card in draw[2:]]
            hero_rank = eval7.evaluate(hero_cards + board)
            opponent_rank = eval7.evaluate(opponent_cards + board)
            wins += hero_rank > opponent_rank
            ties += hero_rank == opponent_rank
        equities[hand_class] = (wins + ties / 2) / samples
    ranked = sorted(equities, key=lambda hand: (-equities[hand], hand))
    percentiles = {
        hand: round((index + 1) * 100 / len(ranked), 6)
        for index, hand in enumerate(ranked)
    }
    return {
        "schema_version": 1,
        "generated_with": f"eval7=={importlib.metadata.version('eval7')}",
        "seed": seed,
        "samples_per_class": samples,
        "class_count": len(equities),
        "opponent_model": "uniform random two-card hand",
        "equities": equities,
        "ranked": ranked,
        "percentiles": percentiles,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260217)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "preflop_rank.json",
    )
    args = parser.parse_args()
    table = generate(args.samples, args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(table, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {table['class_count']} classes, "
        f"{table['samples_per_class']} samples/class ({table['class_count'] * table['samples_per_class']:,} deals), "
        f"seed {table['seed']} to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
