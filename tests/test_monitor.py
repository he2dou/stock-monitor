import pytest
from unittest.mock import MagicMock, patch
from src.monitor import MonitorService


@pytest.fixture(autouse=True)
def _market_open():
    """Default: treat market as open so existing tests are time-independent."""
    with patch("src.monitor.is_market_open", return_value=True):
        yield
from src.models import Quote, Alert, AlertRule

@pytest.fixture
def monitor_setup():
    mock_source = MagicMock()
    mock_source.fetch_quotes.return_value = [
        Quote("AAPL", "Apple", "美股", 210, 1.0, 100)]
    mock_engine = MagicMock()
    mock_notifier = MagicMock()
    return mock_source, mock_engine, mock_notifier

def test_monitor_run_fetches_and_checks(monitor_setup):
    src, engine, notifier = monitor_setup
    engine.check.return_value = []
    svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                         stocks=[{"symbol": "AAPL", "name": "Apple", "market": "美股"}])
    svc.run_once()
    src.fetch_quotes.assert_called_once()
    engine.check.assert_called_once()
    notifier.send.assert_called_once_with([])

def test_monitor_sends_alerts(monitor_setup):
    src, engine, notifier = monitor_setup
    alert = Alert("AAPL", "Apple", AlertRule("price", "above", 200), 210, "msg")
    engine.check.return_value = [alert]
    svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                         stocks=[{"symbol": "AAPL", "name": "Apple", "market": "美股"}])
    svc.run_once()
    notifier.send.assert_called_once_with([alert])

def test_monitor_logs_all_quotes(monitor_setup, caplog):
    src, engine, notifier = monitor_setup
    engine.check.return_value = []
    import logging
    with caplog.at_level(logging.INFO):
        svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                             stocks=[{"symbol": "AAPL", "name": "Apple", "market": "美股"}])
        svc.run_once()
    assert any("AAPL" in r.message for r in caplog.records)


def test_monitor_skips_when_market_closed(monitor_setup):
    src, engine, notifier = monitor_setup
    with patch("src.monitor.is_market_open", return_value=False):
        svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                             stocks=[{"symbol": "AAPL", "name": "Apple", "market": "美股"}])
        svc.run_once()
    src.fetch_quotes.assert_not_called()
    engine.check.assert_not_called()
    notifier.send.assert_not_called()


def test_monitor_fetches_only_open_market_stocks(monitor_setup):
    src, engine, notifier = monitor_setup
    engine.check.return_value = []
    stocks = [
        {"symbol": "159995", "name": "芯片ETF", "market": "A股"},
        {"symbol": "00700", "name": "腾讯控股", "market": "港股"},
        {"symbol": "AAPL", "name": "Apple", "market": "美股"},
    ]
    with patch("src.monitor.is_market_open", side_effect=lambda market: market == "港股"):
        svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                             stocks=stocks)
        svc.run_once()
    src.fetch_quotes.assert_called_once_with([stocks[1]])

def test_monitor_reloads_watchlist_each_cycle(monitor_setup):
    src, engine, notifier = monitor_setup
    engine.check.return_value = []
    first = [{"symbol": "AAPL", "name": "Apple", "market": "美股"}]
    second = [{"symbol": "00700", "name": "腾讯控股", "market": "港股"}]
    stocks_loader = MagicMock(side_effect=[first, second])
    svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                         stocks=first, stocks_loader=stocks_loader)

    svc.run_once()
    svc.run_once()

    assert src.fetch_quotes.call_args_list[0].args[0] == first
    assert src.fetch_quotes.call_args_list[1].args[0] == second


def test_monitor_reloads_alert_rules_each_cycle(monitor_setup):
    src, engine, notifier = monitor_setup
    engine.check.return_value = []
    rules1 = [{"symbol": "AAPL", "field": "price", "op": "above", "value": 200}]
    rules2 = [{"symbol": "AAPL", "field": "price", "op": "below", "value": 100}]
    rules_loader = MagicMock(side_effect=[rules1, rules2])
    svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                         stocks=[{"symbol": "AAPL", "name": "Apple", "market": "美股"}],
                         rules_loader=rules_loader)

    svc.run_once()
    svc.run_once()

    assert engine.set_rules.call_args_list[0].args[0] == rules1
    assert engine.set_rules.call_args_list[1].args[0] == rules2


def test_monitor_keeps_previous_watchlist_when_reload_fails(monitor_setup):
    src, engine, notifier = monitor_setup
    engine.check.return_value = []
    original = [{"symbol": "AAPL", "name": "Apple", "market": "美股"}]
    stocks_loader = MagicMock(side_effect=Exception("bad yaml"))
    svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                         stocks=original, stocks_loader=stocks_loader)

    svc.run_once()

    src.fetch_quotes.assert_called_once_with(original)

