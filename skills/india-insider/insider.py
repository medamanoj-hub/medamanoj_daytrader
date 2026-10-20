#!/usr/bin/env python3
"""india-insider: watch what company insiders and big investors trade.

India has no "Congress trades" disclosure, but it has two better things:

1. Insider trades (SEBI PIT). Promoters, directors, key managers and their
   relatives must report trades in their own company's shares. The company
   files the disclosure with the exchange within days; NSE publishes it.
2. Bulk and block deals. Any single client trading more than 0.5 % of a
   company's shares in a day (bulk), or a big negotiated trade in the
   morning block window (block), is published by NSE the same evening,
   with the client's name. This is where well-known investors show up.

Commands
  pit    [--symbol S] [--days N]            insider trades (SEBI PIT filings)
  deals  --kind bulk|block [--days N]       bulk / block deals
  new    [--days N]                         only what's new since the last run,
                                            for your watchlist; run on a schedule

Every command also takes --file, for a CSV or JSON you downloaded by hand
from nseindia.com, so nothing depends on NSE's API staying the same.
Standard library only. Python 3.8+.
"""

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TRADER = os.environ.get("TRADER_DIR", "trader")
SEEN = os.path.join(TRADER, "insider_seen.json")

INSIDER_CATS = ("promoter", "director", "key managerial", "kmp", "designated",
                "immediate relative", "employee")


def die(msg):
    print("error: " + msg, file=sys.stderr)
    sys.exit(1)


def norm(s):
    return re.sub(r"\s+", " ", str(s if s is not None else "")).strip()


