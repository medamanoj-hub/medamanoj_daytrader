#!/usr/bin/env python3
"""india-risk: check every proposed order against your limits and BLOCK
anything that breaks one. Only orders marked PASS can move on to the broker.

Checks, per order:
  - equity cash only: exchange NSE or BSE, product DELIVERY (no intraday,
    no margin, no F&O), series EQ unless you allow others
  - whole, positive quantity; LIMIT orders only unless you allow MARKET
    (under SEBI's 2026 API rules market orders are converted anyway)
  - limit price within price_band_pct of the last price, and the price
    not older than max_price_age_hours
  - order value <= max_order_value
  - symbol not in blocked_symbols or your surveillance list (ASM / GSM)
  - sells: never more than you hold (no short selling)
  - position after the fill <= max_position_pct of the portfolio
Then, across all orders in sequence:
  - buys + charges never dip into the cash buffer
  - no more than max_orders_per_day approved orders today

Reads  trader/orders.json, trader/config.json, trader/portfolio.json,
       trader/surveillance.json (optional: {"ASM": [...], "GSM": [...]})
Writes the verdict back into trader/orders.json.
Exit code 0 if every order passed, 2 if any was blocked.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from trader_common import (APPROVALS, ORDERS, TRADER, charges, die, inr, load_config,  # noqa: E402
                           load_portfolio, now_iso, order_hash, read_json, write_json)

SURVEILLANCE = os.path.join(TRADER, "surveillance.json")
DERIV = re.compile(r"(FUT|\d+(CE|PE))$")
DONE = ("APPROVED", "FILLED_PAPER", "PLACED_BY_USER", "REJECTED")


def approvals_today():
    if not os.path.exists(APPROVALS):
        return 0
    today = dt.date.today().isoformat()
    n = 0
    with open(APPROVALS, encoding="utf-8") as f:
        for line in f:
            try:
                if json.loads(line).get("approved_at", "").startswith(today):
                    n += 1
            except ValueError:
                pass
    return n


def price_age_hours(stamp):
    if not stamp:
        return None
    try:
        t = dt.datetime.fromisoformat(stamp.replace("Z", ""))
    except ValueError:
        return None
    return (dt.datetime.now() - t).total_seconds() / 3600


def check(orders_doc, cfg, portfolio, surveillance):
    L = cfg["limits"]
    blocked = {s.upper() for s in L.get("blocked_symbols", [])}
    watch = {}
    for lst, syms in (surveillance or {}).items():
        if lst.startswith("_"):
            continue
        for s in syms:
            watch.setdefault(s.upper(), []).append(lst)

    positions = {k.upper(): dict(v) for k, v in portfolio.get("positions", {}).items()}
    # What can be sold is what you own now, never shares from buys still pending.
    sellable = {k: v.get("qty", 0) for k, v in positions.items()}
    cash = float(portfolio.get("cash", cfg["budget"]))
    # portfolio value at last known prices (cost for positions without a price)
    last = {o["symbol"].upper(): o.get("last_price") for o in orders_doc["orders"]}
    holdings_value = sum(p["qty"] * (last.get(s) or p.get("avg_price", 0))
                         for s, p in positions.items())
    total_value = cash + holdings_value
    buffer_amt = total_value * cfg["cash_buffer_pct"] / 100
    cap_amt = total_value * cfg["max_position_pct"] / 100
    age = price_age_hours(orders_doc.get("prices_as_of"))

    used_today = approvals_today()
    passes = 0
    cash_left = cash

    for o in orders_doc["orders"]:
        if o.get("status") in DONE:
            continue  # already approved, filled, placed or rejected: leave it alone
        reasons, warnings = [], []
        sym = (o.get("symbol") or "").upper()
        side = (o.get("side") or "").upper()
        qty = o.get("qty")
        lp = o.get("limit_price")
        ref = o.get("last_price")

        if not sym:
            reasons.append("no symbol")
        if side not in ("BUY", "SELL"):
            reasons.append("side must be BUY or SELL")
        if o.get("exchange", "NSE") not in ("NSE", "BSE"):
            reasons.append("exchange %s not allowed (equity cash on NSE/BSE only)" % o.get("exchange"))
        if (o.get("product") or "").upper() not in ("DELIVERY", "CNC"):
            reasons.append("product %s not allowed: delivery only (no intraday or margin)"
                           % o.get("product"))
        if DERIV.search(sym):
            reasons.append("looks like a derivative; futures and options are not allowed")
        series = (o.get("series") or "EQ").upper()
        if series != "EQ" and not L.get("allow_non_eq_series"):
            reasons.append("series %s is trade-to-trade or restricted; not allowed by your limits" % series)
        if not isinstance(qty, int) or qty <= 0:
            reasons.append("quantity must be a whole number above zero")
        otype = (o.get("order_type") or "").upper()
        if otype == "MARKET" and not L.get("allow_market_orders"):
            reasons.append("market orders not allowed; use a LIMIT price")
        elif otype not in ("LIMIT", "MARKET"):
            reasons.append("order type %s not allowed" % otype)
        if otype == "LIMIT":
            if not lp or lp <= 0:
                reasons.append("limit price missing")
            elif ref:
                dev = abs(lp - ref) / ref * 100
                if dev > L["price_band_pct"]:
                    reasons.append("limit %s is %.1f %% from last price %s (band %g %%)"
                                   % (inr(lp), dev, inr(ref), L["price_band_pct"]))
            else:
                warnings.append("no last price to compare the limit against")
        if age is not None and age > L["max_price_age_hours"]:
            reasons.append("prices are %.0f h old (max %g h); refresh them" % (age, L["max_price_age_hours"]))
        elif age is None:
            warnings.append("price timestamp unknown")

        price = lp or ref or 0
        value = (qty or 0) * price
        fee = charges(value, side, cfg) if value else 0
        if value > L["max_order_value"]:
            reasons.append("order %s above max_order_value %s" % (inr(value), inr(L["max_order_value"])))
        if sym in blocked:
            reasons.append("%s is on your blocked list" % sym)
        if sym in watch:
            reasons.append("%s is under exchange surveillance (%s)" % (sym, ", ".join(watch[sym])))

        held = positions.get(sym, {}).get("qty", 0)
        can_sell = sellable.get(sym, 0)
        if side == "SELL" and qty and qty > can_sell:
            reasons.append("selling %d but you hold %d (no short selling)" % (qty, can_sell))
        if side == "BUY" and value:
            after = (held * price) + value
            if after > cap_amt + 0.01:
                reasons.append("position after fill %s above cap %s (%g %%)"
                               % (inr(after), inr(cap_amt), cfg["max_position_pct"]))
            if cash_left - value - fee < buffer_amt - 0.01:
                reasons.append("would use the cash buffer: %s cash left, need %s + %s fees, keep %s"
                               % (inr(cash_left), inr(value), inr(fee), inr(buffer_amt)))
        if not reasons and used_today + passes >= L["max_orders_per_day"]:
            reasons.append("max_orders_per_day (%d) reached" % L["max_orders_per_day"])

        status = "BLOCK" if reasons else "PASS"
        if status == "PASS":
            passes += 1
            if side == "BUY":
                cash_left -= value + fee
                positions.setdefault(sym, {"qty": 0})["qty"] = held + qty
            else:
                # sale proceeds settle T+1, so they don't fund buys in this batch
                sellable[sym] = can_sell - qty
                positions[sym]["qty"] = held - qty
        o["hash"] = order_hash(o)
        o["risk"] = {"status": status, "reasons": reasons, "warnings": warnings,
                     "checked": now_iso(), "hash": o["hash"]}
        o["status"] = "RISK_PASS" if status == "PASS" else "RISK_BLOCK"
    return orders_doc, {"cash": cash, "cash_after": round(cash_left, 2), "buffer": round(buffer_amt, 2),
                        "total_value": round(total_value, 2), "passed": passes}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--orders", default=ORDERS)
    p.add_argument("--config")
    p.add_argument("--surveillance", default=SURVEILLANCE)
    a = p.parse_args(argv)

    cfg = load_config(a.config)
    doc = read_json(a.orders)
    if not doc or not doc.get("orders"):
        die("no orders in %s. Run india-budget first." % a.orders)
    portfolio = load_portfolio(cfg)
    if not os.path.exists(os.path.join(TRADER, "portfolio.json")) and doc.get("summary", {}).get("budget"):
        portfolio["cash"] = float(doc["summary"]["budget"])  # fresh start: the budget is the cash
    surveillance = read_json(a.surveillance, {})
    doc, s = check(doc, cfg, portfolio, surveillance)
    doc["risk_checked"] = now_iso()
    write_json(a.orders, doc)

    print("Portfolio %s (cash %s), buffer kept %s\n" % (inr(s["total_value"]), inr(s["cash"]), inr(s["buffer"])))
    blocked = 0
    for o in doc["orders"]:
        if o.get("status") in DONE:
            print("%-4s %-5s (%s, not re-checked)" % (o["id"], "-", o["status"]))
            continue
        r = o["risk"]
        print("%-4s %-5s %-4s %4s x %-12s @ %-12s %s" % (
            o["id"], r["status"], o["side"], o["qty"], o["symbol"], inr(o.get("limit_price")),
            "" if r["status"] == "PASS" else "<- " + "; ".join(r["reasons"])))
        for w in r["warnings"]:
            print("      note: " + w)
        blocked += r["status"] == "BLOCK"
    print("\n%d passed, %d blocked. Cash after passed orders: %s" % (s["passed"], blocked, inr(s["cash_after"])))
    if not os.path.exists(os.path.join(os.path.dirname(a.orders) or ".", "surveillance.json")):
        print("tip: add trader/surveillance.json with NSE's current ASM/GSM symbols to block them.")
    sys.exit(2 if blocked else 0)


if __name__ == "__main__":
    main()
