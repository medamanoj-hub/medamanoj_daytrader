#!/usr/bin/env python3
"""india-tax: capital-gains arithmetic for listed Indian shares.

What it knows (listed equity, STT paid, resident individual; check
trader/config.json "tax" for the rates it uses):
  - Short term: held 12 months or less. STCG taxed at 20 %.
  - Long term: held more than 12 months. LTCG taxed at 12.5 % above
    ₹1.25 lakh a financial year (April-March).
  - Shares bought before 1 Feb 2018: cost = higher of actual cost and the
    lower of (31 Jan 2018 fair market value, sale price). Put FMVs in
    trader/fmv_2018.json as {"SYMBOL": price}.
  - Lots are matched first-in-first-out, as for shares held in demat.
  - Set-off: short-term losses against short- or long-term gains;
    long-term losses only against long-term gains. Unused losses carry
    forward 8 years if the return is filed on time.
  - Brokerage, exchange, SEBI, stamp and GST are deductible transfer
    costs. STT is not, so it is taken out of the charges.
  - These rates and periods are the same under the Income-tax Act, 2025
    (in force from 1 April 2026); only section numbers moved.
  - India has no "wash sale" rule, but a sale made only to create a loss
    can be questioned. Say so whenever suggesting harvesting.

Ignored: surcharge, the basic-exemption shortfall, business income (if
you trade a lot, gains may be business income instead), F&O, dividends.

Trades live in trader/trades.json.

Commands
  add BUY|SELL SYMBOL QTY PRICE --date YYYY-MM-DD [--charges X]
  import-paper                      trades from trader/portfolio.json history
  import-csv FILE                   broker tradebook CSV (date, symbol, buy/sell, qty, price)
  gains [--fy 2026-27]              realised STCG/LTCG, set-off, tax estimate
  open [--prices F | --fetch]       open lots, gains, days to long-term
  harvest [--min-loss 1000]         loss harvesting and LTCG-exemption harvesting ideas
"""

import argparse
import csv
import datetime as dt
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from trader_common import (PORTFOLIO, TRADER, die, inr, load_config, load_prices,  # noqa: E402
                           read_json, write_json)

TRADES = os.path.join(TRADER, "trades.json")
FMV = os.path.join(TRADER, "fmv_2018.json")
GRANDFATHER_DATE = dt.date(2018, 2, 1)

TAX_DEFAULTS = {"stcg_rate": 20.0, "ltcg_rate": 12.5, "ltcg_exemption": 125000, "cess_pct": 4.0,
                "brought_forward": {"st": 0, "lt": 0}}


def tax_cfg():
    cfg = load_config()
    t = dict(TAX_DEFAULTS)
    t.update({k: v for k, v in (cfg.get("tax") or {}).items() if not k.startswith("_")})
    return t, cfg


def fy_of(d):
    y = d.year if d.month >= 4 else d.year - 1
    return "%d-%02d" % (y, (y + 1) % 100)


def fy_bounds(label):
    y = int(label[:4])
    return dt.date(y, 4, 1), dt.date(y + 1, 3, 31)


def add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year + n, day=28)


def long_term_from(buy):
    """First sale date that counts as long term (held more than 12 months)."""
    return add_years(buy, 1) + dt.timedelta(days=1)


def is_long(buy, sell):
    return sell >= long_term_from(buy)


def load_trades():
    return read_json(TRADES, []) or []


def save_trades(tr):
    tr.sort(key=lambda t: (t["date"], 0 if t["side"] == "BUY" else 1))
    write_json(TRADES, tr)


def deductible(t, cfg):
    """Charges minus STT (STT is not deductible)."""
    stt = t["qty"] * t["price"] * cfg["charges"]["stt_pct"] / 100
    return max(0.0, float(t.get("charges") or 0) - stt)


# ------------------------------------------------------------------- FIFO


