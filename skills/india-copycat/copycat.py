#!/usr/bin/env python3
"""india-copycat: rebuild a mutual fund's or a big investor's Indian stock
portfolio as weights you can copy.

Two sources, both public:

1. Mutual funds (the main one). SEBI makes every AMC publish each scheme's
   full portfolio every month, as a spreadsheet, with ISIN and "% to NAV"
   for every holding. Download the month's file from the AMC's website
   ("Monthly Portfolio Disclosure") and point this tool at it.

2. Individual "superstar" investors (experimental). Listed companies file a
   shareholding pattern every quarter that names every public shareholder
   holding more than 1 %. Give the tool the investor's name and the stocks
   to check, and it reads each company's latest filing from NSE.

Commands
  sheets   FILE                         list the sheets in an AMC workbook
  mf       FILE [--sheet S] [--label L] parse a scheme's portfolio, save it
  diff     OLD.json NEW.json            what was bought, added, trimmed, sold
  copy     HOLDINGS.json --top N        write trader/targets.json
  symbols  [--fetch | --file CSV]       ISIN -> NSE symbol table (EQUITY_L.csv)
  investor NAME --symbols A,B,C         holdings of a named shareholder (>1 %)

Standard library only. Python 3.8+.
"""

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TRADER = os.environ.get("TRADER_DIR", "trader")
EQUITY_LIST = os.path.join(TRADER, "nse_equity_list.csv")
EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

# ---------------------------------------------------------------- utilities


def die(msg, code=1):
    print("error: " + msg, file=sys.stderr)
    sys.exit(code)


def ensure_dir(path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def write_json(path, obj):
    ensure_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def to_float(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).replace(",", "").replace("%", "").replace("₹", "").strip()
    if t in ("", "-", "--", "NIL", "Nil", "nil", "NA", "N.A."):
        return None
    if t.startswith("(") and t.endswith(")"):
        t = "-" + t[1:-1]
    try:
        return float(t)
    except ValueError:
        return None


ISIN_RE = re.compile(r"^IN[A-Z0-9]{9}[0-9]$")


def is_equity_isin(isin):
    """Indian ISINs: IN + E (company) + 4 issuer + 2 security type + ...
    Security type '01' is equity shares. Mutual funds hold other things
    too (bonds 'INE...07...', T-bills 'IN00...', units 'INF...')."""
    return bool(ISIN_RE.match(isin or "")) and isin[2] == "E" and isin[7:9] == "01"


# ------------------------------------------------------- spreadsheet reading

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _col_index(ref):
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def xlsx_sheets(path):
    """Return [(sheet_name, zip_member_path)] in workbook order."""
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels.iter(NS_PKG + "Relationship")}
        out = []
        for s in wb.iter(NS_MAIN + "sheet"):
            t = targets.get(s.get(NS_REL + "id"), "")
            t = t.lstrip("/")
            if not t.startswith("xl/"):
                t = "xl/" + t
            out.append((s.get("name"), t))
        return out


def xlsx_rows(path, sheet=None):
    """Read one sheet of an .xlsx into a list of rows (lists of strings)."""
    sheets = xlsx_sheets(path)
    if not sheets:
        die("no sheets in " + path)
    member = sheets[0][1]
    if sheet is not None:
        match = [m for n, m in sheets if n == sheet] or \
                [m for n, m in sheets if n.lower() == str(sheet).lower()]
        if not match and str(sheet).isdigit() and int(sheet) < len(sheets):
            match = [sheets[int(sheet)][1]]
        if not match:
            die("sheet %r not found. Sheets: %s" % (sheet, ", ".join(n for n, _ in sheets)))
        member = match[0]
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in sst.iter(NS_MAIN + "si"):
                shared.append("".join(t.text or "" for t in si.iter(NS_MAIN + "t")))
        root = ET.fromstring(z.read(member))
    rows = []
    for r in root.iter(NS_MAIN + "row"):
        cells = {}
        for c in r.iter(NS_MAIN + "c"):
            ref, typ = c.get("r"), c.get("t")
            v = c.find(NS_MAIN + "v")
            if typ == "s" and v is not None:
                val = shared[int(v.text)]
            elif typ == "inlineStr":
                val = "".join(t.text or "" for t in c.iter(NS_MAIN + "t"))
            else:
                val = v.text if v is not None else ""
            if ref:
                cells[_col_index(ref)] = val
        if cells:
            row = [""] * (max(cells) + 1)
            for i, val in cells.items():
                row[i] = val
            rows.append(row)
        else:
            rows.append([])
    return rows


