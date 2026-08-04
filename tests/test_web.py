from pathlib import Path

from fastapi.testclient import TestClient

from src.models import Quote
from src.trading_store import TradingStore
from src.web.app import create_app

VALID_STRATEGY = """strategies:
  - id: test_buy
    enabled: true
    symbol: SOXL
    action: buy
    trigger:
      field: change_pct
      op: below
      value: -10
    sizing:
      type: fixed_amount
      amount: 1000
      currency: USD
      lot_size: 1
    constraints:
      cooldown_minutes: 300
      max_position_amount: 5000
"""

INVALID_STRATEGY = """strategies:
  - id: bad
    enabled: true
    symbol: SOXL
    action: buy
    sizing:
      type: not_a_real_type
      amount: 1000
"""


def _write_config(config_dir: Path, db_path: str, password: str = ""):
    config_dir.mkdir(parents=True, exist_ok=True)
    # Use single-quoted YAML scalars so Windows backslash paths stay literal.
    pw = password if password else ""
    (config_dir / "config.yaml").write_text(
        f"paper_trading:\n  db_path: '{db_path}'\n"
        f"web:\n  secret_key: 'test-secret'\n  admin_password: '{pw}'\n",
        encoding="utf-8",
    )
    (config_dir / "strategies.yaml").write_text("strategies: []\n", encoding="utf-8")


def _make_app(tmp_path, password: str = ""):
    db = str(tmp_path / "t.sqlite3")
    config_dir = tmp_path / "config"
    _write_config(config_dir, db, password)
    store = TradingStore(db)
    store.import_watchlist([{"symbol": "SOXL", "name": "SOXL", "market": "美股"}], replace=True)
    store.save_quote_snapshots(
        [Quote("SOXL", "SOXL", "美股", 100.0, -1.0, 1000, timestamp="2026-08-04T10:00:00+08:00")]
    )
    app = create_app(store=store, config_dir=config_dir)
    return app, store


def test_pages_render(tmp_path):
    app, store = _make_app(tmp_path)
    client = TestClient(app)
    for path in ["/", "/watchlist", "/alerts", "/markets", "/portfolio",
                 "/strategies", "/backtest", "/ops", "/markets/history/SOXL"]:
        r = client.get(path, follow_redirects=True)
        assert r.status_code == 200, (path, r.status_code, r.text[:300])
    store.close()


def test_watchlist_crud(tmp_path):
    app, store = _make_app(tmp_path)
    client = TestClient(app)
    r = client.post("/watchlist/add", data={
        "symbol": "00700", "name": "腾讯", "market": "港股", "enabled": "1"},
        follow_redirects=False)
    assert r.status_code == 303
    assert any(s["symbol"] == "00700" for s in store.load_watchlist(include_disabled=True))

    r = client.post("/watchlist/00700/toggle", follow_redirects=False)
    assert r.status_code == 303
    row = [s for s in store.load_watchlist(include_disabled=True) if s["symbol"] == "00700"][0]
    assert row["enabled"] == 0

    r = client.post("/watchlist/00700/delete", follow_redirects=False)
    assert r.status_code == 303
    assert not any(s["symbol"] == "00700" for s in store.load_watchlist(include_disabled=True))
    store.close()


def test_alert_crud(tmp_path):
    app, store = _make_app(tmp_path)
    client = TestClient(app)
    r = client.post("/alerts/add", data={
        "symbol": "SOXL", "field": "price", "op": "above", "value": "100"},
        follow_redirects=False)
    assert r.status_code == 303
    rules = store.load_alert_rules(include_disabled=True)
    assert any(rule["symbol"] == "SOXL" for rule in rules)
    rid = rules[0]["rule_id"]

    r = client.post(f"/alerts/{rid}/toggle", follow_redirects=False)
    assert r.status_code == 303
    assert store.load_alert_rules(include_disabled=True)[0]["enabled"] == 0

    r = client.post(f"/alerts/{rid}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert not store.load_alert_rules(include_disabled=True)
    store.close()


def test_strategies_save_valid_and_invalid(tmp_path):
    app, store = _make_app(tmp_path)
    client = TestClient(app)
    r = client.post("/strategies/save", data={"content": INVALID_STRATEGY},
                    follow_redirects=False)
    assert r.status_code == 400

    r = client.post("/strategies/save", data={"content": VALID_STRATEGY},
                    follow_redirects=False)
    assert r.status_code == 303
    path = app.state.config_dir / "strategies.yaml"
    assert "test_buy" in path.read_text(encoding="utf-8")
    store.close()


def test_auth_redirect_and_login(tmp_path):
    app, store = _make_app(tmp_path, password="secret")
    client = TestClient(app)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"

    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 200

    r = client.post("/login", data={"password": "wrong"}, follow_redirects=False)
    assert r.status_code == 401

    r = client.post("/login", data={"password": "secret"}, follow_redirects=False)
    assert r.status_code == 303

    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    store.close()


def test_ops_update_snapshots(tmp_path, monkeypatch):
    app, store = _make_app(tmp_path)
    client = TestClient(app)

    from src.service import ops as ops_mod
    from src.models import Quote

    captured = {}

    class FakeSource:
        def fetch_quotes(self, items):
            captured["items"] = items
            return [Quote("SOXL", "SOXL", "美股", 101.0, 0.5, 2000,
                          timestamp="2026-08-04T11:00:00+08:00")]

    monkeypatch.setattr(ops_mod, "SinaTxSource", lambda: FakeSource())
    monkeypatch.setattr(ops_mod, "is_market_open", lambda market: True)
    monkeypatch.setattr(ops_mod, "load_market_indices", lambda cfg: [])

    r = client.post("/ops/update-snapshots", data={"target": "stock"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert captured["items"]
    rows = store.conn.execute(
        "SELECT price FROM quote_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert rows["price"] == 101.0
    store.close()