def to_float(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).replace(",", "").replace("₹", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def fmt_inr(v):
    """1.5e7 -> '₹1.50 cr', 2.5e5 -> '₹2.50 lakh'."""
    if v is None:
        return "?"
    a = abs(v)
    if a >= 1e7:
        return "₹%.2f cr" % (v / 1e7)
    if a >= 1e5:
        return "₹%.2f lakh" % (v / 1e5)
    return "₹{:,.0f}".format(v)


DATE_FORMATS = ("%d-%b-%Y", "%d-%b-%Y %H:%M", "%d-%b-%Y %H:%M:%S", "%d-%m-%Y",
                "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%Y-%m-%dT%H:%M:%S")


def parse_date(s):
    s = norm(s)
    if not s:
        return None
    for f in DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    m = re.match(r"(\d{1,2}-[A-Za-z]{3}-\d{4})", s)
    if m:
        return parse_date(m.group(1))
    return None


def pick(row, *names):
    """First non-empty value among candidate keys (exact, then fuzzy)."""
    for n in names:
        if n in row and norm(row[n]) not in ("", "-", "Nil", "NIL"):
            return row[n]
    low = {re.sub(r"[^a-z]", "", k.lower()): k for k in row}
    for n in names:
        k = low.get(re.sub(r"[^a-z]", "", n.lower()))
        if k and norm(row[k]) not in ("", "-"):
            return row[k]
    return None


def pick_like(row, *fragments):
    """Value of the first column whose header contains all fragments."""
    for k in row:
        kl = k.lower()
        for frag in fragments:
            if all(f in kl for f in frag):
                v = row[k]
                if norm(v) not in ("", "-"):
                    return v
    return None


def rid(*parts):
    return hashlib.sha1("|".join(norm(p).lower() for p in parts).encode()).hexdigest()[:16]


def load_rows(path):
    """Rows from a JSON (NSE API response) or CSV (NSE page download)."""
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    t = text.lstrip()
    if t.startswith("{") or t.startswith("["):
        data = json.loads(t)
        if isinstance(data, dict):
            data = data.get("data", [])
        return data
    rdr = csv.DictReader(io.StringIO(text))
    rdr.fieldnames = [norm(h) for h in (rdr.fieldnames or [])]
    return [{norm(k): v for k, v in r.items() if k} for r in rdr]


# --------------------------------------------------------------- normalise


def norm_pit(r):
    sym = norm(pick(r, "symbol", "SYMBOL"))
    person = norm(pick(r, "acqName", "NAME OF THE ACQUIRER/DISPOSER") or
                  pick_like(r, ("name", "acquirer"), ("name", "person")))
    cat = norm(pick(r, "personCategory", "CATEGORY OF PERSON") or pick_like(r, ("category",)))
    ttype = norm(pick(r, "tdpTransactionType", "ACQUISITION/DISPOSAL TRANSACTION TYPE") or
                 pick_like(r, ("transaction", "type")))
    mode = norm(pick(r, "acqMode", "MODE OF ACQUISITION") or pick_like(r, ("mode",)))
    shares = to_float(pick(r, "secAcq") or pick_like(r, ("no.", "security", "acquired"),
                                                      ("no", "securit", "acq")))
    value = to_float(pick(r, "secVal") or pick_like(r, ("value", "security", "acquired"),
                                                    ("value", "securit", "acq")))
    d_from = parse_date(pick(r, "acqfromDt", "DATE OF ALLOTMENT/ACQUISITION FROM") or
                        pick_like(r, ("date", "from")))
    d_to = parse_date(pick(r, "acqtoDt", "DATE OF ALLOTMENT/ACQUISITION TO") or
                      pick_like(r, ("date", "to")))
    filed = parse_date(pick(r, "date", "intimDt", "BROADCASTE DATE AND TIME") or
                       pick_like(r, ("broadcast",), ("intimation",)))
    after = to_float(pick(r, "afterAcqSharesPer") or pick_like(r, ("post", "%"), ("after", "per")))
    side = "BUY" if re.search(r"buy|acqui|purchase", ttype + " " + mode, re.I) and \
        not re.search(r"sell|sale|dispos", ttype, re.I) else \
        "SELL" if re.search(r"sell|sale|dispos", ttype + " " + mode, re.I) else \
        ("PLEDGE" if "pledge" in ttype.lower() else (ttype.upper() or "?"))
    return {
        "kind": "insider",
        "id": rid("pit", sym, person, ttype, shares, d_from, d_to),
        "symbol": sym.upper(),
        "company": norm(pick(r, "company", "COMPANY")),
        "person": person,
        "category": cat,
        "side": side,
        "type": ttype,
        "mode": mode,
        "shares": shares,
        "value": value,
        "price": (value / shares) if value and shares else None,
        "post_holding_pct": after,
        "trade_from": d_from.isoformat() if d_from else None,
        "trade_to": d_to.isoformat() if d_to else None,
        "filed": filed.isoformat() if filed else None,
        "market": bool(re.search(r"market", mode, re.I)) and not re.search(r"off", mode, re.I),
        "insider": any(c in cat.lower() for c in INSIDER_CATS),
    }


def norm_deal(r, kind):
    sym = norm(pick(r, "BD_SYMBOL", "Symbol", "SYMBOL", "symbol"))
    client = norm(pick(r, "BD_CLIENT_NAME", "Client Name", "clientName"))
    side = norm(pick(r, "BD_BUY_SELL", "Buy/Sell", "buySell")).upper()
    qty = to_float(pick(r, "BD_QTY_TRD", "Quantity Traded", "qty"))
    price = to_float(pick(r, "BD_TP_WATP", "Trade Price / Wght. Avg. Price",
                          "Trade Price / Wght. Avg. Price ", "watp") or pick_like(r, ("price",)))
    d = parse_date(pick(r, "BD_DT_DATE", "Date", "date"))
    side = "BUY" if side.startswith("B") else "SELL" if side.startswith("S") else side
    return {
        "kind": kind,
        "id": rid(kind, d, sym, client, side, qty, price),
        "symbol": sym.upper(),
        "company": norm(pick(r, "BD_SCRIP_NAME", "Security Name", "name")),
        "person": client,
        "category": kind + " deal",
        "side": side,
        "shares": qty,
        "price": price,
        "value": qty * price if qty and price else None,
        "trade_from": d.isoformat() if d else None,
        "trade_to": d.isoformat() if d else None,
        "filed": d.isoformat() if d else None,
        "remarks": norm(pick(r, "BD_REMARKS", "Remarks")),
    }


# ------------------------------------------------------------------- fetch


def window(a):
    to = dt.datetime.strptime(a.to, "%Y-%m-%d").date() if getattr(a, "to", None) else dt.date.today()
    fr = dt.datetime.strptime(a.frm, "%Y-%m-%d").date() if getattr(a, "frm", None) \
        else to - dt.timedelta(days=a.days)
    return fr, to


def fetch_pit(fr, to, symbol=None, nse=None):
    from nse_http import NSE, ddmmyyyy
    nse = nse or NSE()
    params = {"index": "equities", "from_date": ddmmyyyy(fr), "to_date": ddmmyyyy(to)}
    if symbol:
        params["symbol"] = symbol.upper()
    data = nse.get_json("corporates-pit", params)
    rows = data.get("data", []) if isinstance(data, dict) else (data or [])
    return [norm_pit(r) for r in rows]


def fetch_deals(kind, fr, to, nse=None):
    from nse_http import NSE, ddmmyyyy
    nse = nse or NSE()
    data = nse.get_json("historical/%s-deals" % kind, {"from": ddmmyyyy(fr), "to": ddmmyyyy(to)})
    rows = data.get("data", []) if isinstance(data, dict) else (data or [])
    return [norm_deal(r, kind) for r in rows]


# ------------------------------------------------------------------ output


def show(rows, title):
    print("%s: %d" % (title, len(rows)))
    if not rows:
        return
    print("%-10s %-11s %-4s %-30s %-18s %12s %14s" % (
        "traded", "symbol", "side", "who", "category", "shares", "value"))
    for r in rows:
        print("%-10s %-11s %-4s %-30s %-18s %12s %14s" % (
            (r["trade_to"] or r["filed"] or "")[:10], r["symbol"][:11], r["side"][:4],
            r["person"][:30], (r["category"] or "")[:18],
            "{:,.0f}".format(r["shares"]) if r["shares"] else "?", fmt_inr(r["value"])))


def clusters(rows, min_people=2):
    """Symbols where several different insiders bought in the window."""
    buyers = {}
    for r in rows:
        if r["kind"] == "insider" and r["side"] == "BUY" and r.get("market"):
            buyers.setdefault(r["symbol"], set()).add(r["person"].lower())
    return {s: len(p) for s, p in buyers.items() if len(p) >= min_people}


def apply_filters(rows, a):
    out = []
    for r in rows:
        if getattr(a, "symbol", None) and r["symbol"] != a.symbol.upper():
            continue
        if getattr(a, "side", None) and r["side"] != a.side.upper():
            continue
        if getattr(a, "insiders_only", False) and r["kind"] == "insider" and not r.get("insider"):
            continue
        if getattr(a, "market_only", False) and r["kind"] == "insider" and not r.get("market"):
            continue
        if getattr(a, "min_value", None) and (r["value"] or 0) < a.min_value:
            continue
        if getattr(a, "who", None) and a.who.lower() not in r["person"].lower():
            continue
        out.append(r)
    out.sort(key=lambda r: (r["trade_to"] or r["filed"] or ""), reverse=True)
    return out


def save(rows, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
        f.write("\n")


def cmd_pit(a):
    if a.file:
        rows = [norm_pit(r) for r in load_rows(a.file)]
    else:
        fr, to = window(a)
        rows = fetch_pit(fr, to, a.symbol)
    rows = apply_filters(rows, a)
    show(rows, "Insider trades")
    cl = clusters(rows)
    if cl:
        print("\nCluster buying (2+ insiders bought on market): " +
              ", ".join("%s (%d people)" % kv for kv in sorted(cl.items(), key=lambda kv: -kv[1])))
    if a.save:
        save(rows, a.save)
        print("\nsaved " + a.save)


def cmd_deals(a):
    if a.file:
        rows = [norm_deal(r, a.kind) for r in load_rows(a.file)]
    else:
        fr, to = window(a)
        rows = fetch_deals(a.kind, fr, to)
    rows = apply_filters(rows, a)
    show(rows, a.kind.capitalize() + " deals")
    if a.save:
        save(rows, a.save)
        print("\nsaved " + a.save)


def watchlist():
    """Symbols to watch: trader/watchlist.json + targets + paper portfolio."""
    syms = set()
    for path, getter in (
        (os.path.join(TRADER, "watchlist.json"), lambda d: d.get("symbols", d) if isinstance(d, dict) else d),
        (os.path.join(TRADER, "targets.json"), lambda d: [t.get("symbol") for t in d.get("targets", [])]),
        (os.path.join(TRADER, "portfolio.json"), lambda d: list(d.get("positions", {}).keys())),
    ):
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    syms.update(s.upper() for s in getter(json.load(f)) if s)
            except (ValueError, AttributeError):
                pass
    return syms


def cmd_new(a):
    rows, errors = [], []
    if a.file:
        for path in a.file:
            low = os.path.basename(path).lower()
            kind = "block" if "block" in low else "bulk" if "bulk" in low else "insider"
            raw = load_rows(path)
            rows += [norm_pit(r) for r in raw] if kind == "insider" else [norm_deal(r, kind) for r in raw]
    else:
        from nse_http import NSE, NSEError
        nse = NSE()
        fr, to = window(a)
        for label, fn in (("insider", lambda: fetch_pit(fr, to, nse=nse)),
                          ("bulk", lambda: fetch_deals("bulk", fr, to, nse)),
                          ("block", lambda: fetch_deals("block", fr, to, nse))):
            try:
                rows += fn()
            except NSEError as e:
                errors.append("%s: %s" % (label, e))
    watch = watchlist()
    names = [w.lower() for w in (a.who or [])]
    keep = []
    for r in rows:
        on_watch = r["symbol"] in watch
        named = any(n in r["person"].lower() for n in names)
        big_insider_buy = (r["kind"] == "insider" and r["side"] == "BUY" and r.get("market")
                           and r.get("insider") and (r["value"] or 0) >= a.min_value)
        if on_watch or named or (a.all_big and big_insider_buy):
            keep.append(r)
    seen = set()
    if os.path.exists(SEEN):
        with open(SEEN, encoding="utf-8") as f:
            seen = set(json.load(f))
    fresh = [r for r in keep if r["id"] not in seen]
    fresh.sort(key=lambda r: (r["symbol"], r["trade_to"] or ""))
    for e in errors:
        print("warning: could not fetch " + e, file=sys.stderr)
    if not watch and not names and not a.all_big:
        print("note: watchlist is empty. Add symbols to trader/watchlist.json, "
              "names with --who, or use --all-big.")
    if not fresh:
        print("No new filings for your watchlist.")
    else:
        print("NEW since last check (%d):\n" % len(fresh))
        for r in fresh:
            why = "watchlist" if r["symbol"] in watch else "name match" if any(
                n in r["person"].lower() for n in names) else "large insider buy"
            print("- %s %s: %s (%s) %s %s shares, %s  [%s, traded %s]" % (
                r["symbol"], r["kind"], r["person"], r["category"], r["side"],
                "{:,.0f}".format(r["shares"]) if r["shares"] else "?", fmt_inr(r["value"]),
                why, r["trade_to"] or r["filed"]))
        cl = clusters(fresh)
        if cl:
            print("\nCluster buying: " + ", ".join("%s (%d people)" % kv for kv in cl.items()))
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
        save(fresh, os.path.join(TRADER, "alerts", "insider_%s.json" % stamp))
    if not a.dry_run:
        seen.update(r["id"] for r in keep)
        os.makedirs(TRADER, exist_ok=True)
        with open(SEEN, "w", encoding="utf-8") as f:
            json.dump(sorted(seen), f)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(s, days):
        s.add_argument("--days", type=int, default=days, help="look-back window (default %d)" % days)
        s.add_argument("--from", dest="frm", help="YYYY-MM-DD")
        s.add_argument("--to", help="YYYY-MM-DD")
        s.add_argument("--symbol")
        s.add_argument("--side", choices=["buy", "sell"])
        s.add_argument("--who", help="only rows whose person/client contains this text")
        s.add_argument("--min-value", type=float, help="minimum rupee value")
        s.add_argument("--save", help="write the rows to this JSON file")

    s = sub.add_parser("pit", help="insider trades (SEBI PIT)")
    common(s, 30)
    s.add_argument("--file", help="CSV/JSON downloaded from NSE's insider-trading page")
    s.add_argument("--insiders-only", action="store_true",
                   help="promoters, directors, KMP and relatives only")
    s.add_argument("--market-only", action="store_true",
                   help="open-market trades only (skip ESOPs, gifts, inter-se transfers)")
    s.set_defaults(fn=cmd_pit)

    s = sub.add_parser("deals", help="bulk or block deals")
    common(s, 7)
    s.add_argument("--kind", choices=["bulk", "block"], default="bulk")
    s.add_argument("--file", help="CSV/JSON downloaded from NSE's bulk/block deals page")
    s.set_defaults(fn=cmd_deals)

    s = sub.add_parser("new", help="only new filings for your watchlist (for schedules)")
    s.add_argument("--days", type=int, default=5)
    s.add_argument("--from", dest="frm")
    s.add_argument("--to")
    s.add_argument("--who", nargs="*", help="investor names to always flag, e.g. KACHOLIA DAMANI")
    s.add_argument("--all-big", action="store_true",
                   help="also flag any market buy by an insider above --min-value")
    s.add_argument("--min-value", type=float, default=1e7, help="default ₹1 crore")
    s.add_argument("--file", nargs="*", help="downloaded files instead of fetching "
                   "(name them *insider*, *bulk*, *block*)")
    s.add_argument("--dry-run", action="store_true", help="don't mark anything as seen")
    s.set_defaults(fn=cmd_new)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
