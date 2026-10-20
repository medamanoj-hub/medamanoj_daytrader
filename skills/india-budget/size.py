#!/usr/bin/env python3
"""india-budget: turn target weights into whole-share buy orders that fit
your budget in rupees.

Rules it follows:
  - keep a cash buffer (cash_buffer_pct of the budget is never invested)
  - no stock above max_position_pct of the budget; the excess is shared
    out among the others in proportion to their weights
  - whole shares only (Indian cash market has no fractional shares)
  - each order is a LIMIT a little above the last price (limit_buffer_pct),
    rounded to the tick; it is sized on that limit, plus estimated charges,
    so it can never cost more than its allocation
  - orders below min_order_value are dropped
  - optional: spend leftover rupees one share at a time on the most
    under-weight stock, without breaking the cap

Reads  trader/targets.json, trader/config.json, prices (see below)
Writes trader/orders.json (status PROPOSED; nothing is sent anywhere)

Prices, in this order:
  --prices FILE      JSON {"SYMBOL": price, ...}
  --fetch            last price from Yahoo Finance (SYMBOL.NS), unofficial
  trader/prices.json if it exists

Standard library only. Python 3.8+.
"""

import argparse
import json
import math
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from trader_common import (ORDERS, PRICES, TARGETS, charges, die, inr, load_config,  # noqa: E402
                           load_portfolio, now_iso, order_hash, read_json, round_to_tick,
                           tick_for, write_json)

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/%s.NS?range=5d&interval=1d"


