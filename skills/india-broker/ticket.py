#!/usr/bin/env python3
"""india-broker: turn risk-approved orders into exact Angel One tickets,
record the user's yes, and fill paper trades. It never places a real order.

Flow for each order
  1. india-risk marked it PASS, and nothing changed since (hash matches)
  2. `show` prints the ticket in plain words + Angel One SmartAPI fields
  3. the user types yes in the chat for that exact ticket
  4. `approve ID --confirm yes` records that yes in trader/approvals.jsonl
  5a. paper (default): `paper ID` fills it in trader/portfolio.json
  5b. live: the user places it in the Angel One app (steps printed), or
      Claude passes the exported ticket to an Angel One connector the user
      installed, which asks once more ("Allow once / Deny"). Then
      `record ID --broker-order-id X` logs it.

Commands
  master  [--fetch | --file F]    build the symbol -> token index from Angel
                                  One's instrument file (OpenAPIScripMaster)
  show    [ID]                    tickets and whether each is READY
  approve ID --confirm yes [--live]
  reject  ID                      the user said no
  paper   ID                      simulate the fill at the limit price
  export  ID                      write trader/tickets/ID.json (SmartAPI fields)
  record  ID --broker-order-id X  log a live order the user placed
  retick  ID                      round the limit to Angel One's tick size
                                  (resets the order to PROPOSED: re-run risk)
"""

import argparse
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from trader_common import (APPROVALS, ORDERS, TRADER, charges, die, inr, load_config,  # noqa: E402
                           load_portfolio, now_iso, order_hash, read_json, round_to_tick,
                           write_json, PORTFOLIO)

MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
INDEX = os.path.join(TRADER, "angel_eq_index.json")
TICKETS = os.path.join(TRADER, "tickets")


# ------------------------------------------------------------ instruments


def build_index(master):
    """Keep NSE '-EQ' and BSE equity rows: {"NSE:RELIANCE": {...}}."""
    idx = {}
    for r in master:
        seg, sym = r.get("exch_seg"), r.get("symbol", "")
        if seg == "NSE" and sym.endswith("-EQ"):
            key = "NSE:" + sym[:-3]
        elif seg == "BSE" and (r.get("instrumenttype") in ("", None)) and r.get("name"):
            key = "BSE:" + r["name"]
        else:
            continue
        try:
            tick = float(r.get("tick_size") or 0) / 100  # Angel lists tick in paise
        except ValueError:
            tick = 0
        idx[key] = {"token": str(r.get("token")), "tradingsymbol": sym, "name": r.get("name"),
                    "tick": tick or None, "lot": int(float(r.get("lotsize") or 1))}
    return idx


def load_index():
    return read_json(INDEX, {}) or {}


