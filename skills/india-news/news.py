#!/usr/bin/env python3
"""india-news: watch exchange announcements and headlines for every stock
you hold, and flag what matters.

Two sources:
1. NSE corporate announcements: what the company itself files with the
   exchange (results, board meetings, dividends, resignations, pledges,
   credit ratings, orders, acquisitions...). India's version of 8-Ks.
2. Headlines from Google News' public RSS search (India edition).

Each item gets a flag: HIGH (auditor/CEO/CFO exit, default, fraud, SEBI
order, pledge invoked, rating downgrade...), MEDIUM (results, dividend,
bonus, split, buyback, acquisition, fund raise, big order...), or INFO.

Commands
  scan [--symbols A,B] [--hours 24] [--new]   announcements + headlines
       --ann-file F  --rss-file SYMBOL=F      use downloaded files instead

Stocks default to your watchlist: trader/watchlist.json, targets.json and
portfolio.json. Standard library only. Python 3.8+.
"""

import argparse
import csv
import datetime as dt
import email.utils
import hashlib
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TRADER = os.environ.get("TRADER_DIR", "trader")
SEEN = os.path.join(TRADER, "news_seen.json")
GNEWS = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

HIGH = [r"resign\w* .*(auditor|ceo|cfo|managing director|chief)", r"auditor\W.*resign",
        r"(chief executive|chief financial|managing director|\bceo\b|\bcfo\b|\bmd\b).*resign",
        r"\bdefault", r"\bfraud", r"forensic", r"sebi (order|penalty|ban|show cause)",
        r"show[- ]cause", r"\bpenalt", r"invocation of pledge|pledge invo|pledge invoked",
        r"downgrad", r"insolvency|\bnclt\b|\bcirp\b", r"\braid|search and seizure|income tax search",
        r"suspension of trading|delist", r"\bed\b (raid|probe)|enforcement directorate",
        r"qualified opinion|disclaimer of opinion", r"profit warning|net loss"]
MEDIUM = [r"financial results|quarterly results|\bq[1-4]\b.*results|earnings", r"board meeting",
          r"dividend", r"\bbonus\b", r"split|sub-division", r"buy ?back", r"acqui|merger|amalgamation|demerger",
          r"\bqip\b|preferential|rights issue|fund rais", r"order (win|inflow|received)|bags .*order|wins .*order",
          r"credit rating|\brating\b", r"\bpledge", r"\bopen offer", r"block deal|bulk deal|stake sale|ofs\b",
          r"upgrad", r"guidance", r"\busfda|warning letter|form 483", r"capex|expansion"]
NOISE = [r"loss of share certificate|duplicate share certificate", r"newspaper (publication|advertisement)",
         r"trading window", r"\besop\b|\besos\b", r"investor (meet|presentation)|analyst.*meet|con call|conference call",
         r"compliance certificate|regulation 74\(5\)|regulation 7\(3\)"]


def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def flag(text):
    t = text.lower()
    if any(re.search(p, t) for p in HIGH):
        return "HIGH"
    if any(re.search(p, t) for p in NOISE):
        return "NOISE"
    if any(re.search(p, t) for p in MEDIUM):
        return "MEDIUM"
    return "INFO"


def parse_dt(s):
    s = norm(s)
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s)  # dates we stored ourselves
        return d if d.tzinfo else d.replace(tzinfo=IST)
    except ValueError:
        pass
    for f in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d %H:%M:%S",
              "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try:
            return dt.datetime.strptime(s, f).replace(tzinfo=IST)
        except ValueError:
            pass
    try:
        d = email.utils.parsedate_to_datetime(s)  # RSS pubDate
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def watchlist():
    syms = set()
    for path, get in (
        ("watchlist.json", lambda d: d.get("symbols", d) if isinstance(d, dict) else d),
        ("targets.json", lambda d: [t.get("symbol") for t in d.get("targets", [])]),
        ("portfolio.json", lambda d: list(d.get("positions", {}).keys())),
    ):
        p = os.path.join(TRADER, path)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    syms.update(s.upper() for s in get(json.load(f)) if s)
            except (ValueError, AttributeError):
                pass
    return sorted(syms)