def match(trades, cfg, fmv):
    """FIFO-match sells to buys. Returns (realised, open_lots, warnings)."""
    lots = {}
    realised, warnings = [], []
    for t in sorted(trades, key=lambda t: (t["date"], 0 if t["side"] == "BUY" else 1)):
        s, d = t["symbol"].upper(), dt.date.fromisoformat(t["date"])
        if t["side"] == "BUY":
            per_share = t["price"] + deductible(t, cfg) / t["qty"]
            lots.setdefault(s, []).append({"symbol": s, "date": d, "qty": t["qty"], "cost": per_share})
            continue
        left = t["qty"]
        net_px = t["price"] - deductible(t, cfg) / t["qty"]
        queue = lots.get(s, [])
        while left > 0 and queue:
            lot = queue[0]
            q = min(left, lot["qty"])
            cost = lot["cost"]
            gf = False
            if lot["date"] < GRANDFATHER_DATE:
                f = fmv.get(s)
                if f is None:
                    warnings.append("%s lot of %s bought before 1 Feb 2018 needs its 31 Jan 2018 FMV in %s"
                                    % (s, lot["date"], FMV))
                else:
                    cost = max(cost, min(float(f), t["price"]))
                    gf = True
            realised.append({"symbol": s, "qty": q, "buy_date": lot["date"].isoformat(),
                             "sell_date": d.isoformat(), "fy": fy_of(d),
                             "term": "LT" if is_long(lot["date"], d) else "ST",
                             "cost": round(q * cost, 2), "proceeds": round(q * net_px, 2),
                             "gain": round(q * (net_px - cost), 2), "grandfathered": gf})
            lot["qty"] -= q
            left -= q
            if lot["qty"] == 0:
                queue.pop(0)
        if left > 0:
            warnings.append("%s: sold %d more than the buys on record (on %s). Add the missing buys."
                            % (s, left, d))
    open_lots = [l for ls in lots.values() for l in ls if l["qty"] > 0]
    return realised, open_lots, warnings


def setoff(realised_fy, tcfg):
    stg = sum(r["gain"] for r in realised_fy if r["term"] == "ST" and r["gain"] > 0)
    stl = -sum(r["gain"] for r in realised_fy if r["term"] == "ST" and r["gain"] < 0)
    ltg = sum(r["gain"] for r in realised_fy if r["term"] == "LT" and r["gain"] > 0)
    ltl = -sum(r["gain"] for r in realised_fy if r["term"] == "LT" and r["gain"] < 0)
    net_st, net_lt = stg - stl, ltg - ltl
    cf_st = cf_lt = 0.0
    if net_lt < 0:                       # LT loss: only against LT gains
        cf_lt, net_lt = -net_lt, 0.0
    if net_st < 0:                       # ST loss: against LT gains too
        use = min(-net_st, net_lt)
        net_lt -= use
        cf_st = -net_st - use
        net_st = 0.0
    bf = tcfg.get("brought_forward") or {}
    bst, blt = float(bf.get("st", 0)), float(bf.get("lt", 0))
    u = min(bst, net_st); net_st -= u; bst -= u          # noqa: E702
    u = min(bst, net_lt); net_lt -= u; bst -= u          # noqa: E702
    u = min(blt, net_lt); net_lt -= u; blt -= u          # noqa: E702
    exempt = min(net_lt, float(tcfg["ltcg_exemption"]))
    taxable_lt = net_lt - exempt
    tax = net_st * tcfg["stcg_rate"] / 100 + taxable_lt * tcfg["ltcg_rate"] / 100
    tax *= 1 + tcfg["cess_pct"] / 100
    return {"st_gains": stg, "st_losses": stl, "lt_gains": ltg, "lt_losses": ltl,
            "net_st": net_st, "net_lt": net_lt, "exemption_used": exempt,
            "exemption_left": float(tcfg["ltcg_exemption"]) - exempt, "taxable_lt": taxable_lt,
            "tax_estimate": round(tax, 2), "carry_forward_st": cf_st + bst, "carry_forward_lt": cf_lt + blt}


# --------------------------------------------------------------- commands


def cmd_add(a):
    tr = load_trades()
    tr.append({"date": a.date, "symbol": a.symbol.upper(), "side": a.side.upper(), "qty": a.qty,
               "price": a.price, "charges": a.charges, "source": "manual"})
    save_trades(tr)
    print("added %s %d %s @ %s on %s" % (a.side.upper(), a.qty, a.symbol.upper(), inr(a.price), a.date))


def cmd_import_paper(a):
    pf = read_json(PORTFOLIO)
    if not pf or not pf.get("history"):
        die("no paper history in " + PORTFOLIO)
    tr = load_trades()
    have = {t.get("ref") for t in tr}
    n = 0
    for h in pf["history"]:
        ref = "paper:%s:%s" % (h["order_id"], h["at"])
        if ref in have:
            continue
        tr.append({"date": h["at"][:10], "symbol": h["symbol"], "side": h["side"], "qty": h["qty"],
                   "price": h["price"], "charges": h.get("charges", 0), "source": "paper", "ref": ref})
        n += 1
    save_trades(tr)
    print("imported %d paper trade(s)" % n)


