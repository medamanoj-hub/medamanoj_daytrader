"""Shared helpers for india-budget, india-risk and india-broker.

Each skill folder carries its own copy so any one skill can be installed
alone. Keep the copies identical (tests check this).
"""

import datetime as dt
import hashlib
import json
import math
import os
import sys

TRADER = os.environ.get("TRADER_DIR", "trader")
CONFIG = os.path.join(TRADER, "config.json")
ORDERS = os.path.join(TRADER, "orders.json")
TARGETS = os.path.join(TRADER, "targets.json")
PRICES = os.path.join(TRADER, "prices.json")
PORTFOLIO = os.path.join(TRADER, "portfolio.json")
APPROVALS = os.path.join(TRADER, "approvals.jsonl")

DEFAULTS = {
    "paper": True,
    "broker": "angelone",
    "exchange": "NSE",
    "product": "DELIVERY",
    "budget": 100000,
    "cash_buffer_pct": 10,
    "max_position_pct": 25,
    "min_order_value": 1000,
    "limit_buffer_pct": 0.5,
    "fill_leftover": True,
    "limits": {
        "max_order_value": 50000,
        "max_orders_per_day": 10,
        "price_band_pct": 3,
        "max_price_age_hours": 24,
        "blocked_symbols": [],
        "allow_market_orders": False,
        "allow_non_eq_series": False,
    },
    "charges": {
        "brokerage_pct": 0.1,
        "brokerage_max": 20,
        "brokerage_min": 5,
        "stt_pct": 0.1,
        "stamp_buy_pct": 0.015,
        "exchange_txn_pct": 0.00297,
        "sebi_per_crore": 10,
        "gst_pct": 18,
        "dp_charge_per_sell": 20,
    },
}


def die(msg):
    print("error: " + msg, file=sys.stderr)
    sys.exit(1)


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def load_config(path=None):
    cfg = json.loads(json.dumps(DEFAULTS))
    user = read_json(path or CONFIG, {}) or {}
    for k, v in user.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update({kk: vv for kk, vv in v.items() if not kk.startswith("_")})
        else:
            cfg[k] = v
    return cfg


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def inr(v):
    if v is None:
        return "?"
    return ("-" if v < 0 else "") + "₹{:,.2f}".format(abs(v))


# NSE tick sizes by price (equity cash market, revised 2025). The broker
# skill prefers the exact tick from Angel One's instrument file when present.
TICKS = [(250, 0.01), (1000, 0.05), (5000, 0.10), (10000, 0.50), (20000, 1.00)]


def tick_for(price, tick=None):
    if tick:
        return tick
    for upper, t in TICKS:
        if price < upper:
            return t
    return 5.00


def round_to_tick(price, tick, up=True):
    n = price / tick
    n = math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)
    return round(n * tick, 2)


def charges(value, side, cfg):
    """Estimated statutory + broker charges for one equity-delivery order."""
    c = cfg["charges"]
    brokerage = value * c["brokerage_pct"] / 100
    brokerage = min(brokerage, c["brokerage_max"])
    brokerage = max(brokerage, c["brokerage_min"]) if value > 0 else 0
    stt = value * c["stt_pct"] / 100
    stamp = value * c["stamp_buy_pct"] / 100 if side == "BUY" else 0
    exch = value * c["exchange_txn_pct"] / 100
    sebi = value * c["sebi_per_crore"] / 1e7
    gst = (brokerage + exch + sebi) * c["gst_pct"] / 100
    dp = c["dp_charge_per_sell"] if side == "SELL" else 0
    total = brokerage + stt + stamp + exch + sebi + gst + dp
    return round(total, 2)


def order_hash(o):
    """Fingerprint of the economic fields. If any of these change after the
    risk check or the user's yes, the order must be checked again."""
    key = "|".join(str(o.get(k)) for k in
                   ("symbol", "exchange", "side", "qty", "order_type", "limit_price", "product"))
    return hashlib.sha1(key.encode()).hexdigest()[:12]


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/%s.NS?range=5d&interval=1d"


def fetch_yahoo(symbols):
    """Last price per NSE symbol from Yahoo Finance (unofficial, may lag)."""
    import urllib.request
    out = {}
    for s in symbols:
        req = urllib.request.Request(YAHOO % s, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                out[s] = float(json.load(r)["chart"]["result"][0]["meta"]["regularMarketPrice"])
        except Exception as e:  # noqa: BLE001
            print("warning: no price for %s (%s)" % (s, e), file=sys.stderr)
    return out


def load_prices(path=None, fetch=False, symbols=()):
    """(prices, as_of). From a file ({"prices": {...}, "as_of": ...} or a flat
    {symbol: price}), from Yahoo with fetch=True (also saved to
    trader/prices.json), or from trader/prices.json."""
    if fetch:
        raw = {"prices": fetch_yahoo([s.upper() for s in symbols]), "as_of": now_iso(), "source": "yahoo"}
        write_json(PRICES, raw)
    else:
        raw = read_json(path or PRICES, {}) or {}
    as_of = raw.get("as_of") if isinstance(raw, dict) else None
    flat = raw.get("prices", raw) if isinstance(raw, dict) else {}
    prices = {k.upper(): float(v) for k, v in flat.items()
              if isinstance(v, (int, float)) and not k.startswith("_")}
    return prices, as_of


def load_portfolio(cfg):
    p = read_json(PORTFOLIO)
    if p is None:
        p = {"mode": "paper" if cfg.get("paper", True) else "live",
             "cash": float(cfg["budget"]), "positions": {}, "history": [],
             "created": now_iso()}
    return p