def fetch_prices(symbols):
    out = {}
    for s in symbols:
        req = urllib.request.Request(YAHOO % s, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                meta = json.load(r)["chart"]["result"][0]["meta"]
            out[s] = float(meta["regularMarketPrice"])
        except Exception as e:  # noqa: BLE001 - report and carry on
            print("warning: no price for %s (%s)" % (s, e), file=sys.stderr)
    return out


def load_prices(a, symbols):
    if a.prices:
        raw = read_json(a.prices, {})
    elif a.fetch:
        raw = {"prices": fetch_prices(symbols), "as_of": now_iso(), "source": "yahoo"}
        write_json(PRICES, raw)
    else:
        raw = read_json(PRICES, {}) or {}
    as_of = raw.get("as_of") if isinstance(raw, dict) else None
    prices = raw.get("prices", raw) if isinstance(raw, dict) else {}
    prices = {k.upper(): float(v) for k, v in prices.items()
              if not k.startswith("_") and k not in ("as_of", "source") and v}
    return prices, as_of


def cap_weights(weights, cap):
    """Scale weights (summing to 1) so none exceeds cap; redistribute the
    excess pro rata. If every name hits the cap, the rest stays uninvested."""
    w = dict(weights)
    capped = set()
    for _ in range(len(w) + 1):
        over = {k: v for k, v in w.items() if v > cap + 1e-12}
        if not over:
            break
        excess = sum(v - cap for v in over.values())
        for k in over:
            w[k] = cap
            capped.add(k)
        free = {k: v for k, v in w.items() if k not in capped}
        base = sum(free.values())
        if base <= 0:
            break
        for k, v in free.items():
            w[k] = v + excess * v / base
    return w


def size(targets, prices, cfg, budget=None):
    budget = float(budget if budget is not None else cfg["budget"])
    buffer_amt = budget * cfg["cash_buffer_pct"] / 100
    investable = budget - buffer_amt
    cap_amt = budget * cfg["max_position_pct"] / 100

    usable, skipped = [], []
    for t in targets:
        s = (t.get("symbol") or "").upper()
        if not s:
            skipped.append((t.get("isin") or "?", "no NSE symbol"))
        elif s not in prices:
            skipped.append((s, "no price"))
        else:
            usable.append(t)
    total_w = sum(float(t["weight"]) for t in usable)
    if not usable or total_w <= 0:
        die("nothing to size: no targets with both a symbol and a price")

    weights = {t["symbol"].upper(): float(t["weight"]) / total_w for t in usable}
    cap_frac = cap_amt / investable if investable else 0
    weights = cap_weights(weights, cap_frac)
    alloc = {s: investable * w for s, w in weights.items()}

    rows = {}
    for t in usable:
        s = t["symbol"].upper()
        last = prices[s]
        limit = round_to_tick(last * (1 + cfg["limit_buffer_pct"] / 100), tick_for(last))
        qty = int(math.floor(alloc[s] / limit))
        while qty > 0 and qty * limit + charges(qty * limit, "BUY", cfg) > alloc[s] + 1e-9:
            qty -= 1
        rows[s] = {"symbol": s, "series": t.get("series") or "EQ", "name": t.get("name"),
                   "last_price": last, "limit_price": limit, "qty": qty,
                   "alloc": alloc[s], "target_weight": float(t["weight"]) / total_w * 100}

    def spent():
        return sum(r["qty"] * r["limit_price"] + charges(r["qty"] * r["limit_price"], "BUY", cfg)
                   for r in rows.values() if r["qty"])

    if cfg.get("fill_leftover", True):
        while True:
            left = investable - spent()
            best = None
            for r in rows.values():
                new_val = (r["qty"] + 1) * r["limit_price"]
                extra = new_val + charges(new_val, "BUY", cfg) - (
                    r["qty"] * r["limit_price"] + (charges(r["qty"] * r["limit_price"], "BUY", cfg)
                                                   if r["qty"] else 0))
                if extra > left + 1e-9 or new_val > cap_amt + 1e-9:
                    continue
                gap = r["alloc"] - r["qty"] * r["limit_price"]
                if best is None or gap > best[0]:
                    best = (gap, r)
            if best is None:
                break
            best[1]["qty"] += 1

    orders, dropped = [], []
    n = 0
    for r in sorted(rows.values(), key=lambda r: -r["alloc"]):
        value = r["qty"] * r["limit_price"]
        if r["qty"] == 0:
            dropped.append((r["symbol"], "one share (%s) costs more than its allocation %s"
                            % (inr(r["limit_price"]), inr(r["alloc"]))))
            continue
        if value < cfg["min_order_value"]:
            dropped.append((r["symbol"], "order %s below min_order_value" % inr(value)))
            continue
        n += 1
        o = {"id": "O%d" % n, "symbol": r["symbol"], "series": r["series"], "name": r["name"],
             "exchange": cfg["exchange"], "side": "BUY", "qty": r["qty"],
             "order_type": "LIMIT", "limit_price": r["limit_price"],
             "last_price": r["last_price"], "product": cfg["product"], "validity": "DAY",
             "value": round(value, 2), "est_charges": charges(value, "BUY", cfg),
             "target_weight": round(r["target_weight"], 2),
             "actual_weight": round(value / budget * 100, 2),
             "status": "PROPOSED"}
        o["hash"] = order_hash(o)
        orders.append(o)

    invested = sum(o["value"] for o in orders)
    fees = sum(o["est_charges"] for o in orders)
    summary = {"budget": budget, "cash_buffer": round(buffer_amt, 2),
               "invested": round(invested, 2), "est_charges": round(fees, 2),
               "idle": round(investable - invested - fees, 2),
               "cash_after": round(budget - invested - fees, 2)}
    return orders, summary, skipped, dropped


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--budget", type=float, help="override config budget (rupees)")
    p.add_argument("--buffer", type=float, help="override cash_buffer_pct")
    p.add_argument("--cap", type=float, help="override max_position_pct")
    p.add_argument("--targets", default=TARGETS)
    p.add_argument("--prices", help="JSON {symbol: price}")
    p.add_argument("--fetch", action="store_true", help="fetch last prices from Yahoo (.NS)")
    p.add_argument("--config")
    p.add_argument("--use-cash", action="store_true",
                   help="size against cash in trader/portfolio.json instead of the budget")
    p.add_argument("--write", action="store_true", help="write trader/orders.json")
    a = p.parse_args(argv)

    cfg = load_config(a.config)
    if a.buffer is not None:
        cfg["cash_buffer_pct"] = a.buffer
    if a.cap is not None:
        cfg["max_position_pct"] = a.cap
    budget = a.budget
    if a.use_cash:
        budget = load_portfolio(cfg)["cash"]

    tg = read_json(a.targets)
    if not tg or not tg.get("targets"):
        die("no targets in %s. Run india-copycat first, or write the file yourself." % a.targets)
    symbols = [t["symbol"].upper() for t in tg["targets"] if t.get("symbol")]
    prices, as_of = load_prices(a, symbols)

    orders, s, skipped, dropped = size(tg["targets"], prices, cfg, budget)

    print("Budget %s, buffer %s (%g %%), cap %g %% per stock%s\n" % (
        inr(s["budget"]), inr(s["cash_buffer"]), cfg["cash_buffer_pct"], cfg["max_position_pct"],
        "" if not as_of else ", prices as of " + as_of))
    print("%-4s %-12s %6s %12s %14s %10s %8s %8s" % (
        "id", "symbol", "qty", "limit", "value", "charges", "target", "actual"))
    for o in orders:
        print("%-4s %-12s %6d %12s %14s %10s %7.2f%% %7.2f%%" % (
            o["id"], o["symbol"], o["qty"], inr(o["limit_price"]), inr(o["value"]),
            inr(o["est_charges"]), o["target_weight"], o["actual_weight"]))
    print("\ninvested %s + charges %s; buffer %s; idle %s; cash after %s" % (
        inr(s["invested"]), inr(s["est_charges"]), inr(s["cash_buffer"]),
        inr(s["idle"]), inr(s["cash_after"])))
    for sym, why in skipped:
        print("skipped %s: %s" % (sym, why))
    for sym, why in dropped:
        print("dropped %s: %s" % (sym, why))

    if a.write:
        write_json(ORDERS, {"created": now_iso(), "source": tg.get("source"),
                            "prices_as_of": as_of, "summary": s, "orders": orders})
        print("\nwrote %s (status PROPOSED; run india-risk next)" % ORDERS)
    else:
        print("\n(dry run; add --write to save trader/orders.json)")


if __name__ == "__main__":
    main()