def test_monitor_sends_trade_messages(monitor_setup):
    src, engine, notifier = monitor_setup
    engine.check.return_value = []
    trade = MagicMock(message="trade filled")
    trading_service = MagicMock()
    trading_service.process.return_value = [trade]
    svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                         stocks=[{"symbol": "AAPL", "name": "Apple", "market": "美股"}],
                         trading_service=trading_service)

    svc.run_once()

    trading_service.process.assert_called_once()
    notifier.send.assert_any_call([trade])


def test_monitor_reloads_strategies_and_app_config(monitor_setup):
    src, engine, notifier = monitor_setup
    engine.check.return_value = []
    trading_service = MagicMock()
    trading_service.process.return_value = []
    strategies_loader = MagicMock(return_value=[{"id": "s1"}])
    app_config_loader = MagicMock(return_value={"paper_trading": {"enabled": True}})
    svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                         stocks=[{"symbol": "AAPL", "name": "Apple", "market": "美股"}],
                         trading_service=trading_service,
                         strategies_loader=strategies_loader,
                         app_config_loader=app_config_loader)

    svc.run_once()

    trading_service.set_strategies.assert_called_once_with([{"id": "s1"}])
    trading_service.apply_config.assert_called_once_with({"paper_trading": {"enabled": True}})


def test_monitor_saves_daily_index_snapshots_before_stock_alerts(monitor_setup):
    src, engine, notifier = monitor_setup
    index_quote = Quote(".DJI", "道琼斯工业平均指数", "美股", 52210.0, 0.51, 1000)
    stock_quote = Quote("AAPL", "Apple", "美股", 210, 1.0, 100)
    src.fetch_quotes.side_effect = [[index_quote], [stock_quote]]
    engine.check.return_value = []
    store = MagicMock()
    store.index_snapshot_exists.return_value = False
    indices = [{"symbol": ".DJI", "name": "道琼斯工业平均指数", "market": "美股", "sina_symbol": "gb_dji"}]
    stocks = [{"symbol": "AAPL", "name": "Apple", "market": "美股"}]

    svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                         stocks=stocks, index_store=store, market_indices=indices)
    svc.run_once()

    assert src.fetch_quotes.call_args_list[0].args[0] == indices
    assert src.fetch_quotes.call_args_list[1].args[0] == stocks
    saved_quotes, snapshot_dates = store.save_index_snapshots.call_args.args
    assert saved_quotes == [index_quote]
    assert ".DJI" in snapshot_dates
    engine.check.assert_called_once_with([stock_quote])


def test_monitor_updates_existing_daily_index_snapshot_each_cycle(monitor_setup):
    src, engine, notifier = monitor_setup
    index_quote = Quote(".DJI", "道琼斯工业平均指数", "美股", 52210.0, 0.51, 1000)
    stock_quote = Quote("AAPL", "Apple", "美股", 210, 1.0, 100)
    src.fetch_quotes.side_effect = [[index_quote], [stock_quote]]
    engine.check.return_value = []
    store = MagicMock()
    indices = [{"symbol": ".DJI", "name": "道琼斯工业平均指数", "market": "美股", "sina_symbol": "gb_dji"}]
    stocks = [{"symbol": "AAPL", "name": "Apple", "market": "美股"}]

    svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                         stocks=stocks, index_store=store, market_indices=indices)
    svc.run_once()

    assert src.fetch_quotes.call_args_list[0].args[0] == indices
    assert src.fetch_quotes.call_args_list[1].args[0] == stocks
    store.save_index_snapshots.assert_called_once()

def test_monitor_updates_market_indices_on_each_run_once_cycle(monitor_setup):
    src, engine, notifier = monitor_setup
    first_index = Quote("HSI", "恒生指数", "港股", 25300.0, 0.4, 1000)
    first_stock = Quote("00700", "腾讯控股", "港股", 450.0, 1.0, 100)
    second_index = Quote("HSI", "恒生指数", "港股", 25350.0, 0.6, 2000)
    second_stock = Quote("00700", "腾讯控股", "港股", 451.0, 1.1, 200)
    src.fetch_quotes.side_effect = [[first_index], [first_stock], [second_index], [second_stock]]
    engine.check.return_value = []
    store = MagicMock()
    indices = [{"symbol": "HSI", "name": "恒生指数", "market": "港股", "sina_symbol": "hkHSI"}]
    stocks = [{"symbol": "00700", "name": "腾讯控股", "market": "港股"}]

    svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                         stocks=stocks, index_store=store, market_indices=indices)
    svc.run_once()
    svc.run_once()

    assert src.fetch_quotes.call_args_list[0].args[0] == indices
    assert src.fetch_quotes.call_args_list[1].args[0] == stocks
    assert src.fetch_quotes.call_args_list[2].args[0] == indices
    assert src.fetch_quotes.call_args_list[3].args[0] == stocks
    assert store.save_index_snapshots.call_count == 2