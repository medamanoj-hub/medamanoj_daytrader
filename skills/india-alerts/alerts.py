#!/usr/bin/env python3
"""india-alerts: ring when a price target or stop is hit, and know when
NSE is actually open.

NSE equity hours (IST, Monday-Friday, except trading holidays):
  09:00-09:15  pre-open (orders 09:00-09:08)
  09:15-15:30  normal market
  15:40-16:00  post-close session
Special sessions (e.g. Diwali Muhurat trading) are announced by NSE each
year; they are not in this calendar.

Holidays: the 2026 list is built in (from NSE's published calendar).
`holidays --fetch` adds NSE's current list to trader/nse_holidays.json.

Commands
  clock                                  open or closed right now, next open
  holidays [--fetch] [--year 2026]       trading holidays
  add SYMBOL above|below PRICE [--kind target|stop] [--note TEXT]
  list | remove ID
  check [--prices F | --fetch] [--force] [--keep]
        compare prices to alerts; fired alerts switch off unless --keep.
        Outside market hours it does nothing unless --force.
"""

import argparse
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from trader_common import IST, TRADER, die, inr, load_prices, now_iso, read_json, write_json  # noqa: E402

ALERTS = os.path.join(TRADER, "alerts.json")
HOLIDAYS = os.path.join(TRADER, "nse_holidays.json")

