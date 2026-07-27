from abc import ABC, abstractmethod
from src.models import Quote


class DataSource(ABC):
    """Abstract base class for all market data sources."""

    @abstractmethod
    def fetch_quotes(self, stocks: list[dict]) -> list[Quote]:
        """Fetch quotes for the given list of stocks.

        Each stock dict has keys: symbol, name, market.
        Returns a list of Quote objects (may skip unsupported markets).
        """
        raise NotImplementedError