def read_table(path, sheet=None):
    low = path.lower()
    if low.endswith((".xlsx", ".xlsm")):
        return xlsx_rows(path, sheet)
    if low.endswith(".xls"):
        die("old .xls format: open it and 'Save As' .xlsx or .csv, then retry")
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return [row for row in csv.reader(f)]


# --------------------------------------------------- AMC portfolio parsing

PCT_HINTS = ("% to nav", "% of nav", "% to net assets", "% of net assets",
             "% to aum", "% of aum", "% to net asset", "percentage to nav",
             "% to nav ", "% nav", "% net assets")


def find_header(rows):
    """Locate the header row: it has an ISIN column and a '% to NAV' column."""
    for i, row in enumerate(rows[:80]):
        low = [norm(c).lower() for c in row]
        isin = [j for j, c in enumerate(low) if c == "isin" or c.startswith("isin")]
        pct = [j for j, c in enumerate(low)
               if any(h in c for h in PCT_HINTS) or (("%" in c or "percent" in c) and
                                                     ("nav" in c or "net asset" in c or "aum" in c))]
        if isin and pct:
            cols = {"isin": isin[0], "pct": pct[0]}
            for j, c in enumerate(low):
                if "name" not in cols and ("name" in c or "instrument" in c or "company" in c
                                           or "security" in c or "issuer" in c):
                    cols["name"] = j
                elif "industry" not in cols and ("industry" in c or "sector" in c):
                    cols["industry"] = j
                elif "quantity" not in cols and ("quantity" in c or c in ("qty", "no. of shares")):
                    cols["quantity"] = j
                elif "value" not in cols and ("market value" in c or "market/fair value" in c
                                              or "exposure" in c or "value" in c):
                    cols["value"] = j
            cols.setdefault("name", max(0, cols["isin"] - 1))
            return i, cols
    return None, None


AS_OF_RES = [
    re.compile(r"as\s+(?:on|of|at)\s*[:\-]?\s*(\d{1,2})[\s\-/.]*([A-Za-z]{3,9})[\s\-/.,]*(\d{4})", re.I),
    re.compile(r"as\s+(?:on|of|at)\s*[:\-]?\s*([A-Za-z]{3,9})\s+(\d{1,2}),?\s*(\d{4})", re.I),
    re.compile(r"as\s+(?:on|of|at)\s*[:\-]?\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", re.I),
]
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def find_as_of(rows):
    for row in rows[:15]:
        text = " ".join(norm(c) for c in row)
        for k, rx in enumerate(AS_OF_RES):
            m = rx.search(text)
            if not m:
                continue
            try:
                if k == 0:
                    d, mon, y = int(m.group(1)), MONTHS[m.group(2)[:3].lower()], int(m.group(3))
                elif k == 1:
                    mon, d, y = MONTHS[m.group(1)[:3].lower()], int(m.group(2)), int(m.group(3))
                else:
                    d, mon, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                return dt.date(y, mon, d).isoformat()
            except (KeyError, ValueError):
                continue
    return None


def find_scheme(rows, header_idx):
    for row in rows[:header_idx]:
        for c in row:
            t = norm(c)
            if re.search(r"\bfund\b|\bscheme\b|\bETF\b", t, re.I) and 6 < len(t) < 120 \
                    and not re.search(r"^portfolio|statement|as on|mutual fund$", t, re.I):
                return t
    return None


