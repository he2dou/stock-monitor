from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.paper_trading import MARKET_CURRENCY, execute_manual_order
from src.web.auth import ensure_login
from src.web.deps import get_store, pop_flash, render, set_flash
from src.web import queries

router = APIRouter(prefix="/orders", dependencies=[Depends(ensure_login)])

_SIDE_LABELS = {"buy": "买入", "sell": "卖出"}


def _latest_quote(store, symbol: str) -> dict | None:
    if not symbol:
        return None
    row = store.conn.execute(
        """
        SELECT symbol, name, market, price
        FROM quote_snapshots
        WHERE symbol = ?
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    return dict(row) if row else None


def _position_for_defaults(store, symbol: str) -> dict | None:
    if not symbol:
        return None
    row = store.conn.execute(
        "SELECT * FROM positions WHERE symbol = ? ORDER BY quantity DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    return dict(row) if row else None


def _watchlist_item(store, symbol: str) -> dict | None:
    if not symbol:
        return None
    for item in store.load_watchlist(include_disabled=True):
        if item["symbol"] == symbol:
            return item
    return None


def _symbol_index(store) -> dict[str, dict]:
    options: dict[str, dict] = {}

    for position in queries.positions(store, include_zero=True):
        symbol = str(position.get("symbol") or "").strip()
        if not symbol:
            continue
        market = position.get("market") or "美股"
        options[symbol] = {
            "symbol": symbol,
            "name": position.get("name") or symbol,
            "market": market,
            "currency": position.get("currency") or MARKET_CURRENCY.get(market, "CNY"),
            "price": position.get("current_price"),
            "current_price": position.get("current_price"),
            "quantity": position.get("quantity"),
            "avg_cost": position.get("avg_cost"),
            "unrealized_pnl": position.get("unrealized_pnl"),
            "pnl_pct": position.get("pnl_pct"),
        }

    for item in store.load_watchlist(include_disabled=True):
        symbol = str(item.get("symbol") or "").strip()
        if not symbol:
            continue
        market = item.get("market") or "美股"
        entry = options.setdefault(
            symbol,
            {
                "symbol": symbol,
                "name": item.get("name") or symbol,
                "market": market,
                "currency": MARKET_CURRENCY.get(market, "CNY"),
                "price": None,
                "current_price": None,
                "quantity": None,
                "avg_cost": None,
                "unrealized_pnl": None,
                "pnl_pct": None,
            },
        )
        entry["name"] = entry.get("name") or item.get("name") or symbol
        entry["market"] = entry.get("market") or market

    for quote in queries.latest_quotes(store, limit=500):
        symbol = str(quote.get("symbol") or "").strip()
        if not symbol:
            continue
        market = quote.get("market") or "美股"
        entry = options.setdefault(
            symbol,
            {
                "symbol": symbol,
                "name": quote.get("name") or symbol,
                "market": market,
                "currency": MARKET_CURRENCY.get(market, "CNY"),
                "price": quote.get("price"),
                "current_price": quote.get("price"),
                "quantity": None,
                "avg_cost": None,
                "unrealized_pnl": None,
                "pnl_pct": None,
            },
        )
        entry["name"] = entry.get("name") or quote.get("name") or symbol
        entry["market"] = entry.get("market") or market
        if entry.get("price") is None:
            entry["price"] = quote.get("price")
        if entry.get("current_price") is None:
            entry["current_price"] = quote.get("price")

    return options


def _symbol_options(store) -> list[dict]:
    index = _symbol_index(store)
    return sorted(index.values(), key=lambda item: (item["market"], item["symbol"]))


def _resolve_symbol_metadata(store, symbol: str) -> dict | None:
    symbol = symbol.strip()
    if not symbol:
        return None
    metadata = _symbol_index(store).get(symbol)
    if metadata is not None:
        return metadata

    position = _position_for_defaults(store, symbol)
    quote = _latest_quote(store, symbol)
    watchlist_item = _watchlist_item(store, symbol)
    market = (
        (position or {}).get("market")
        or (quote or {}).get("market")
        or (watchlist_item or {}).get("market")
    )
    if not market:
        return None
    return {
        "symbol": symbol,
        "market": market,
        "name": (position or {}).get("name") or (quote or {}).get("name") or (watchlist_item or {}).get("name") or symbol,
        "currency": (position or {}).get("currency") or MARKET_CURRENCY.get(market, "CNY"),
        "price": (quote or {}).get("price"),
        "current_price": (quote or {}).get("price"),
        "quantity": (position or {}).get("quantity"),
        "avg_cost": (position or {}).get("avg_cost"),
        "unrealized_pnl": (position or {}).get("unrealized_pnl"),
        "pnl_pct": (position or {}).get("pnl_pct"),
    }


def _order_defaults(request: Request, store) -> dict:
    params = request.query_params
    side = params.get("side", "buy").strip().lower()
    if side not in _SIDE_LABELS:
        side = "buy"

    symbol_options = _symbol_options(store)
    symbol = params.get("symbol", "").strip() or (symbol_options[0]["symbol"] if symbol_options else "")
    metadata = _resolve_symbol_metadata(store, symbol) if symbol else None
    price = params.get("price", "").strip() or (
        str(metadata["price"]) if metadata and metadata.get("price") is not None else ""
    )
    quantity = params.get("quantity", "").strip()
    if not quantity and side == "sell" and metadata and int(metadata.get("quantity") or 0) > 0:
        quantity = str(metadata["quantity"])

    return {
        "side": side,
        "symbol": symbol,
        "quantity": quantity,
        "price": price,
        "symbol_options": symbol_options,
        "selected_symbol": metadata,
    }


def _orders_redirect(**params: str) -> RedirectResponse:
    clean = {key: value for key, value in params.items() if value}
    suffix = f"?{urlencode(clean)}" if clean else ""
    return RedirectResponse(f"/orders{suffix}", status_code=303)


@router.get("")
async def orders_page(request: Request):
    store = get_store(request)
    return render(
        request,
        "orders.html",
        "委托",
        order_defaults=_order_defaults(request, store),
        side_labels=_SIDE_LABELS,
        orders=queries.orders(store, limit=200),
        fills=queries.fills(store, limit=200),
        signals=queries.signals(store, limit=100),
        flash=pop_flash(request),
    )


@router.post("/submit")
async def submit_order(
    request: Request,
    side: str = Form(...),
    symbol: str = Form(...),
    quantity: int = Form(...),
    price: float = Form(...),
):
    store = get_store(request)
    side = side.strip().lower()
    symbol = symbol.strip()
    metadata = _resolve_symbol_metadata(store, symbol)

    if (
        side not in _SIDE_LABELS
        or not symbol
        or quantity <= 0
        or price <= 0
        or metadata is None
    ):
        set_flash(request, "委托参数无效，请检查方向、代码、数量和价格")
        return _orders_redirect(
            side=side,
            symbol=symbol,
            quantity=str(quantity),
            price=str(price),
        )

    execution = execute_manual_order(
        store,
        side=side,
        market=metadata["market"],
        symbol=symbol,
        name=metadata["name"],
        quantity=quantity,
        price=price,
        currency=metadata["currency"],
    )
    side_label = _SIDE_LABELS[side]
    if execution.status == "FILLED":
        set_flash(
            request,
            f"委托已成交：{side_label} {symbol} {quantity} 股，成交价 {execution.price:.4f}",
        )
    else:
        set_flash(request, f"委托已拒绝：{execution.reason}")
    return RedirectResponse("/orders", status_code=303)