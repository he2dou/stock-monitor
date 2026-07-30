import json
import pytest
from unittest.mock import patch

import pandas as pd
from pathlib import Path
from src import cli
from src.models import Quote


def write_configs(root: Path):
    config_dir = root / "config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("""
paper_trading:
  db_path: "data/test.sqlite3"
""", encoding="utf-8")
    (config_dir / "watchlist.yaml").write_text("""
stocks:
  - symbol: "SOXL"
    name: "半导体ETF"
    market: "美股"
""", encoding="utf-8")
    (config_dir / "alerts.yaml").write_text("""
rules:
  - symbol: "SOXL"
    field: "change_pct"
    op: "below"
    value: -10
""", encoding="utf-8")
    (config_dir / "strategies.yaml").write_text("""
strategies:
  - id: "soxl_drop_buy"
    enabled: true
    symbol: "SOXL"
    action: "buy"
    trigger:
      field: "change_pct"
      op: "below"
      value: -10
    sizing:
      type: "fixed_amount"
      amount: 1000
      currency: "USD"
      lot_size: 1
    constraints:
      cooldown_minutes: 300
      max_position_amount: 5000
""", encoding="utf-8")


def test_cli_import_and_list(tmp_path, monkeypatch, capsys):
    write_configs(tmp_path)
    monkeypatch.setattr(cli, "BASE_DIR", tmp_path)
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / "config")

    args = cli.build_parser().parse_args(["import-yaml", "--replace"])
    args.func(args)
    imported = json.loads(capsys.readouterr().out)
    assert imported["watchlist_imported"] == 1
    assert imported["alert_rules_imported"] == 1

    args = cli.build_parser().parse_args(["list-watchlist"])
    args.func(args)
    watchlist = json.loads(capsys.readouterr().out)
    assert watchlist[0]["symbol"] == "SOXL"

    args = cli.build_parser().parse_args(["list-alerts"])
    args.func(args)
    alerts = json.loads(capsys.readouterr().out)
    assert alerts[0]["symbol"] == "SOXL"


def test_cli_add_and_disable_stock(tmp_path, monkeypatch, capsys):
    write_configs(tmp_path)
    monkeypatch.setattr(cli, "BASE_DIR", tmp_path)
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / "config")

    args = cli.build_parser().parse_args([
        "add-stock", "--symbol", "00700", "--name", "腾讯控股", "--market", "港股"
    ])
    args.func(args)
    assert json.loads(capsys.readouterr().out)["ok"] is True

    args = cli.build_parser().parse_args(["disable-stock", "--symbol", "00700"])
    args.func(args)
    assert json.loads(capsys.readouterr().out)["enabled"] is False

    args = cli.build_parser().parse_args(["list-watchlist", "--all"])
    args.func(args)
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["enabled"] == 0

    args = cli.build_parser().parse_args(["del-stock", "--symbol", "00700"])
    args.func(args)
    assert json.loads(capsys.readouterr().out)["deleted"] == 1

    args = cli.build_parser().parse_args(["list-watchlist", "--all"])
    args.func(args)
    assert json.loads(capsys.readouterr().out) == []


def test_cli_add_and_disable_alert(tmp_path, monkeypatch, capsys):
    write_configs(tmp_path)
    monkeypatch.setattr(cli, "BASE_DIR", tmp_path)
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / "config")

    args = cli.build_parser().parse_args([
        "add-alert", "--symbol", "SOXL", "--field", "price", "--op", "above", "--value", "100"
    ])
    args.func(args)
    rule_id = json.loads(capsys.readouterr().out)["rule_id"]

    args = cli.build_parser().parse_args(["disable-alert", "--rule-id", rule_id])
    args.func(args)
    assert json.loads(capsys.readouterr().out)["enabled"] is False

    args = cli.build_parser().parse_args(["del-alert", "--rule-id", rule_id])
    args.func(args)
    assert json.loads(capsys.readouterr().out)["deleted"] == 1


def test_cli_lists_index_snapshots(tmp_path, monkeypatch, capsys):
    write_configs(tmp_path)
    monkeypatch.setattr(cli, "BASE_DIR", tmp_path)
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / "config")
    store = cli.default_store()
    store.save_index_snapshots(
        [Quote("HSI", "恒生指数", "港股", 25320.0, 0.44, 1000, timestamp="2026-07-28T07:30:00Z")],
        {"HSI": "2026-07-28"},
    )
    store.close()

    args = cli.build_parser().parse_args(["list-index-snapshots", "--from", "2026-07-28", "--to", "2026-07-28"])
    args.func(args)
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "HSI"
    assert rows[0]["snapshot_date"] == "2026-07-28"

def test_cli_list_index_snapshots_with_backfill(tmp_path, monkeypatch, capsys):
    write_configs(tmp_path)
    monkeypatch.setattr(cli, "BASE_DIR", tmp_path)
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / "config")
    df = pd.DataFrame({
        "date": ["2026-06-30", "2026-07-01"],
        "open": [90, 100],
        "close": [100, 110],
        "high": [101, 111],
        "low": [89, 98],
        "amount": [1000, 2000],
    })
    with patch("src.index_history.ak.stock_zh_index_daily_tx", return_value=df):
        args = cli.build_parser().parse_args([
            "list-index-snapshots", "--from", "2026-07-01", "--to", "2026-07-01", "--backfill"
        ])
        args.func(args)

    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 9
    assert {r["snapshot_date"] for r in rows} == {"2026-07-01"}

def test_cli_update_stock_snapshots(tmp_path, monkeypatch, capsys):
    write_configs(tmp_path)
    monkeypatch.setattr(cli, "BASE_DIR", tmp_path)
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / "config")
    store = cli.default_store()
    store.import_watchlist([{"symbol": "SOXL", "name": "半导体ETF", "market": "美股"}], replace=True)
    store.close()

    source = patch("src.cli.SinaTxSource").start()
    source.return_value.fetch_quotes.return_value = [
        Quote("SOXL", "半导体ETF", "美股", 100.0, -11.0, 1000, timestamp="2026-07-29T14:00:00+08:00")
    ]
    hours = patch("src.cli.is_market_open", return_value=True).start()
    try:
        args = cli.build_parser().parse_args([
            "update-snapshots", "--target", "stock", "--symbol", "SOXL"
        ])
        args.func(args)
    finally:
        patch.stopall()

    result = json.loads(capsys.readouterr().out)
    assert result["target"] == "stock"
    assert result["selected"] == 1
    assert result["saved"] == 1
    assert result["updated_symbols"] == ["SOXL"]
    assert hours.called
    store = cli.default_store()
    rows = store.conn.execute("SELECT symbol, price FROM quote_snapshots").fetchall()
    assert [dict(r) for r in rows] == [{"symbol": "SOXL", "price": 100.0}]
    store.close()


def test_cli_update_index_snapshots(tmp_path, monkeypatch, capsys):
    write_configs(tmp_path)
    monkeypatch.setattr(cli, "BASE_DIR", tmp_path)
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(cli, "load_market_indices", lambda config: [
        {"symbol": "HSI", "name": "恒生指数", "market": "港股", "sina_symbol": "hkHSI"}
    ])
    monkeypatch.setattr(cli, "is_market_open", lambda market: market == "港股")

    source = patch("src.cli.SinaTxSource").start()
    source.return_value.fetch_quotes.return_value = [
        Quote("HSI", "恒生指数", "港股", 25320.0, 0.44, 1000, timestamp="2026-07-29T14:00:00+08:00")
    ]
    try:
        args = cli.build_parser().parse_args([
            "update-snapshots", "--target", "index", "--market", "港股"
        ])
        args.func(args)
    finally:
        patch.stopall()

    result = json.loads(capsys.readouterr().out)
    assert result["target"] == "index"
    assert result["selected"] == 1
    assert result["requested"] == 1
    assert result["saved"] == 1
    assert result["updated_symbols"] == ["HSI"]
    store = cli.default_store()
    rows = store.load_index_snapshots(start="2026-07-29", end="2026-07-29")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "HSI"
    assert rows[0]["price"] == 25320.0
    store.close()


def test_cli_update_snapshots_skips_closed_market(tmp_path, monkeypatch, capsys):
    write_configs(tmp_path)
    monkeypatch.setattr(cli, "BASE_DIR", tmp_path)
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / "config")
    store = cli.default_store()
    store.import_watchlist([{"symbol": "SOXL", "name": "半导体ETF", "market": "美股"}], replace=True)
    store.close()
    monkeypatch.setattr(cli, "is_market_open", lambda market: False)

    source = patch("src.cli.SinaTxSource").start()
    try:
        args = cli.build_parser().parse_args([
            "update-snapshots", "--target", "stock", "--symbol", "SOXL"
        ])
        args.func(args)
    finally:
        patch.stopall()

    result = json.loads(capsys.readouterr().out)
    assert result["requested"] == 0
    assert result["saved"] == 0
    assert result["skipped_closed"] == ["SOXL"]
    source.return_value.fetch_quotes.assert_not_called()
