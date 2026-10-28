from __future__ import annotations

import hashlib
import random

RANKS = "23456789TJQKA"
SUITS = "shdc"
STANDARD_DECK = tuple(rank + suit for rank in RANKS for suit in SUITS)


def stream_seed(seed: int, purpose: str, index: int = 0) -> int:
    digest = hashlib.sha256(f"{seed}:{purpose}:{index}".encode()).digest()
    return int.from_bytes(digest[:16], "big")


def stream(seed: int, purpose: str, index: int = 0) -> random.Random:
    return random.Random(stream_seed(seed, purpose, index))


def hand_deck(seed: int, hand_number: int) -> tuple[str, ...]:
    deck = list(STANDARD_DECK)
    stream(seed, "deal", hand_number).shuffle(deck)
    return tuple(deck)