def _col(head, *pats):
    for p in pats:
        for i, h in enumerate(head):
            if re.search(p, h, re.I):
                return i
    return None


def cmd_import_csv(a):
    with open(a.file, encoding="utf-8-sig", errors="replace") as f:
        rows = list(csv.reader(f))
    hi = next((i for i, r in enumerate(rows[:30]) if _col(r, r"symbol|scrip|stock|instrument") is not None
               and _col(r, r"qty|quantity") is not None), None)
    if hi is None:
        die("could not find columns for symbol and quantity")
    head = [h.strip() for h in rows[hi]]
    c = {"date": _col(head, r"trade.?date|order.?date|^date"), "sym": _col(head, r"symbol|scrip|stock|instrument"),
         "side": _col(head, r"buy.?/?.?sell|trade.?type|side|^type|transaction"),
         "qty": _col(head, r"qty|quantity"), "price": _col(head, r"^price|rate|avg|trade.?price"),
         "chg": _col(head, r"charge|brokerage|total.?tax")}
    if None in (c["date"], c["side"], c["price"]):
        die("need date, buy/sell and price columns; found: %s" % head)
    tr = load_trades()
    n = 0
    for r in rows[hi + 1:]:
        if len(r) <= max(v for v in c.values() if v is not None) or not r[c["sym"]].strip():
            continue
        d = None
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%Y-%m-%d %H:%M:%S"):
            try:
                d = dt.datetime.strptime(r[c["date"]].strip()[:19], fmt).date()
                break
            except ValueError:
                pass
        if not d:
            continue
        side = "BUY" if r[c["side"]].strip().upper().startswith("B") else "SELL"
        sym = re.sub(r"-EQ$", "", r[c["sym"]].strip().upper())
        tr.append({"date": d.isoformat(), "symbol": sym, "side": side,
                   "qty": int(float(r[c["qty"]].replace(",", ""))),
                   "price": float(r[c["price"]].replace(",", "")),
                   "charges": float(r[c["chg"]].replace(",", "") or 0) if c["chg"] is not None else 0,
                   "source": os.path.basename(a.file)})
        n += 1
    save_trades(tr)
    print("imported %d trade(s) from %s" % (n, a.file))
    if c["chg"] is None:
        print("note: no charges column; costs exclude brokerage (slightly overstates gains)")


def cmd_gains(a):
    tcfg, cfg = tax_cfg()
    fmv = read_json(FMV, {}) or {}
    realised, _, warnings = match(load_trades(), cfg, fmv)
    fy = a.fy or fy_of(dt.date.today())
    rs = [r for r in realised if r["fy"] == fy]
    for w in sorted(set(warnings)):
        print("warning: " + w)
    print("FY %s: %d matched sale lot(s)\n" % (fy, len(rs)))
    for r in rs:
        print("  %s sell %-12s %5d  bought %s  %s  gain %s%s" % (
            r["sell_date"], r["symbol"], r["qty"], r["buy_date"], r["term"], inr(r["gain"]),
            "  (grandfathered)" if r["grandfathered"] else ""))
    s = setoff(rs, tcfg)
    print("\nShort term: gains %s, losses %s  -> net %s" % (inr(s["st_gains"]), inr(s["st_losses"]), inr(s["net_st"])))
    print("Long term:  gains %s, losses %s  -> net %s" % (inr(s["lt_gains"]), inr(s["lt_losses"]), inr(s["net_lt"])))
    print("LTCG exemption used %s, left %s" % (inr(s["exemption_used"]), inr(s["exemption_left"])))
    print("Estimated tax (incl. %g %% cess, no surcharge): %s" % (tcfg["cess_pct"], inr(s["tax_estimate"])))
    if s["carry_forward_st"] or s["carry_forward_lt"]:
        print("Loss to carry forward (file your return on time): ST %s, LT %s"
              % (inr(s["carry_forward_st"]), inr(s["carry_forward_lt"])))
    if int(fy[:4]) < 2025:
        print("note: rates changed on 23 July 2024; this applies today's rates to the whole year.")
    print("\nAn estimate, not tax advice. Check with a CA before filing.")