def parse_portfolio(rows):
    """Return (holdings, meta). Holdings are equity lines only, % of NAV."""
    hi, cols = find_header(rows)
    if hi is None:
        die("could not find a header row with 'ISIN' and '% to NAV' columns. "
            "If the workbook has one sheet per scheme, pick it with --sheet "
            "(see the 'sheets' command).")
    raw = []
    for row in rows[hi + 1:]:
        def cell(key):
            j = cols.get(key)
            return norm(row[j]) if j is not None and j < len(row) else ""
        isin = cell("isin").upper()
        if not is_equity_isin(isin):
            continue
        pct = to_float(cell("pct"))
        if pct is None:
            continue
        raw.append({
            "isin": isin,
            "name": cell("name"),
            "industry": cell("industry") or None,
            "quantity": to_float(cell("quantity")),
            "market_value": to_float(cell("value")),
            "pct_nav": pct,
        })
    if not raw:
        die("header found but no equity rows (ISIN 'INE....01....'). Is this a debt scheme?")
    # Some AMCs write 0.0912 for 9.12 %. Fractions never sum above ~1.
    total = sum(h["pct_nav"] for h in raw)
    if total <= 1.05 and max(h["pct_nav"] for h in raw) <= 1:
        for h in raw:
            h["pct_nav"] *= 100
    # The same ISIN can appear twice (e.g. partly paid, or listed under two
    # headings). Merge.
    merged = {}
    for h in raw:
        m = merged.get(h["isin"])
        if m is None:
            merged[h["isin"]] = dict(h)
        else:
            m["pct_nav"] += h["pct_nav"]
            for k in ("quantity", "market_value"):
                if h[k] is not None:
                    m[k] = (m[k] or 0) + h[k]
    holdings = sorted(merged.values(), key=lambda h: -h["pct_nav"])
    for h in holdings:
        h["pct_nav"] = round(h["pct_nav"], 4)
    meta = {
        "scheme": find_scheme(rows, hi),
        "as_of": find_as_of(rows),
        "equity_pct_nav": round(sum(h["pct_nav"] for h in holdings), 2),
        "lines": len(holdings),
    }
    return holdings, meta


# ------------------------------------------------------- ISIN -> NSE symbol


def load_symbol_map(path=EQUITY_LIST):
    """Map ISIN -> (SYMBOL, SERIES) from NSE's EQUITY_L.csv."""
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        rdr = csv.reader(f)
        head = [norm(h).upper() for h in next(rdr, [])]
        try:
            si, ii = head.index("SYMBOL"), head.index("ISIN NUMBER")
        except ValueError:
            return {}
        ser = head.index("SERIES") if "SERIES" in head else None
        for row in rdr:
            if len(row) > max(si, ii):
                out[norm(row[ii]).upper()] = (norm(row[si]).upper(),
                                              norm(row[ser]) if ser is not None else "EQ")
    return out


def attach_symbols(holdings):
    smap = load_symbol_map()
    missing = 0
    for h in holdings:
        sym = smap.get(h["isin"])
        h["symbol"] = sym[0] if sym else None
        h["series"] = sym[1] if sym else None
        if not sym:
            missing += 1
    return missing, bool(smap)


# ----------------------------------------------------------------- commands


def cmd_sheets(a):
    if not a.file.lower().endswith((".xlsx", ".xlsm")):
        die("only .xlsx workbooks have sheets")
    for i, (name, _) in enumerate(xlsx_sheets(a.file)):
        print("%3d  %s" % (i, name))


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "fund").lower()).strip("-")[:60] or "fund"


