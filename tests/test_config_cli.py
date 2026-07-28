import json
from pathlib import Path
from src import config_cli


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


def test_config_cli_import_and_list(tmp_path, monkeypatch, capsys):
    write_configs(tmp_path)
    monkeypatch.setattr(config_cli, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config_cli, "CONFIG_DIR", tmp_path / "config")

    args = config_cli.build_parser().parse_args(["import-yaml", "--replace"])
    args.func(args)
    imported = json.loads(capsys.readouterr().out)
    assert imported["watchlist_imported"] == 1
    assert imported["alert_rules_imported"] == 1

    args = config_cli.build_parser().parse_args(["list-watchlist"])
    args.func(args)
    watchlist = json.loads(capsys.readouterr().out)
    assert watchlist[0]["symbol"] == "SOXL"

    args = config_cli.build_parser().parse_args(["list-alerts"])
    args.func(args)
    alerts = json.loads(capsys.readouterr().out)
    assert alerts[0]["symbol"] == "SOXL"


def test_config_cli_add_and_disable_stock(tmp_path, monkeypatch, capsys):
    write_configs(tmp_path)
    monkeypatch.setattr(config_cli, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config_cli, "CONFIG_DIR", tmp_path / "config")

    args = config_cli.build_parser().parse_args([
        "add-stock", "--symbol", "00700", "--name", "腾讯控股", "--market", "港股"
    ])
    args.func(args)
    assert json.loads(capsys.readouterr().out)["ok"] is True

    args = config_cli.build_parser().parse_args(["disable-stock", "--symbol", "00700"])
    args.func(args)
    assert json.loads(capsys.readouterr().out)["enabled"] is False

    args = config_cli.build_parser().parse_args(["list-watchlist", "--all"])
    args.func(args)
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["enabled"] == 0


def test_config_cli_add_and_disable_alert(tmp_path, monkeypatch, capsys):
    write_configs(tmp_path)
    monkeypatch.setattr(config_cli, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config_cli, "CONFIG_DIR", tmp_path / "config")

    args = config_cli.build_parser().parse_args([
        "add-alert", "--symbol", "SOXL", "--field", "price", "--op", "above", "--value", "100"
    ])
    args.func(args)
    rule_id = json.loads(capsys.readouterr().out)["rule_id"]

    args = config_cli.build_parser().parse_args(["disable-alert", "--rule-id", rule_id])
    args.func(args)
    assert json.loads(capsys.readouterr().out)["enabled"] is False
