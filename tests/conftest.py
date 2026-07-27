import pytest
from unittest.mock import MagicMock

@pytest.fixture
def mock_quote_data():
    return {
        "symbol": "159995",
        "name": "芯片ETF",
        "market": "A股",
        "price": 1.234,
        "change_pct": 2.5,
        "volume": 1000000,
    }