# NSE equity segment trading holidays, 2026 (weekdays only).
BUILTIN = {
    "2026-01-15": "Municipal Corporation election, Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}

PRE_OPEN, OPEN, CLOSE, POST_START, POST_END = (dt.time(9, 0), dt.time(9, 15), dt.time(15, 30),
                                                dt.time(15, 40), dt.time(16, 0))


def holidays():
    h = dict(BUILTIN)
    h.update((read_json(HOLIDAYS, {}) or {}).get("holidays", {}))
    return h


def is_trading_day(d, hol=None):
    hol = hol if hol is not None else holidays()
    return d.weekday() < 5 and d.isoformat() not in hol


def status(now=None):
    now = (now or dt.datetime.now(IST)).astimezone(IST)
    hol = holidays()
    d, t = now.date(), now.time()
    if d.weekday() >= 5:
        st, why = "CLOSED", "weekend"
    elif d.isoformat() in hol:
        st, why = "CLOSED", "holiday: " + hol[d.isoformat()]
    elif PRE_OPEN <= t < OPEN:
        st, why = "PRE-OPEN", "pre-open session"
    elif OPEN <= t < CLOSE:
        st, why = "OPEN", "normal market"
    elif POST_START <= t < POST_END:
        st, why = "POST-CLOSE", "post-close session"
    else:
        st, why = "CLOSED", "outside market hours"
    nxt = None
    if st != "OPEN":
        day = d if t < OPEN else d + dt.timedelta(days=1)
        for _ in range(30):
            if is_trading_day(day, hol):
                nxt = dt.datetime.combine(day, OPEN, IST)
                break
            day += dt.timedelta(days=1)
    return st, why, nxt


def cmd_clock(a):
    now = dt.datetime.now(IST)
    st, why, nxt = status(now)
    print("%s IST: NSE is %s (%s)." % (now.strftime("%a %d %b %Y %H:%M"), st, why))
    if st == "OPEN":
        print("Closes at 15:30, in %d min." % ((dt.datetime.combine(now.date(), CLOSE, IST) - now).seconds // 60))
    elif nxt:
        print("Next open: %s." % nxt.strftime("%a %d %b %H:%M"))
    if now.year != 2026 and not os.path.exists(HOLIDAYS):
        print("note: built-in holidays cover 2026 only; run `holidays --fetch`.")


def cmd_holidays(a):
    if a.fetch:
        from nse_http import NSE
        data = NSE().get_json("holiday-master", {"type": "trading"})
        got = {}
        for r in (data or {}).get("CM", []):
            try:
                d = dt.datetime.strptime(r["tradingDate"], "%d-%b-%Y").date()
                got[d.isoformat()] = r.get("description", "holiday")
            except (KeyError, ValueError):
                continue
        write_json(HOLIDAYS, {"fetched": now_iso(), "holidays": got})
        print("saved %d holidays from NSE" % len(got))
    year = str(a.year or dt.date.today().year)
    for d, name in sorted(holidays().items()):
        if d.startswith(year):
            print("  %s  %s  %s" % (d, dt.date.fromisoformat(d).strftime("%a"), name))


def load_alerts():
    return read_json(ALERTS, []) or []


def cmd_add(a):
    al = load_alerts()
    n = max([int(x["id"][1:]) for x in al] + [0]) + 1
    item = {"id": "A%d" % n, "symbol": a.symbol.upper(), "op": a.op, "price": a.price,
            "kind": a.kind, "note": a.note or "", "active": True, "created": now_iso()}
    al.append(item)
    write_json(ALERTS, al)
    print("%s: %s %s %s %s" % (item["id"], item["kind"], item["symbol"], a.op, inr(a.price)))


def cmd_list(a):
    al = load_alerts()
    if not al:
        print("no alerts")
    for x in al:
        print("%-4s %-6s %-12s %-5s %12s  %s%s" % (x["id"], x["kind"], x["symbol"], x["op"], inr(x["price"]),
                                                  "active" if x["active"] else "fired " + x.get("fired_at", ""),
                                                  ("  - " + x["note"]) if x["note"] else ""))


def cmd_remove(a):
    al = [x for x in load_alerts() if x["id"].upper() != a.id.upper()]
    write_json(ALERTS, al)
    print("removed " + a.id)


def cmd_check(a):
    st, why, nxt = status()
    if st not in ("OPEN", "PRE-OPEN") and not a.force:
        print("NSE is %s (%s); not checking. Use --force to check anyway." % (st, why))
        return
    al = load_alerts()
    live = [x for x in al if x["active"]]
    if not live:
        print("no active alerts")
        return
    prices, as_of = load_prices(a.prices, a.fetch, sorted({x["symbol"] for x in live}))
    fired = []
    for x in live:
        px = prices.get(x["symbol"])
        if px is None:
            print("warning: no price for " + x["symbol"])
            continue
        hit = px >= x["price"] if x["op"] == "above" else px <= x["price"]
        if hit:
            fired.append((x, px))
            if not a.keep:
                x["active"] = False
                x["fired_at"] = now_iso()
                x["fired_price"] = px
    write_json(ALERTS, al)
    if not fired:
        print("No alert hit%s." % (" (prices as of %s)" % as_of if as_of else ""))
        return
    for x, px in fired:
        print("ALERT %s %s: %s is %s, %s your %s level %s%s" % (
            x["id"], x["kind"].upper(), x["symbol"], inr(px), "above" if x["op"] == "above" else "below",
            x["kind"], inr(x["price"]), (" - " + x["note"]) if x["note"] else ""))
    os.makedirs(os.path.join(TRADER, "alerts"), exist_ok=True)
    write_json(os.path.join(TRADER, "alerts", "price_%s.json" % dt.datetime.now().strftime("%Y%m%d-%H%M")),
               [dict(x, price_now=px) for x, px in fired])
    if any(x["kind"] == "stop" for x, _ in fired):
        print("A stop is only an alert here; nothing was sold. To act, go through india-risk and india-broker.")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("clock").set_defaults(fn=cmd_clock)
    s = sub.add_parser("holidays")
    s.add_argument("--fetch", action="store_true")
    s.add_argument("--year", type=int)
    s.set_defaults(fn=cmd_holidays)
    s = sub.add_parser("add")
    s.add_argument("symbol")
    s.add_argument("op", choices=["above", "below"])
    s.add_argument("price", type=float)
    s.add_argument("--kind", choices=["target", "stop", "level"], default="level")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_add)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    s = sub.add_parser("remove")
    s.add_argument("id")
    s.set_defaults(fn=cmd_remove)
    s = sub.add_parser("check")
    s.add_argument("--prices")
    s.add_argument("--fetch", action="store_true")
    s.add_argument("--force", action="store_true")
    s.add_argument("--keep", action="store_true", help="keep alerts active after they fire")
    s.set_defaults(fn=cmd_check)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