def cmd_open(a):
    tcfg, cfg = tax_cfg()
    _, lots, warnings = match(load_trades(), cfg, read_json(FMV, {}) or {})
    if not lots:
        die("no open lots")
    prices, as_of = load_prices(a.prices, a.fetch, sorted({l["symbol"] for l in lots}))
    today = dt.date.today()
    print("%-12s %-10s %6s %12s %12s %12s %4s %s" % ("symbol", "bought", "qty", "cost/sh", "price", "gain", "term", "long-term from"))
    for l in sorted(lots, key=lambda l: (l["symbol"], l["date"])):
        px = prices.get(l["symbol"])
        g = (px - l["cost"]) * l["qty"] if px else None
        lt = long_term_from(l["date"])
        term = "LT" if today >= lt else "ST"
        print("%-12s %-10s %6d %12s %12s %12s %4s %s" % (
            l["symbol"], l["date"], l["qty"], inr(l["cost"]), inr(px), inr(g), term,
            "" if term == "LT" else "%s (%d days)" % (lt, (lt - today).days)))


def cmd_harvest(a):
    tcfg, cfg = tax_cfg()
    fmv = read_json(FMV, {}) or {}
    realised, lots, _ = match(load_trades(), cfg, fmv)
    prices, _ = load_prices(a.prices, a.fetch, sorted({l["symbol"] for l in lots}))
    today = dt.date.today()
    fy = fy_of(today)
    s = setoff([r for r in realised if r["fy"] == fy], tcfg)
    print("FY %s so far: net ST %s, net LT %s, LTCG exemption left %s\n" % (
        fy, inr(s["net_st"]), inr(s["net_lt"]), inr(s["exemption_left"])))
    losses, ltgains = [], []
    for l in lots:
        px = prices.get(l["symbol"])
        if not px:
            continue
        g = (px - l["cost"]) * l["qty"]
        term = "LT" if today >= long_term_from(l["date"]) else "ST"
        if g <= -a.min_loss:
            losses.append((l, px, g, term))
        elif g > 0 and term == "LT":
            ltgains.append((l, px, g))

    print("1. Loss harvesting (book losses to cut this year's tax)")
    if not losses:
        print("   none: no open lot with a loss of %s or more" % inr(a.min_loss))
    this_fy = [r for r in realised if r["fy"] == fy]
    for l, px, g, term in sorted(losses, key=lambda x: x[2]):
        # tax this year with and without booking this loss
        extra = {"term": term, "gain": g}
        saved = s["tax_estimate"] - setoff(this_fy + [extra], tcfg)["tax_estimate"]
        print("   %-12s %5d sh bought %s (%s): loss %s at %s. Tax saved this year: ~%s%s" % (
            l["symbol"], l["qty"], l["date"], term, inr(g), inr(px), inr(saved),
            "" if saved else " (no gains to offset yet; carries forward)"))
    print("\n2. LTCG exemption harvesting (book long-term gains tax-free up to the yearly limit)")
    left = s["exemption_left"]
    if left <= 0:
        print("   exemption already used this year")
    elif not ltgains:
        print("   no long-term lots in profit")
    else:
        for l, px, g in sorted(ltgains, key=lambda x: -x[2]):
            per = px - l["cost"]
            q = min(l["qty"], int(left // per)) if per > 0 else 0
            if q <= 0:
                continue
            print("   %-12s sell %d of %d (bought %s) at %s: gain %s, tax-free; buying back resets the cost higher"
                  % (l["symbol"], q, l["qty"], l["date"], inr(px), inr(q * per)))
            left -= q * per
            if left < 1:
                break
    print("\nIndia has no wash-sale rule, but a sale made only to create a tax loss can be questioned,")
    print("and selling then buying back costs charges both ways. An estimate, not tax advice.")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add")
    s.add_argument("side", choices=["BUY", "SELL", "buy", "sell"])
    s.add_argument("symbol")
    s.add_argument("qty", type=int)
    s.add_argument("price", type=float)
    s.add_argument("--date", required=True)
    s.add_argument("--charges", type=float, default=0.0)
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("import-paper")
    s.set_defaults(fn=cmd_import_paper)
    s = sub.add_parser("import-csv")
    s.add_argument("file")
    s.set_defaults(fn=cmd_import_csv)
    s = sub.add_parser("gains")
    s.add_argument("--fy", help="e.g. 2026-27 (default: current)")
    s.set_defaults(fn=cmd_gains)
    for name, fn in (("open", cmd_open), ("harvest", cmd_harvest)):
        s = sub.add_parser(name)
        s.add_argument("--prices")
        s.add_argument("--fetch", action="store_true")
        if name == "harvest":
            s.add_argument("--min-loss", type=float, default=1000)
        s.set_defaults(fn=fn)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
