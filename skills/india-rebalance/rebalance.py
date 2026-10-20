#!/usr/bin/env python3
"""india-rebalance: bring the portfolio back to its target weights when a
stock drifts outside your band.

  total     = cash + every holding at today's price
  target    = (total - cash buffer) x target weight, capped at max_position_pct
  drift     = current weight - target weight, in percentage points
  trade     = only where |drift| > band (default 5 points)

Sells come first (limit a little below the last price). Buys are sized to
the cash you have now, keeping the buffer. Sale money is not assumed to be
available for buys the same day; if buys are short of cash, run this again
after the sells settle and it will propose the rest.

Stocks you hold that are not in targets.json are listed but left alone,
unless you pass --sell-untargeted.

Reads  trader/targets.json, trader/portfolio.json, prices, trader/config.json
Writes trader/orders.json (status PROPOSED) with --write. Then: india-risk.
"""

import argparse
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from trader_common import (ORDERS, TARGETS, charges, die, inr, load_config, load_portfolio,  # noqa: E402
                           load_prices, now_iso, order_hash, read_json, round_to_tick, tick_for,
                           write_json)

FINAL = ("FILLED_PAPER", "PLACED_BY_USER", "REJECTED")


def plan(targets, portfolio, prices, cfg, band, sell_untargeted=False):
    pos = {s.upper(): p for s, p in portfolio.get("positions", {}).items() if p.get("qty")}
    tw = {t["symbol"].upper(): float(t["weight"]) for t in targets if t.get("symbol")}
    tot_w = sum(tw.values()) or 1
    tw = {s: w / tot_w for s, w in tw.items()}

    missing = [s for s in set(pos) | set(tw) if s not in prices]
    if missing:
        die("no price for: " + ", ".join(sorted(missing)))
    cash = float(portfolio.get("cash", 0))
    total = cash + sum(p["qty"] * prices[s] for s, p in pos.items())
    if total <= 0:
        die("portfolio is empty")
    investable = total * (1 - cfg["cash_buffer_pct"] / 100)
    cap = total * cfg["max_position_pct"] / 100

    rows = []
    for s in sorted(set(pos) | set(tw)):
        qty = pos.get(s, {}).get("qty", 0)
        val = qty * prices[s]
        if s in tw:
            tgt = min(investable * tw[s], cap)
        else:
            tgt = 0 if sell_untargeted else None
        cur_w = val / total * 100
        tgt_w = None if tgt is None else tgt / total * 100
        drift = None if tgt_w is None else cur_w - tgt_w
        rows.append({"symbol": s, "qty": qty, "price": prices[s], "value": val, "target_value": tgt,
                     "weight": cur_w, "target_weight": tgt_w, "drift": drift,
                     "act": drift is not None and abs(drift) > band})

    orders, notes = [], []
    lb = cfg["limit_buffer_pct"] / 100
    # sells first
    for r in rows:
        if not r["act"] or r["drift"] <= 0:
            continue
        excess = r["value"] - r["target_value"]
        q = min(r["qty"], int(math.floor(excess / r["price"]))) if r["target_value"] else r["qty"]
        if q <= 0:
            continue
        lp = round_to_tick(r["price"] * (1 - lb), tick_for(r["price"]), up=False)
        orders.append(_order(r["symbol"], "SELL", q, lp, r, cfg))
    # buys from cash on hand, keeping the buffer
    spendable = cash - total * cfg["cash_buffer_pct"] / 100
    for r in sorted([r for r in rows if r["act"] and r["drift"] < 0], key=lambda r: r["drift"]):
        lp = round_to_tick(r["price"] * (1 + lb), tick_for(r["price"]))
        want = int(math.floor((r["target_value"] - r["value"]) / lp))
        q = want
        while q > 0 and q * lp + charges(q * lp, "BUY", cfg) > spendable + 1e-9:
            q -= 1
        if q < want:
            notes.append("%s: buy %d of %d now; the rest after sells settle or cash is added"
                         % (r["symbol"], q, want))
        if q <= 0:
            continue
        spendable -= q * lp + charges(q * lp, "BUY", cfg)
        orders.append(_order(r["symbol"], "BUY", q, lp, r, cfg))
    for i, o in enumerate(orders, 1):
        o["id"] = "R%d" % i
        o["hash"] = order_hash(o)
    return rows, orders, notes, {"total": total, "cash": cash, "investable": investable}


