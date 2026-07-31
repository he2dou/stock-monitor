from datetime import date

from src.kline_history import parse_nasdaq_historical, parse_yahoo_chart


def test_parse_yahoo_chart_daily_bars_filters_and_skips_nulls():
    payload = {
        "chart": {
            "result": [{
                "timestamp": [1704067200, 1704153600, 1704240000],
                "indicators": {
                    "quote": [{
                        "open": [10.0, None, 12.0],
                        "high": [11.0, None, 13.0],
                        "low": [9.0, None, 11.5],
                        "close": [10.5, None, 12.5],
                        "volume": [1000, 2000, 3000],
                    }],
                    "adjclose": [{"adjclose": [10.4, None, 12.4]}],
                },
            }],
            "error": None,
        }
    }

    bars = parse_yahoo_chart("SOXL", "SOXL", "美股", payload, date(2024, 1, 1), date(2024, 1, 3))

    assert len(bars) == 2
    assert bars[0].date == "2024-01-01"
    assert bars[0].close == 10.5
    assert bars[1].date == "2024-01-03"
    assert bars[1].adj_close == 12.4

def test_parse_nasdaq_historical_sorts_rows_and_parses_numbers():
    payload = {
        "data": {
            "tradesTable": {
                "rows": [
                    {
                        "date": "07/30/2026",
                        "close": "$114.72",
                        "volume": "109,931,900",
                        "open": "$107.38",
                        "high": "$118.00",
                        "low": "$105.12",
                    },
                    {
                        "date": "07/29/2026",
                        "close": "91.99",
                        "volume": "149,950,200",
                        "open": "107.62",
                        "high": "112.6685",
                        "low": "91.50",
                    },
                ]
            }
        }
    }

    bars = parse_nasdaq_historical("SOXL", "SOXL", "美股", payload, date(2026, 7, 29), date(2026, 7, 30))

    assert [bar.date for bar in bars] == ["2026-07-29", "2026-07-30"]
    assert bars[0].close == 91.99
    assert bars[1].volume == 109931900
    assert bars[1].source == "nasdaq"