def cmd_mf(a):
    rows = read_table(a.file, a.sheet)
    holdings, meta = parse_portfolio(rows)
    if a.as_of:
        meta["as_of"] = a.as_of
    if a.scheme:
        meta["scheme"] = a.scheme
    missing, have_map = attach_symbols(holdings)
    meta.update({"source": "AMC monthly portfolio", "file": os.path.basename(a.file),
                 "sheet": a.sheet})
    label = a.label or slug(meta["scheme"])
    out = a.out or os.path.join(TRADER, "holdings", "%s_%s.json" % (label, meta["as_of"] or "undated"))
    write_json(out, {"meta": meta, "holdings": holdings})

    print("%s  (as of %s)" % (meta["scheme"] or "scheme", meta["as_of"] or "unknown date"))
    print("%d equity lines, %.2f %% of NAV in equity\n" % (meta["lines"], meta["equity_pct_nav"]))
    print("%-4s %-12s %-12s %-40s %8s" % ("#", "symbol", "isin", "name", "% NAV"))
    for i, h in enumerate(holdings[: a.show], 1):
        print("%-4d %-12s %-12s %-40s %8.2f" % (i, h["symbol"] or "?", h["isin"],
                                                h["name"][:40], h["pct_nav"]))
    if len(holdings) > a.show:
        print("... %d more" % (len(holdings) - a.show))
    if not have_map:
        print("\nnote: no ISIN->symbol table yet. Run: copycat.py symbols --fetch")
    elif missing:
        print("\nnote: %d ISIN(s) not on NSE's equity list (BSE-only, new listing, "
              "or delisted). They are kept but cannot be ordered until mapped." % missing)
    if not meta["as_of"]:
        print("note: could not read the 'as on' date; pass --as-of YYYY-MM-DD")
    print("\nsaved " + out)


def cmd_diff(a):
    old, new = read_json(a.old), read_json(a.new)
    o = {h["isin"]: h for h in old["holdings"]}
    n = {h["isin"]: h for h in new["holdings"]}
    key = "pct_nav" if "pct_nav" in next(iter(n.values()), {}) else "weight"
    bought = [n[i] for i in n if i not in o]
    sold = [o[i] for i in o if i not in n]
    changed = []
    for i in n:
        if i in o:
            q0, q1 = o[i].get("quantity"), n[i].get("quantity")
            w0, w1 = o[i].get(key, 0), n[i].get(key, 0)
            if q0 and q1:
                ch = (q1 - q0) / q0 * 100
                basis = "shares"
            else:
                ch = w1 - w0
                basis = "weight pts"
            if abs(ch) >= a.min_change:
                changed.append((n[i], ch, basis, w0, w1))
    print("%s -> %s" % (old["meta"].get("as_of"), new["meta"].get("as_of")))
    print("\nNEW (%d)" % len(bought))
    for h in sorted(bought, key=lambda h: -h.get(key, 0)):
        print("  + %-12s %-40s %6.2f %%" % (h.get("symbol") or h["isin"], h["name"][:40], h.get(key, 0)))
    print("\nEXITED (%d)" % len(sold))
    for h in sorted(sold, key=lambda h: -h.get(key, 0)):
        print("  - %-12s %-40s was %5.2f %%" % (h.get("symbol") or h["isin"], h["name"][:40], h.get(key, 0)))
    adds = sorted([c for c in changed if c[1] > 0], key=lambda c: -c[1])
    trims = sorted([c for c in changed if c[1] < 0], key=lambda c: c[1])
    for title, items in (("ADDED", adds), ("TRIMMED", trims)):
        print("\n%s (%d)" % (title, len(items)))
        for h, ch, basis, w0, w1 in items:
            unit = "%" if basis == "shares" else " pts"
            print("  %-12s %-34s %+7.1f%s (%s)  weight %.2f -> %.2f" % (
                h.get("symbol") or h["isin"], h["name"][:34], ch, unit, basis, w0, w1))
    if a.json:
        write_json(a.json, {"from": old["meta"].get("as_of"), "to": new["meta"].get("as_of"),
                            "new": bought, "exited": sold,
                            "added": [c[0]["isin"] for c in adds],
                            "trimmed": [c[0]["isin"] for c in trims]})


