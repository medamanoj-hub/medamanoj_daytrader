#!/usr/bin/env python3
"""india-analyst: each company's reported quarterly numbers, as filed with
NSE: revenue, net profit, EPS, margins, and growth quarter-on-quarter and
year-on-year. Results are saved to trader/metrics.json for india-screener.

Source: NSE's "results comparison" for each symbol (the last ~5 quarters,
as filed by the company; NSE reports amounts in rupees lakh, shown here in
crore). NSE does not document this endpoint, so field names are matched
loosely. When it fails, use --file with a CSV:

  quarter_end,revenue_cr,net_profit_cr,eps[,pbt_cr]
  2026-09-30,24500,3120,18.4

Commands
  facts SYMBOL [SYMBOL...] [--price P | --prices FILE]   fetch, show, save
  facts SYMBOL --file results.csv                         from a file
  show                                                    what's in metrics.json

Standard library only. Python 3.8+.
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TRADER = os.environ.get("TRADER_DIR", "trader")
METRICS = os.path.join(TRADER, "metrics.json")
LAKH_PER_CRORE = 100.0


def die(msg):
    print("error: " + msg, file=sys.stderr)
    sys.exit(1)


def num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).replace(",", "").strip()
    if t.startswith("(") and t.endswith(")"):
        t = "-" + t[1:-1]
    try:
        return float(t)
    except ValueError:
        return None


def parse_date(s):
    s = str(s or "").strip()
    for f in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%b %Y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def fuzzy(row, *patterns):
    """Value of the first key matching any regex (case-insensitive)."""
    for p in patterns:
        rx = re.compile(p, re.I)
        for k, v in row.items():
            if rx.search(k) and num(v) is not None:
                return num(v)
    return None


def from_nse_row(r):
    end = parse_date(r.get("re_to_dt") or r.get("toDate") or r.get("re_to_date"))
    rev = fuzzy(r, r"^re_net_sale", r"^re_revenue", r"rev.*oper", r"^re_total_inc", r"total_?inc")
    profit = fuzzy(r, r"^re_net_profit", r"net_?profit", r"^re_pro_loss_aft", r"profit.*after", r"\bpat\b")
    pbt = fuzzy(r, r"^re_pro_loss_bef_tax", r"pro.*bef.*tax", r"\bpbt\b")
    eps = fuzzy(r, r"dil.*eps", r"eps.*dil", r"^re_dil", r"basic.*eps", r"eps.*basic", r"^re_basic", r"eps")
    to_cr = lambda x: None if x is None else round(x / LAKH_PER_CRORE, 2)  # noqa: E731
    return {"quarter_end": end.isoformat() if end else None, "revenue_cr": to_cr(rev),
            "net_profit_cr": to_cr(profit), "pbt_cr": to_cr(pbt), "eps": eps,
            "audited": r.get("re_res_type") or r.get("audited"),
            "consolidated": r.get("re_consolidated") or r.get("consolidated")}


def from_csv(path):
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            r = {k.strip().lower(): v for k, v in r.items() if k}
            end = parse_date(r.get("quarter_end") or r.get("quarter") or r.get("period"))
            rows.append({"quarter_end": end.isoformat() if end else None,
                         "revenue_cr": num(r.get("revenue_cr") or r.get("revenue") or r.get("sales")),
                         "net_profit_cr": num(r.get("net_profit_cr") or r.get("net_profit") or r.get("pat")),
                         "pbt_cr": num(r.get("pbt_cr") or r.get("pbt")),
                         "eps": num(r.get("eps"))})
    return rows


def fetch(symbol):
    from nse_http import NSE
    data = NSE().get_json("results-comparision", {"symbol": symbol.upper()})  # NSE's spelling
    rows = data.get("resCmpData", []) if isinstance(data, dict) else []
    if not rows:
        die("NSE returned no results for %s. Try --file." % symbol)
    return [from_nse_row(r) for r in rows]


def growth(new, old):
    if new is None or old in (None, 0):
        return None
    return round((new - old) / abs(old) * 100, 1)


def analyse(symbol, quarters, price=None):
    qs = sorted([q for q in quarters if q.get("quarter_end")], key=lambda q: q["quarter_end"], reverse=True)
    # one row per quarter end (keep the first: consolidated rows are usually listed first)
    seen, uniq = set(), []
    for q in qs:
        if q["quarter_end"] not in seen:
            seen.add(q["quarter_end"])
            uniq.append(q)
    qs = uniq
    if not qs:
        die("no dated quarters for " + symbol)
    for q in qs:
        r, p = q.get("revenue_cr"), q.get("net_profit_cr")
        q["net_margin_pct"] = round(p / r * 100, 2) if r and p is not None else None
    latest = qs[0]
    prev = qs[1] if len(qs) > 1 else {}
    ly_date = dt.date.fromisoformat(latest["quarter_end"]) - dt.timedelta(days=365)
    yoy = next((q for q in qs[1:] if abs((dt.date.fromisoformat(q["quarter_end"]) - ly_date).days) <= 20), {})
    m = {
        "symbol": symbol.upper(),
        "as_of": latest["quarter_end"],
        "revenue_cr": latest.get("revenue_cr"),
        "net_profit_cr": latest.get("net_profit_cr"),
        "eps": latest.get("eps"),
        "net_margin_pct": latest.get("net_margin_pct"),
        "rev_growth_qoq": growth(latest.get("revenue_cr"), prev.get("revenue_cr")),
        "profit_growth_qoq": growth(latest.get("net_profit_cr"), prev.get("net_profit_cr")),
        "rev_growth_yoy": growth(latest.get("revenue_cr"), yoy.get("revenue_cr")),
        "profit_growth_yoy": growth(latest.get("net_profit_cr"), yoy.get("net_profit_cr")),
        "eps_growth_yoy": growth(latest.get("eps"), yoy.get("eps")),
        "margin_change_yoy_pts": (round(latest["net_margin_pct"] - yoy["net_margin_pct"], 2)
                                  if latest.get("net_margin_pct") is not None and
                                  yoy.get("net_margin_pct") is not None else None),
        "loss_quarters": sum(1 for q in qs[:4] if (q.get("net_profit_cr") or 0) < 0),
        "quarters": qs[:8],
        "updated": dt.datetime.now().isoformat(timespec="seconds"),
    }
    if len(qs) >= 4 and all(q.get("eps") is not None for q in qs[:4]):
        m["eps_ttm"] = round(sum(q["eps"] for q in qs[:4]), 2)
    if price:
        m["price"] = price
        if m.get("eps_ttm") and m["eps_ttm"] > 0:
            m["pe_ttm"] = round(price / m["eps_ttm"], 1)
    return m


def fmt(v, unit=""):
    return "-" if v is None else ("%s%s" % ("{:,.2f}".format(v) if isinstance(v, float) else v, unit))


def show(m):
    print("%s  (latest quarter ending %s)" % (m["symbol"], m["as_of"]))
    print("  %-12s %12s %12s %8s %9s" % ("quarter", "revenue cr", "profit cr", "EPS", "margin"))
    for q in m["quarters"]:
        print("  %-12s %12s %12s %8s %8s%%" % (q["quarter_end"], fmt(q.get("revenue_cr")),
                                               fmt(q.get("net_profit_cr")), fmt(q.get("eps")),
                                               fmt(q.get("net_margin_pct"))))
    print("  revenue growth: %s QoQ, %s YoY | profit growth: %s QoQ, %s YoY" % (
        fmt(m["rev_growth_qoq"], "%"), fmt(m["rev_growth_yoy"], "%"),
        fmt(m["profit_growth_qoq"], "%"), fmt(m["profit_growth_yoy"], "%")))
    extra = []
    if m.get("eps_ttm") is not None:
        extra.append("TTM EPS %s" % fmt(m["eps_ttm"]))
    if m.get("pe_ttm") is not None:
        extra.append("P/E %s at %s" % (fmt(m["pe_ttm"]), fmt(m["price"])))
    if m["loss_quarters"]:
        extra.append("%d loss quarter(s) in the last 4" % m["loss_quarters"])
    if extra:
        print("  " + "; ".join(extra))
    if m["rev_growth_yoy"] is None:
        print("  note: no quarter a year back in the data, so no YoY figures.")
    print()


def load_metrics():
    if os.path.exists(METRICS):
        with open(METRICS, encoding="utf-8") as f:
            return json.load(f)
    return {}


def cmd_facts(a):
    prices = {}
    if a.prices:
        with open(a.prices, encoding="utf-8") as f:
            raw = json.load(f)
        prices = {k.upper(): float(v) for k, v in raw.get("prices", raw).items() if isinstance(v, (int, float))}
    allm = load_metrics()
    for sym in a.symbols:
        quarters = from_csv(a.file) if a.file else fetch(sym)
        price = a.price if (a.price and len(a.symbols) == 1) else prices.get(sym.upper())
        m = analyse(sym, quarters, price)
        allm[m["symbol"]] = m
        show(m)
    os.makedirs(TRADER, exist_ok=True)
    with open(METRICS, "w", encoding="utf-8") as f:
        json.dump(allm, f, indent=2)
    print("saved %d symbol(s) to %s" % (len(a.symbols), METRICS))


def cmd_show(a):
    allm = load_metrics()
    if not allm:
        die("no metrics yet; run facts first")
    for m in allm.values():
        show(m)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("facts")
    s.add_argument("symbols", nargs="+")
    s.add_argument("--file", help="CSV of quarters for a single symbol")
    s.add_argument("--price", type=float, help="current price, for P/E")
    s.add_argument("--prices", help="JSON {symbol: price} for several symbols")
    s.set_defaults(fn=cmd_facts)
    s = sub.add_parser("show")
    s.set_defaults(fn=cmd_show)
    a = p.parse_args(argv)
    if a.cmd == "facts" and a.file and len(a.symbols) != 1:
        die("--file works with one symbol at a time")
    a.fn(a)


if __name__ == "__main__":
    main()