def names():
    """Company names, to make headline searches precise."""
    out = {}
    for path in ("targets.json",):
        p = os.path.join(TRADER, path)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for t in json.load(f).get("targets", []):
                    if t.get("symbol") and t.get("name"):
                        out[t["symbol"].upper()] = t["name"]
    return out


# ------------------------------------------------------------ sources


def ann_item(r):
    def g(*keys):
        for k in keys:
            if r.get(k) not in (None, "", "-"):
                return r[k]
        low = {re.sub(r"[^a-z]", "", k.lower()): k for k in r}
        for k in keys:
            kk = low.get(re.sub(r"[^a-z]", "", k.lower()))
            if kk and r.get(kk) not in (None, "", "-"):
                return r[kk]
        return None
    subject = norm(g("desc", "SUBJECT", "subject"))
    detail = norm(g("attchmntText", "DETAILS", "details"))
    when = parse_dt(g("an_dt", "sort_date", "BROADCAST DATE/TIME", "broadcastDate", "exchdisstime"))
    text = (subject + ". " + detail).strip(". ")
    return {"kind": "announcement", "symbol": norm(g("symbol", "SYMBOL")).upper(),
            "title": subject or detail[:120], "detail": detail[:400],
            "when": when.isoformat() if when else None,
            "link": g("attchmntFile", "ATTACHMENT"), "source": "NSE",
            "flag": flag(text)}


def load_ann_file(path):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    t = text.lstrip()
    if t[:1] in "[{":
        data = json.loads(t)
        rows = data.get("data", data) if isinstance(data, dict) else data
    else:
        rdr = csv.DictReader(io.StringIO(text))
        rows = [{norm(k): v for k, v in r.items() if k} for r in rdr]
    return [ann_item(r) for r in rows]


def fetch_announcements(symbol, since, nse):
    from nse_http import ddmmyyyy
    data = nse.get_json("corporate-announcements", {
        "index": "equities", "symbol": symbol,
        "from_date": ddmmyyyy(since.date()), "to_date": ddmmyyyy(dt.date.today())})
    rows = data.get("data", data) if isinstance(data, dict) else (data or [])
    return [ann_item(r) for r in rows]


def parse_rss(xml_bytes, symbol):
    items = []
    root = ET.fromstring(xml_bytes)
    for it in root.iter("item"):
        title = norm(it.findtext("title"))
        src = it.find("source")
        if src is not None and src.text:  # Google News appends " - Publisher"
            title = re.sub(r"\s+-\s+%s$" % re.escape(norm(src.text)), "", title)
        when = parse_dt(it.findtext("pubDate"))
        items.append({"kind": "headline", "symbol": symbol, "title": title,
                      "when": when.isoformat() if when else None, "link": norm(it.findtext("link")),
                      "source": norm(src.text) if src is not None else "news", "flag": flag(title)})
    return items