def renormalise(items, key, top):
    pick = [h for h in items if h.get(key)][:top]
    total = sum(h[key] for h in pick)
    out = []
    for h in pick:
        out.append({"symbol": h.get("symbol"), "isin": h.get("isin"), "name": h.get("name"),
                    "series": h.get("series"),
                    "source_weight": round(h[key], 4),
                    "weight": round(h[key] / total * 100, 2)})
    # make the rounded weights add to exactly 100.00
    if out:
        drift = round(100 - sum(t["weight"] for t in out), 2)
        out[0]["weight"] = round(out[0]["weight"] + drift, 2)
    return out


def cmd_copy(a):
    data = read_json(a.holdings)
    items = data["holdings"]
    key = "pct_nav" if items and "pct_nav" in items[0] else "weight"
    if a.skip_unmapped:
        items = [h for h in items if h.get("symbol")]
    targets = renormalise(items, key, a.top)
    unmapped = [t for t in targets if not t["symbol"]]
    out = {"source": data["meta"].get("scheme") or data["meta"].get("investor"),
           "source_kind": data["meta"].get("source"),
           "as_of": data["meta"].get("as_of"),
           "created": dt.datetime.now().isoformat(timespec="seconds"),
           "top": a.top, "targets": targets}
    write_json(a.out, out)
    print("Top %d of %s (as of %s), renormalised to 100 %%:\n" % (
        len(targets), out["source"], out["as_of"]))
    for t in targets:
        print("  %-12s %-40s %6.2f %%   (was %.2f %%)" % (
            t["symbol"] or "?" + t["isin"], (t["name"] or "")[:40], t["weight"], t["source_weight"]))
    if unmapped:
        print("\nwarning: %d target(s) have no NSE symbol. Run 'symbols --fetch' "
              "or use --skip-unmapped." % len(unmapped))
    print("\nwrote " + a.out)


def cmd_symbols(a):
    ensure_dir(EQUITY_LIST)
    if a.file:
        with open(a.file, "rb") as src, open(EQUITY_LIST, "wb") as dst:
            dst.write(src.read())
    elif a.fetch:
        from nse_http import NSE
        data = NSE().get_bytes(EQUITY_LIST_URL)
        with open(EQUITY_LIST, "wb") as f:
            f.write(data)
    smap = load_symbol_map()
    if not smap:
        die("no symbol table. Use --fetch, or download EQUITY_L.csv from "
            "nseindia.com (Market Data > Securities available for trading) and pass --file")
    print("%d NSE equity symbols in %s" % (len(smap), EQUITY_LIST))
    for q in a.lookup or []:
        q = q.upper()
        hits = [(i, s) for i, s in smap.items() if q in (i, s[0])]
        print("  %s -> %s" % (q, ", ".join("%s %s" % (s[0], i) for i, s in hits) or "not found"))


# ------------------------------------------------- shareholding (investor)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_shp_xbrl(xml_bytes):
    """Pull named shareholders out of a SEBI shareholding-pattern XBRL file.

    The taxonomy repeats the same facts once per shareholder, each under its
    own context. Rather than hard-code tag names (which have changed over the
    years), group facts by context and keep contexts that have a name and a
    percentage. Returns [{"name", "pct", "shares", "pan_masked"}]."""
    root = ET.fromstring(xml_bytes)
    by_ctx = {}
    for el in root.iter():
        ctx = el.get("contextRef")
        if not ctx or el.text is None:
            continue
        by_ctx.setdefault(ctx, {})[_local(el.tag)] = el.text.strip()
    holders = []
    for ctx, facts in by_ctx.items():
        name = next((v for k, v in facts.items()
                     if k.lower().startswith("nameof") and ("share" in k.lower() or "holder" in k.lower()
                                                            or "person" in k.lower())), None)
        pct_key = next((k for k in facts if "percentage" in k.lower() and "shareholding" in k.lower()
                        and "diluted" not in k.lower()), None) or \
            next((k for k in facts if "percentage" in k.lower() and "share" in k.lower()), None)
        if not name or not pct_key:
            continue
        pct = to_float(facts[pct_key])
        if pct is None:
            continue
        if pct <= 1:  # XBRL stores 0.0123 for 1.23 %; named holders are all >1 %
            pct *= 100
        shares_key = next((k for k in facts if "numberoffullypaidupequityshares" in k.lower()), None) or \
            next((k for k in facts if k.lower().startswith("totalnumberofshares")), None)
        holders.append({"name": norm(name), "pct": round(pct, 4),
                        "shares": to_float(facts.get(shares_key)) if shares_key else None})
    # de-duplicate (same holder can appear in a category total and its own row)
    seen, out = set(), []
    for h in sorted(holders, key=lambda h: -h["pct"]):
        k = (h["name"].lower(), h["pct"])
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out