def cmd_master(a):
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            master = json.load(f)
    elif a.fetch:
        req = urllib.request.Request(MASTER_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            master = json.load(r)
    else:
        die("use --fetch, or --file OpenAPIScripMaster.json")
    idx = build_index(master)
    write_json(INDEX, {"built": now_iso(), "count": len(idx), "instruments": idx})
    print("indexed %d NSE/BSE equity instruments -> %s" % (len(idx), INDEX))


def instrument(o, idx):
    inst = (idx.get("instruments") or {}).get("%s:%s" % (o.get("exchange", "NSE"), o["symbol"].upper()))
    return inst


# ---------------------------------------------------------------- tickets


def smartapi_params(o, inst):
    """Fields for Angel One SmartAPI placeOrder (all values are strings)."""
    return {
        "variety": "NORMAL",
        "tradingsymbol": inst["tradingsymbol"] if inst else o["symbol"] + "-EQ",
        "symboltoken": inst["token"] if inst else None,
        "transactiontype": o["side"],
        "exchange": o.get("exchange", "NSE"),
        "ordertype": o["order_type"],
        "producttype": "DELIVERY",
        "duration": o.get("validity", "DAY"),
        "price": "%.2f" % o["limit_price"] if o["order_type"] == "LIMIT" else "0",
        "squareoff": "0",
        "stoploss": "0",
        "quantity": str(o["qty"]),
        "ordertag": ("itr-" + o["hash"])[:20],
    }


def plain(o):
    return "%s %d x %s @ %s %s, DELIVERY, DAY, on %s  (%s + ~%s charges)" % (
        o["side"], o["qty"], o["symbol"], inr(o["limit_price"]), o["order_type"],
        o.get("exchange", "NSE"), inr(o["qty"] * o["limit_price"]),
        inr(o.get("est_charges")))


def app_steps(o):
    return ("Angel One app: search %s > %s > Delivery > %s > Qty %d > Price %.2f > "
            "Validity Day > swipe to place" % (o["symbol"], o["side"].title(), o["order_type"].title(),
                                               o["qty"], o["limit_price"]))


def readiness(o, idx, cfg):
    """(ready, reasons) for approval."""
    why = []
    r = o.get("risk") or {}
    if r.get("status") != "PASS":
        why.append("risk check: %s" % (r.get("status") or "not run"))
    elif r.get("hash") != order_hash(o):
        why.append("order changed after the risk check; run india-risk again")
    inst = instrument(o, idx)
    if not inst:
        if not cfg.get("paper", True):
            why.append("no Angel One token for %s (run: ticket.py master --fetch)" % o["symbol"])
    elif inst.get("tick") and o["order_type"] == "LIMIT":
        t = inst["tick"]
        if abs(round(o["limit_price"] / t) * t - o["limit_price"]) > 1e-6:
            why.append("limit %.2f is not a multiple of tick %.2f (run: ticket.py retick %s)"
                       % (o["limit_price"], t, o["id"]))
    return (not why), why, inst


def find(doc, oid):
    for o in doc["orders"]:
        if o["id"].upper() == oid.upper():
            return o
    die("no order %s in %s" % (oid, ORDERS))


def load_orders():
    doc = read_json(ORDERS)
    if not doc or not doc.get("orders"):
        die("no orders. Run india-budget and india-risk first.")
    return doc


def cmd_show(a):
    cfg = load_config()
    doc, idx = load_orders(), load_index()
    mode = "PAPER" if cfg.get("paper", True) else "LIVE"
    print("Mode: %s%s\n" % (mode, "" if mode == "PAPER" else
                            "  (real money: you place each order yourself, or via your connector)"))
    for o in doc["orders"]:
        if a.id and o["id"].upper() != a.id.upper():
            continue
        ready, why, inst = readiness(o, idx, cfg)
        state = o.get("status", "?")
        label = state if state in ("APPROVED", "FILLED_PAPER", "PLACED_BY_USER", "REJECTED") else \
            ("READY" if ready else "NOT READY")
        print("[%s] %s  %s" % (o["id"], label, plain(o)))
        for w in why:
            print("      - " + w)
        if ready or a.verbose:
            print("      " + app_steps(o))
            print("      SmartAPI: " + json.dumps(smartapi_params(o, inst)))
        print()


def append_approval(rec):
    os.makedirs(os.path.dirname(APPROVALS) or ".", exist_ok=True)
    with open(APPROVALS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def cmd_approve(a):
    if (a.confirm or "").strip().lower() != "yes":
        die("approval needs --confirm yes, and only after the user typed yes for this exact ticket")
    cfg = load_config()
    if a.live and cfg.get("paper", True):
        die("paper mode is on in trader/config.json. Set \"paper\": false to approve live orders.")
    doc, idx = load_orders(), load_index()
    o = find(doc, a.id)
    if o.get("status") in ("APPROVED", "FILLED_PAPER", "PLACED_BY_USER"):
        die("%s is already %s" % (o["id"], o["status"]))
    ready, why, inst = readiness(o, idx, cfg)
    if not ready:
        die("%s is not ready: %s" % (o["id"], "; ".join(why)))
    rec = {"order_id": o["id"], "hash": o["hash"], "approved_at": now_iso(),
           "mode": "live" if a.live else "paper", "ticket": plain(o),
           "smartapi": smartapi_params(o, inst)}
    append_approval(rec)
    o["status"] = "APPROVED"
    o["approval"] = {k: rec[k] for k in ("approved_at", "mode", "hash")}
    write_json(ORDERS, doc)
    print("Recorded your yes for %s: %s" % (o["id"], rec["ticket"]))
    if a.live:
        print("Now place it: " + app_steps(o))
        print("Then log it:  ticket.py record %s --broker-order-id <id from Angel One>" % o["id"])
    else:
        print("Paper mode. Fill it with: ticket.py paper %s" % o["id"])


def cmd_reject(a):
    doc = load_orders()
    o = find(doc, a.id)
    o["status"] = "REJECTED"
    o["rejected_at"] = now_iso()
    write_json(ORDERS, doc)
    print("%s rejected; nothing will be sent." % o["id"])


def cmd_paper(a):
    cfg = load_config()
    doc = load_orders()
    o = find(doc, a.id)
    if o.get("status") != "APPROVED" or (o.get("approval") or {}).get("mode") != "paper":
        die("%s needs a paper approval first (ticket.py approve %s --confirm yes)" % (o["id"], o["id"]))
    if o["approval"]["hash"] != order_hash(o):
        die("order changed after approval; it must be checked and approved again")
    pf = load_portfolio(cfg)
    if not os.path.exists(PORTFOLIO) and doc.get("summary", {}).get("budget"):
        pf["cash"] = float(doc["summary"]["budget"])
    px = o["limit_price"]
    value = o["qty"] * px
    fee = charges(value, o["side"], cfg)
    pos = pf["positions"].setdefault(o["symbol"], {"qty": 0, "avg_price": 0.0})
    if o["side"] == "BUY":
        if pf["cash"] < value + fee:
            die("paper cash %s is not enough for %s" % (inr(pf["cash"]), inr(value + fee)))
        new_qty = pos["qty"] + o["qty"]
        pos["avg_price"] = round((pos["qty"] * pos["avg_price"] + value + fee) / new_qty, 4)
        pos["qty"] = new_qty
        pf["cash"] = round(pf["cash"] - value - fee, 2)
    else:
        if pos["qty"] < o["qty"]:
            die("paper position %d is less than %d" % (pos["qty"], o["qty"]))
        pos["qty"] -= o["qty"]
        pf["cash"] = round(pf["cash"] + value - fee, 2)
        if pos["qty"] == 0:
            del pf["positions"][o["symbol"]]
    pf.setdefault("history", []).append({"at": now_iso(), "order_id": o["id"], "side": o["side"],
                                         "symbol": o["symbol"], "qty": o["qty"], "price": px,
                                         "charges": fee})
    write_json(PORTFOLIO, pf)
    o["status"] = "FILLED_PAPER"
    o["filled_at"] = now_iso()
    write_json(ORDERS, doc)
    print("Paper fill: %s. Cash now %s." % (plain(o), inr(pf["cash"])))


def cmd_export(a):
    doc, idx = load_orders(), load_index()
    o = find(doc, a.id)
    if o.get("status") != "APPROVED":
        die("%s is not approved" % o["id"])
    if o["approval"]["hash"] != order_hash(o):
        die("order changed after approval")
    inst = instrument(o, idx)
    params = smartapi_params(o, inst)
    if not params["symboltoken"]:
        die("no symboltoken for %s; run: ticket.py master --fetch" % o["symbol"])
    path = os.path.join(TICKETS, "%s.json" % o["id"])
    write_json(path, {"order_id": o["id"], "approved_at": o["approval"]["approved_at"],
                      "mode": o["approval"]["mode"], "angelone_placeOrder": params})
    print(json.dumps(params, indent=2))
    print("\nwrote " + path)


def cmd_record(a):
    doc = load_orders()
    o = find(doc, a.id)
    if o.get("status") != "APPROVED" or (o.get("approval") or {}).get("mode") != "live":
        die("%s has no live approval" % o["id"])
    o["status"] = "PLACED_BY_USER"
    o["broker_order_id"] = a.broker_order_id
    o["placed_at"] = now_iso()
    write_json(ORDERS, doc)
    print("Logged %s as Angel One order %s. Check its status in the app." % (o["id"], a.broker_order_id))


def cmd_retick(a):
    doc, idx = load_orders(), load_index()
    o = find(doc, a.id)
    inst = instrument(o, idx)
    if not inst or not inst.get("tick"):
        die("no tick size known for %s; run: ticket.py master --fetch" % o["symbol"])
    old = o["limit_price"]
    o["limit_price"] = round_to_tick(old, inst["tick"], up=(o["side"] == "BUY"))
    o["value"] = round(o["qty"] * o["limit_price"], 2)
    o["status"] = "PROPOSED"
    o.pop("risk", None)
    write_json(ORDERS, doc)
    print("%s limit %.2f -> %.2f (tick %.2f). Run india-risk again." % (o["id"], old, o["limit_price"], inst["tick"]))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("master")
    s.add_argument("--fetch", action="store_true")
    s.add_argument("--file")
    s.set_defaults(fn=cmd_master)
    s = sub.add_parser("show")
    s.add_argument("id", nargs="?")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("approve")
    s.add_argument("id")
    s.add_argument("--confirm", required=True)
    s.add_argument("--live", action="store_true")
    s.set_defaults(fn=cmd_approve)
    for name, fn in (("reject", cmd_reject), ("paper", cmd_paper), ("export", cmd_export),
                     ("retick", cmd_retick)):
        s = sub.add_parser(name)
        s.add_argument("id")
        s.set_defaults(fn=fn)
    s = sub.add_parser("record")
    s.add_argument("id")
    s.add_argument("--broker-order-id", required=True)
    s.set_defaults(fn=cmd_record)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