def fetch_headlines(symbol, company=None):
    q = '"%s" share OR stock' % (company or symbol)
    req = urllib.request.Request(GNEWS % urllib.parse.quote(q), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return parse_rss(r.read(), symbol)


# --------------------------------------------------------------- scan


def item_id(i):
    return hashlib.sha1(("%s|%s|%s" % (i["symbol"], i["title"], i["when"])).lower().encode()).hexdigest()[:16]


def cmd_scan(a):
    symbols = [s.strip().upper() for s in a.symbols.split(",")] if a.symbols else watchlist()
    if not symbols and not a.ann_file:
        print("No stocks to watch. Pass --symbols or add trader/watchlist.json.")
        return
    since = dt.datetime.now(IST) - dt.timedelta(hours=a.hours)
    items, errors = [], []
    if a.ann_file:
        for f in a.ann_file:
            items += load_ann_file(f)
    elif not a.no_announcements:
        from nse_http import NSE, NSEError
        nse = NSE()
        for s in symbols:
            try:
                items += fetch_announcements(s, since, nse)
            except NSEError as e:
                errors.append("announcements %s: %s" % (s, e))
    if a.rss_file:
        for spec in a.rss_file:
            sym, path = spec.split("=", 1)
            with open(path, "rb") as f:
                items += parse_rss(f.read(), sym.upper())
    elif not a.no_headlines and not a.ann_file:
        cos = names()
        for s in symbols:
            try:
                items += fetch_headlines(s, cos.get(s))
            except Exception as e:  # noqa: BLE001
                errors.append("headlines %s: %s" % (s, e))

    keep = []
    for i in items:
        if symbols and i["symbol"] not in symbols:
            continue
        w = parse_dt(i["when"]) if i["when"] else None
        if w and w < since:
            continue
        if i["flag"] == "NOISE" and not a.all:
            continue
        if a.min_flag == "MEDIUM" and i["flag"] not in ("HIGH", "MEDIUM"):
            continue
        if a.min_flag == "HIGH" and i["flag"] != "HIGH":
            continue
        i["id"] = item_id(i)
        keep.append(i)
    # de-duplicate near-identical headlines
    seen_titles, uniq = set(), []
    for i in keep:
        k = (i["symbol"], re.sub(r"[^a-z0-9]", "", i["title"].lower())[:60])
        if k not in seen_titles:
            seen_titles.add(k)
            uniq.append(i)
    keep = uniq

    seen = set()
    if a.new and os.path.exists(SEEN):
        with open(SEEN, encoding="utf-8") as f:
            seen = set(json.load(f))
    fresh = [i for i in keep if i["id"] not in seen]

    for e in errors:
        print("warning: " + e, file=sys.stderr)
    rank = {"HIGH": 0, "MEDIUM": 1, "INFO": 2, "NOISE": 3}
    fresh.sort(key=lambda i: (i["symbol"], rank[i["flag"]], -(parse_dt(i["when"]).timestamp() if i["when"] else 0)))
    if not fresh:
        print("Nothing %sin the last %d h for %s." % ("new " if a.new else "", a.hours,
                                                      ", ".join(symbols) or "your stocks"))
    else:
        cur = None
        for i in fresh:
            if i["symbol"] != cur:
                cur = i["symbol"]
                print("\n" + cur)
            when = parse_dt(i["when"]).astimezone(IST).strftime("%d %b %H:%M") if i["when"] else "?"
            print("  [%-6s] %s  %s (%s)" % (i["flag"], when, i["title"][:110], i["source"]))
        high = [i for i in fresh if i["flag"] == "HIGH"]
        print("\n%d items, %d HIGH." % (len(fresh), len(high)))
        os.makedirs(os.path.join(TRADER, "news"), exist_ok=True)
        out = os.path.join(TRADER, "news", "news_%s.json" % dt.datetime.now().strftime("%Y%m%d-%H%M"))
        with open(out, "w", encoding="utf-8") as f:
            json.dump(fresh, f, indent=2, ensure_ascii=False)
    if a.new:
        seen.update(i["id"] for i in keep)
        os.makedirs(TRADER, exist_ok=True)
        with open(SEEN, "w", encoding="utf-8") as f:
            json.dump(sorted(seen)[-5000:], f)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--symbols", help="comma-separated NSE symbols (default: watchlist)")
    s.add_argument("--hours", type=int, default=24)
    s.add_argument("--new", action="store_true", help="only items not shown before (for schedules)")
    s.add_argument("--min-flag", choices=["INFO", "MEDIUM", "HIGH"], default="INFO")
    s.add_argument("--all", action="store_true", help="include routine filings (NOISE)")
    s.add_argument("--no-headlines", action="store_true")
    s.add_argument("--no-announcements", action="store_true")
    s.add_argument("--ann-file", nargs="*", help="announcements CSV/JSON downloaded from NSE")
    s.add_argument("--rss-file", nargs="*", help="SYMBOL=rss.xml saved headline feeds")
    s.set_defaults(fn=cmd_scan)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