def _find_xbrl_url(record):
    for k, v in record.items():
        if isinstance(v, str) and v.lower().endswith(".xml") and v.startswith("http"):
            return v
    return None


def cmd_investor(a):
    names = [n.strip().lower() for n in a.name.split("|") if n.strip()]
    results, errors = [], []
    sources = []
    if a.xbrl:
        for item in a.xbrl:
            if "=" in item:
                sym, path = item.split("=", 1)
            else:
                path = item
                parts = re.split(r"[_\-. ]+", os.path.splitext(os.path.basename(path))[0])
                sym = next((p for p in parts if p.lower() not in
                            ("shp", "shareholding", "pattern", "xbrl", "sh")), parts[0])
            sym = sym.upper()
            with open(path, "rb") as f:
                sources.append((sym, None, f.read()))
    else:
        if not a.symbols:
            die("give --symbols A,B,C (stocks to check) or --xbrl FILE ...")
        from nse_http import NSE, NSEError
        nse = NSE()
        for sym in [s.strip().upper() for s in a.symbols.split(",") if s.strip()]:
            try:
                recs = nse.get_json("corporate-share-holdings-master",
                                    {"index": "equities", "symbol": sym})
                recs = recs.get("data", recs) if isinstance(recs, dict) else recs
                if not recs:
                    errors.append("%s: no filings" % sym)
                    continue
                rec = recs[0]
                url = _find_xbrl_url(rec)
                if not url:
                    errors.append("%s: latest filing has no XBRL link" % sym)
                    continue
                sources.append((sym, rec.get("date") or rec.get("asOnDate"), nse.get_bytes(url)))
            except NSEError as e:
                errors.append("%s: %s" % (sym, e))
    for sym, as_of, blob in sources:
        try:
            holders = parse_shp_xbrl(blob)
        except ET.ParseError as e:
            errors.append("%s: bad XBRL (%s)" % (sym, e))
            continue
        for h in holders:
            if any(n in h["name"].lower() for n in names):
                results.append({"symbol": sym, "holder": h["name"], "pct_of_company": h["pct"],
                                "shares": h["shares"], "as_of": as_of})
    for e in errors:
        print("skip " + e, file=sys.stderr)
    if not results:
        print("No holding above 1 %% found for %r in the stocks checked." % a.name)
        return
    # weights: by rupee value if we know shares and prices, else equal
    prices = read_json(a.prices) if a.prices and os.path.exists(a.prices) else {}
    agg = {}
    for r in results:
        g = agg.setdefault(r["symbol"], {"symbol": r["symbol"], "name": r["holder"],
                                         "shares": 0, "pct_of_company": 0, "as_of": r["as_of"]})
        g["shares"] += r["shares"] or 0
        g["pct_of_company"] += r["pct_of_company"]
    rows = list(agg.values())
    if prices and all(prices.get(r["symbol"]) and r["shares"] for r in rows):
        for r in rows:
            r["value"] = r["shares"] * float(prices[r["symbol"]])
        total = sum(r["value"] for r in rows)
        for r in rows:
            r["weight"] = r["value"] / total * 100
        basis = "rupee value (shares x price)"
    else:
        for r in rows:
            r["weight"] = 100.0 / len(rows)
        basis = "equal weight (pass --prices prices.json for value weights)"
    rows.sort(key=lambda r: -r["weight"])
    print("%s: %d disclosed holdings (>1 %% of company). Weights by %s.\n" % (a.name, len(rows), basis))
    for r in rows:
        print("  %-12s %6.2f %% of company  %14s sh  weight %6.2f %%" % (
            r["symbol"], r["pct_of_company"], "{:,.0f}".format(r["shares"]) if r["shares"] else "?",
            r["weight"]))
    label = slug(a.label or a.name.split("|")[0])
    out = a.out or os.path.join(TRADER, "holdings", "investor-%s_%s.json" % (
        label, dt.date.today().isoformat()))
    smap = load_symbol_map()
    rev = {s[0]: i for i, s in smap.items()}
    holdings = [{"symbol": r["symbol"], "isin": rev.get(r["symbol"]), "name": r["symbol"],
                 "series": "EQ", "weight": round(r["weight"], 4), "shares": r["shares"],
                 "pct_of_company": r["pct_of_company"]} for r in rows]
    write_json(out, {"meta": {"investor": a.name, "source": "shareholding pattern (>1 % holders)",
                              "as_of": max((r["as_of"] or "") for r in rows) or None,
                              "weight_basis": basis},
                     "holdings": holdings})
    print("\nsaved " + out)
    print("note: only stakes above 1 % of a company are disclosed, so smaller "
          "positions are invisible. Check the names matched are the right person.")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sheets", help="list sheets in an AMC workbook")
    s.add_argument("file")
    s.set_defaults(fn=cmd_sheets)

    s = sub.add_parser("mf", help="parse an AMC monthly portfolio file")
    s.add_argument("file")
    s.add_argument("--sheet", help="sheet name or index (AMCs often use one sheet per scheme)")
    s.add_argument("--label", help="short name for the saved file, e.g. ppfas-flexi")
    s.add_argument("--scheme", help="override the scheme name")
    s.add_argument("--as-of", help="portfolio date YYYY-MM-DD if not detected")
    s.add_argument("--out")
    s.add_argument("--show", type=int, default=15)
    s.set_defaults(fn=cmd_mf)

    s = sub.add_parser("diff", help="compare two saved holdings files")
    s.add_argument("old")
    s.add_argument("new")
    s.add_argument("--min-change", type=float, default=5.0,
                   help="minimum change to report (%% of shares, or weight points)")
    s.add_argument("--json", help="also write the diff as JSON")
    s.set_defaults(fn=cmd_diff)

    s = sub.add_parser("copy", help="write trader/targets.json from a holdings file")
    s.add_argument("holdings")
    s.add_argument("--top", type=int, default=10)
    s.add_argument("--skip-unmapped", action="store_true", help="skip ISINs with no NSE symbol")
    s.add_argument("--out", default=os.path.join(TRADER, "targets.json"))
    s.set_defaults(fn=cmd_copy)

    s = sub.add_parser("symbols", help="ISIN -> NSE symbol table")
    s.add_argument("--fetch", action="store_true", help="download EQUITY_L.csv from NSE")
    s.add_argument("--file", help="use an EQUITY_L.csv you downloaded")
    s.add_argument("lookup", nargs="*", help="ISINs or symbols to look up")
    s.set_defaults(fn=cmd_symbols)

    s = sub.add_parser("investor", help="holdings of a named investor from shareholding patterns")
    s.add_argument("name", help="name to match, e.g. 'Ashish Kacholia'. Use | for aliases.")
    s.add_argument("--symbols", help="comma-separated NSE symbols to check")
    s.add_argument("--xbrl", nargs="*", help="shareholding XBRL files you downloaded, as "
                   "SYMBOL=FILE, or files named like SYMBOL_shp.xml")
    s.add_argument("--prices", help="JSON {symbol: price} for value weights")
    s.add_argument("--label")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_investor)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
