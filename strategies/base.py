"""
Base class for all trading strategies.
Every strategy inherits from BaseStrategy and implements generate_signals().
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List


@dataclass
class Signal:
    """A single trading signal."""
    ticker: str
    action: str          # 'buy', 'sell', or 'hold'
    confidence: float    # 0.0 to 1.0
    reason: str = ""     # human-readable explanation


class BaseStrategy(ABC):
    """Abstract base class for all strategies."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def generate_signals(self, ticker: str, bars: list) -> List[Signal]:
        """
        Given price bars for a ticker, return signals.
        Most strategies return 0 or 1 signal per call.

        Args:
            ticker: Stock symbol like 'AAPL'
            bars: List of dicts with keys: ts, open, high, low, close, volume

        Returns:
            List of Signal objects
        """
        pass

    def __repr__(self):
        return f"<Strategy: {self.name}>"