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


def test_strategies_create_bind_unbind(tmp_path):
    app, store = _make_app(tmp_path)
    client = TestClient(app)

    # Create a threshold strategy via structured form fields
    r = client.post("/strategies/create", data={
        "strategy_id": "test_thr", "strategy_type": "threshold",
        "action": "buy", "enabled": "1",
        "trigger_field": "price", "trigger_op": "above", "trigger_value": "100",
    }, follow_redirects=False)
    assert r.status_code == 303
    items = [s for s in store.load_strategies() if s.get("id") == "test_thr"]
    assert items and items[0]["type"] == "threshold"

    # Bind the strategy to SOXL (which is in the watchlist)
    r = client.post("/strategies/bind", data={
        "strategy_id": "test_thr", "symbol": "SOXL",
    }, follow_redirects=False)
    assert r.status_code == 303
    runtime = store.load_runtime_strategies()
    assert any(rt["id"] == "test_thr" and rt["symbol"] == "SOXL" for rt in runtime)

    # Unbind
    r = client.post("/strategies/unbind", data={
        "strategy_id": "test_thr", "symbol": "SOXL",
    }, follow_redirects=False)
    assert r.status_code == 303
    runtime = store.load_runtime_strategies()
    assert not any(rt["id"] == "test_thr" for rt in runtime)

    # Delete
    r = client.post("/strategies/delete", data={"strategy_id": "test_thr"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert not any(s.get("id") == "test_thr" for s in store.load_strategies())
    store.close()


def test_strategies_bind_unknown_symbol_rejected(tmp_path):
    app, store = _make_app(tmp_path)
    client = TestClient(app)
    client.post("/strategies/create", data={
        "strategy_id": "t2", "strategy_type": "threshold",
        "action": "buy", "enabled": "1",
        "trigger_field": "price", "trigger_op": "above", "trigger_value": "100",
    })
    r = client.post("/strategies/bind", data={
        "strategy_id": "t2", "symbol": "ZZZZZ",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert not store.load_strategy_bindings()
    store.close()

def test_backtest_history_saved_and_listed(tmp_path):
    app, store = _make_app(tmp_path)
    client = TestClient(app)

    # Directly save two backtest runs via the store
    store.save_backtest_run(
        {"strategy_id": "s1", "symbol": "SOXL", "start": "2026-01-01",
         "end": "2026-06-01", "source": "quote-snapshots",
         "next_bar": True, "apply_costs": False},
        {"total_return_pct": 12.5, "max_drawdown_pct": -5.0,
         "sell_win_rate_pct": 60.0, "sharpe_ratio": 1.2, "fills": 10,
         "avg_r_multiple": 1.5, "realized_pnl": 500.0,
         "starting_equity": 10000.0, "ending_total_equity": 11250.0},
    )
    store.save_backtest_run(
        {"strategy_id": "", "symbol": "", "start": "",
         "end": "", "source": "daily-bars",
         "next_bar": False, "apply_costs": True},
        {"total_return_pct": -3.0, "max_drawdown_pct": -8.0,
         "sell_win_rate_pct": None, "sharpe_ratio": None, "fills": 2,
         "avg_r_multiple": None, "realized_pnl": -150.0,
         "starting_equity": 10000.0, "ending_total_equity": 9850.0},
    )

    runs = store.load_backtest_runs()
    assert len(runs) == 2
    # Newest first
    assert runs[0]["source"] == "daily-bars"
    assert runs[1]["strategy_id"] == "s1"
    assert runs[1]["total_return_pct"] == 12.5

    # History shows up on the page
    r = client.get("/backtest")
    assert r.status_code == 200
    assert "s1" in r.text
    assert "daily-bars" in r.text

    # Delete the first (newest) run
    rid = runs[0]["id"]
    r = client.post("/backtest/delete", data={"run_id": str(rid)},
                    follow_redirects=False)
    assert r.status_code == 303
    assert len(store.load_backtest_runs()) == 1
    store.close()

def test_auth_redirect_and_login(tmp_path):
    app, store = _make_app(tmp_path, password="secret")
    client = TestClient(app)

    # Unauthenticated request redirects to login
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"

    # GET login page to obtain CSRF token
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 200
    import re as _re
    csrf_match = _re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    assert csrf_match, "CSRF token not in login page"
    csrf = csrf_match.group(1)

    # POST login with wrong password + CSRF -> 401
    r = client.post("/login", data={"password": "wrong", "csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 401

    # Need fresh CSRF token (the one in session may have changed after failed attempt)
    csrf_match2 = _re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    csrf = csrf_match2.group(1) if csrf_match2 else csrf

    # POST login with correct password + CSRF -> 303
    r = client.post("/login", data={"password": "secret", "csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303

    # Authenticated request succeeds
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


from pathlib import Path
import re

from fastapi.testclient import TestClient

from src.models import Quote
from src.trading_store import TradingStore
from src.web.app import create_app
from src.web.auth import hash_password, verify_password


def _write_config_multi(config_dir, db_path, users=None):
    config_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"paper_trading:\n  db_path: '{db_path}'", "web:"]
    lines.append("  secret_key: 'test-secret'")
    if users:
        lines.append("  users:")
        for u in users:
            lines.append(f'    - username: "{u["username"]}"')
            if u.get("password_hash"):
                lines.append(f'      password_hash: "{u["password_hash"]}"')
            if u.get("password"):
                lines.append(f'      password: "{u["password"]}"')
            if u.get("display_name"):
                lines.append(f'      display_name: "{u["display_name"]}"')
    else:
        lines.append('  admin_password: ""')
    (config_dir / "config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (config_dir / "strategies.yaml").write_text("strategies: []\n", encoding="utf-8")


def _make_auth_app(tmp_path, users):
    db = str(tmp_path / "t.sqlite3")
    config_dir = tmp_path / "config"
    _write_config_multi(config_dir, db, users)
    store = TradingStore(db)
    store.import_watchlist([{"symbol": "SOXL", "name": "SOXL", "market": "US"}], replace=True)
    app = create_app(store=store, config_dir=config_dir)
    return app, store


def _get_csrf(client, path="/login"):
    r = client.get(path, follow_redirects=False)
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    return m.group(1) if m else ""


def test_password_hash_and_verify():
    h = hash_password("test123")
    assert h.startswith("pbkdf2$")
    assert verify_password("test123", h)
    assert not verify_password("wrong", h)
    # Legacy plain text
    assert verify_password("secret", "secret")
    assert not verify_password("secret", "other")


def test_multi_user_login(tmp_path):
    h = hash_password("mypass")
    app, store = _make_auth_app(tmp_path, [
        {"username": "alice", "password_hash": h, "display_name": "Alice"},
        {"username": "bob", "password_hash": hash_password("bobpass")},
    ])
    client = TestClient(app)

    # Unauthenticated redirect
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303

    # Login as alice
    csrf = _get_csrf(client)
    r = client.post("/login", data={"username": "alice", "password": "mypass", "csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303

    # Access works
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    store.close()


def test_csrf_rejected(tmp_path):
    h = hash_password("mypass")
    app, store = _make_auth_app(tmp_path, [{"username": "admin", "password_hash": h}])
    client = TestClient(app)

    # POST login without csrf_token -> 403
    r = client.post("/login", data={"username": "admin", "password": "mypass"}, follow_redirects=False)
    assert r.status_code == 403
    store.close()


def test_login_with_wrong_username(tmp_path):
    h = hash_password("mypass")
    app, store = _make_auth_app(tmp_path, [{"username": "admin", "password_hash": h}])
    client = TestClient(app)

    csrf = _get_csrf(client)
    r = client.post("/login", data={"username": "nobody", "password": "mypass", "csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 401
    store.close()


def test_logout(tmp_path):
    h = hash_password("mypass")
    app, store = _make_auth_app(tmp_path, [{"username": "admin", "password_hash": h}])
    client = TestClient(app)

    # Login
    csrf = _get_csrf(client)
    r = client.post("/login", data={"username": "admin", "password": "mypass", "csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303

    # Get fresh csrf for logout
    r = client.get("/", follow_redirects=False)
    csrf2 = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)

    # Logout
    r = client.post("/logout", data={"csrf_token": csrf2}, follow_redirects=False)
    assert r.status_code == 303

    # Now need auth again
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    store.close()


def test_change_password(tmp_path):
    h = hash_password("oldpass")
    app, store = _make_auth_app(tmp_path, [{"username": "admin", "password_hash": h}])
    client = TestClient(app)

    # Login
    csrf = _get_csrf(client)
    r = client.post("/login", data={"username": "admin", "password": "oldpass", "csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303

    # GET change password page
    r = client.get("/change-password", follow_redirects=False)
    assert r.status_code == 200
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)

    # POST change password - wrong old password
    r = client.post("/change-password", data={
        "old_password": "wrong", "new_password": "newpass", "confirm_password": "newpass",
        "csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 401

    # Refresh csrf
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)

    # POST - mismatched new passwords
    r = client.post("/change-password", data={
        "old_password": "oldpass", "new_password": "newpass", "confirm_password": "different",
        "csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 400

    csrf = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)

    # POST - too short
    r = client.post("/change-password", data={
        "old_password": "oldpass", "new_password": "12345", "confirm_password": "12345",
        "csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 400

    csrf = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)

    # POST - success
    r = client.post("/change-password", data={
        "old_password": "oldpass", "new_password": "newpass", "confirm_password": "newpass",
        "csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 200
    assert "success" in r.text.lower() or "\u6210\u529f" in r.text

    # Verify config file was updated
    import yaml
    cfg_path = app.state.config_dir / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    found = False
    for u in cfg.get("web", {}).get("users", []):
        if u.get("username") == "admin":
            assert u.get("password_hash", "").startswith("pbkdf2$")
            assert "password" not in u or u["password"] != "oldpass"
            found = True
    assert found, "admin user not found in updated config"
    store.close()


def test_rate_limit(tmp_path):
    h = hash_password("mypass")
    app, store = _make_auth_app(tmp_path, [{"username": "admin", "password_hash": h}])
    client = TestClient(app)

    # Make 5 failed attempts
    for i in range(5):
        csrf = _get_csrf(client)
        r = client.post("/login", data={"username": "admin", "password": "wrong", "csrf_token": csrf}, follow_redirects=False)
        assert r.status_code == 401

    # 6th attempt should be rate limited (429)
    csrf = _get_csrf(client)
    r = client.post("/login", data={"username": "admin", "password": "mypass", "csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 429
    store.close()


def test_no_auth_open_mode(tmp_path):
    """When no password configured, everything is open."""
    app, store = _make_auth_app(tmp_path, None)
    client = TestClient(app)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 303  # redirect to /
    store.close()
