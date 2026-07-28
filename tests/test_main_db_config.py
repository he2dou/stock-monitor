from pathlib import Path
from unittest.mock import patch
import src.main as main_mod


def write_configs(root: Path):
    config_dir = root / "config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("""
paper_trading:
  db_path: "data/test.sqlite3"
  accounts:
    USD: 1000
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
    (config_dir / "strategies.yaml").write_text("strategies: []\n", encoding="utf-8")


def patch_paths(monkeypatch, root):
    monkeypatch.setattr(main_mod, "BASE_DIR", root)
    monkeypatch.setattr(main_mod, "CONFIG_DIR", root / "config")


def test_build_store_seeds_yaml_into_sqlite(tmp_path, monkeypatch):
    write_configs(tmp_path)
    patch_paths(monkeypatch, tmp_path)
    store = main_mod.build_store()
    try:
        assert store.load_watchlist()[0]["symbol"] == "SOXL"
        assert store.load_alert_rules()[0]["symbol"] == "SOXL"
    finally:
        store.close()


def test_build_monitor_uses_db_runtime_loaders(tmp_path, monkeypatch):
    write_configs(tmp_path)
    patch_paths(monkeypatch, tmp_path)
    with patch.object(main_mod, "SinaTxSource"):
        monitor = main_mod.build_monitor()
    try:
        assert monitor.stocks[0]["symbol"] == "SOXL"
        assert monitor.engine.rules["SOXL"][0].value == -10
        monitor.trading_service.store.add_stock("AAPL", "Apple", "美股")
        monitor._reload_runtime_config()
        assert {s["symbol"] for s in monitor.stocks} == {"SOXL", "AAPL"}
    finally:
        monitor.trading_service.store.close()
