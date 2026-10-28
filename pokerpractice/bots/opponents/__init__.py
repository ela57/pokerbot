"""Placeholder opponents for phase 1; phase 2 registers the seven styles."""

from .check_call import CheckCallOpponent
from .random_legal import RandomLegalOpponent
from .strategies import (
    CallingStationBot,
    EquityBot,
    LAGBot,
    ManiacBot,
    NitBot,
    ShortStackBot,
    TAGBot,
)

__all__ = [
    "CallingStationBot",
    "CheckCallOpponent",
    "EquityBot",
    "LAGBot",
    "ManiacBot",
    "NitBot",
    "RandomLegalOpponent",
    "ShortStackBot",
    "TAGBot",
]