def _order(sym, side, qty, lp, r, cfg):
    value = round(qty * lp, 2)
    return {"symbol": sym, "series": "EQ", "exchange": cfg["exchange"], "side": side, "qty": qty,
            "order_type": "LIMIT", "limit_price": lp, "last_price": r["price"],
            "product": cfg["product"], "validity": "DAY", "value": value,
            "est_charges": charges(value, side, cfg),
            "target_weight": round(r["target_weight"] or 0, 2), "actual_weight": round(r["weight"], 2),
            "reason": "rebalance: drift %+.1f pts" % r["drift"], "status": "PROPOSED"}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--band", type=float, default=5.0, help="drift in percentage points before trading")
    p.add_argument("--targets", default=TARGETS)
    p.add_argument("--prices", help="JSON {symbol: price}")
    p.add_argument("--fetch", action="store_true", help="fetch prices from Yahoo (.NS)")
    p.add_argument("--sell-untargeted", action="store_true", help="sell holdings not in targets.json")
    p.add_argument("--write", action="store_true", help="write trader/orders.json")
    p.add_argument("--force", action="store_true", help="replace orders.json even if it has open orders")
    a = p.parse_args(argv)

    cfg = load_config()
    tg = read_json(a.targets)
    if not tg or not tg.get("targets"):
        die("no targets in " + a.targets)
    pf = load_portfolio(cfg)
    if not pf.get("positions"):
        die("no holdings in trader/portfolio.json; use india-budget for a first purchase")
    syms = set(pf["positions"]) | {t["symbol"] for t in tg["targets"] if t.get("symbol")}
    prices, as_of = load_prices(a.prices, a.fetch, sorted(syms))
    rows, orders, notes, s = plan(tg["targets"], pf, prices, cfg, a.band, a.sell_untargeted)

    print("Portfolio %s (cash %s). Band %g pts.\n" % (inr(s["total"]), inr(s["cash"]), a.band))
    print("%-12s %6s %14s %8s %8s %8s" % ("symbol", "qty", "value", "now", "target", "drift"))
    for r in rows:
        print("%-12s %6d %14s %7.2f%% %8s %8s %s" % (
            r["symbol"], r["qty"], inr(r["value"]), r["weight"],
            "-" if r["target_weight"] is None else "%.2f%%" % r["target_weight"],
            "-" if r["drift"] is None else "%+.2f" % r["drift"],
            "<- trade" if r["act"] else ("(not in targets, left alone)" if r["target_weight"] is None else "")))
    print()
    if not orders:
        print("Nothing outside the band. No trades needed.")
    for o in orders:
        print("%-4s %-4s %4d x %-12s @ %-12s %s" % (o["id"], o["side"], o["qty"], o["symbol"],
                                                  inr(o["limit_price"]), o["reason"]))
    for n in notes:
        print("note: " + n)

    if a.write and orders:
        old = read_json(ORDERS, {}) or {}
        open_orders = [o for o in old.get("orders", []) if o.get("status") not in FINAL]
        if open_orders and not a.force:
            die("trader/orders.json still has %d open order(s) (%s). Finish or reject them, "
                "or pass --force." % (len(open_orders), ", ".join(o["id"] for o in open_orders)))
        write_json(ORDERS, {"created": now_iso(), "source": "india-rebalance", "prices_as_of": as_of,
                            "summary": {"budget": s["total"], "band": a.band}, "orders": orders})
        print("\nwrote %s (PROPOSED). Next: india-risk." % ORDERS)
    elif orders:
        print("\n(dry run; add --write to save trader/orders.json)")


if __name__ == "__main__":
    main()
