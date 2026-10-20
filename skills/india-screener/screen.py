#!/usr/bin/env python3
"""india-screener: filter companies by your own rules and rank what passes.

Input: trader/metrics.json from india-analyst (or any JSON of the form
{"SYMBOL": {"field": value, ...}}).

Rules file (trader/rules.json), e.g.:
{
  "rules": [
    {"field": "rev_growth_yoy",    "op": ">=", "value": 10},
    {"field": "profit_growth_yoy", "op": ">=", "value": 10},
    {"field": "net_margin_pct",    "op": ">=", "value": 8},
    {"field": "loss_quarters",     "op": "==", "value": 0},
    {"field": "pe_ttm",            "op": "<=", "value": 40}
  ],
  "rank": [
    {"field": "profit_growth_yoy", "weight": 2},
    {"field": "rev_growth_yoy",    "weight": 1},
    {"field": "pe_ttm",            "weight": -1}
  ],
  "missing": "fail",
  "top": 10
}

Ops: > >= < <= == != between (value [lo, hi]) in (value [...]).
"missing": "fail" (default) treats a missing field as failing the rule;
"skip" ignores rules whose field is missing.
Ranking: each rank field is turned into a 0-1 percentile among the stocks
that passed; negative weight means lower is better. Score = weighted sum.

Commands
  run [--rules F] [--metrics F]         filter + rank, save trader/screen.json
      [--targets equal|score --top N]   also write trader/targets.json
  fields                                list fields available in metrics

Standard library only. Python 3.8+.
"""

import argparse
import datetime as dt
import json
import os
import sys

TRADER = os.environ.get("TRADER_DIR", "trader")

OPS = {
    ">": lambda x, v: x > v, ">=": lambda x, v: x >= v,
    "<": lambda x, v: x < v, "<=": lambda x, v: x <= v,
    "==": lambda x, v: x == v, "!=": lambda x, v: x != v,
    "between": lambda x, v: v[0] <= x <= v[1],
    "in": lambda x, v: x in v,
}


def die(msg):
    print("error: " + msg, file=sys.stderr)
    sys.exit(1)


def load(path):
    if not os.path.exists(path):
        die("%s not found" % path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def screen(metrics, cfg):
    rules = cfg.get("rules", [])
    for r in rules:
        if r.get("op") not in OPS:
            die("unknown op %r in rule %s" % (r.get("op"), r))
    missing = cfg.get("missing", "fail")
    passed, failed = [], []
    for sym, m in metrics.items():
        if not isinstance(m, dict):
            continue
        why = []
        for r in rules:
            x = m.get(r["field"])
            if x is None:
                if missing == "fail":
                    why.append("%s missing" % r["field"])
                continue
            try:
                ok = OPS[r["op"]](x, r["value"])
            except TypeError:
                ok = False
            if not ok:
                why.append("%s %s (needs %s %s)" % (r["field"], x, r["op"], r["value"]))
        (failed if why else passed).append((sym, m, why))

    # percentile scores among the stocks that passed
    rank = cfg.get("rank", [])
    scores = {s: 0.0 for s, _, _ in passed}
    for rk in rank:
        f, w = rk["field"], float(rk.get("weight", 1))
        vals = sorted(m[f] for _, m, _ in passed if isinstance(m.get(f), (int, float)))
        for s, m, _ in passed:
            x = m.get(f)
            if not isinstance(x, (int, float)) or len(vals) < 2:
                pct = 0.5
            else:
                below = sum(1 for v in vals if v < x)
                equal = sum(1 for v in vals if v == x)
                pct = (below + (equal - 1) / 2) / (len(vals) - 1)
            scores[s] += w * pct if w > 0 else -w * (1 - pct)
    ranked = sorted(passed, key=lambda t: -scores[t[0]])
    return ranked, failed, scores


def cmd_run(a):
    metrics = load(a.metrics)
    cfg = load(a.rules)
    ranked, failed, scores = screen(metrics, cfg)
    top = a.top or cfg.get("top") or len(ranked)
    print("%d of %d passed your rules.\n" % (len(ranked), len(ranked) + len(failed)))
    show_fields = [r["field"] for r in cfg.get("rank", [])][:4] or \
        [r["field"] for r in cfg.get("rules", [])][:4]
    print("%-4s %-12s %7s  " % ("#", "symbol", "score") + "  ".join("%14s" % f[:14] for f in show_fields))
    for i, (s, m, _) in enumerate(ranked[:top], 1):
        print("%-4d %-12s %7.2f  " % (i, s, scores[s]) +
              "  ".join("%14s" % ("-" if m.get(f) is None else m.get(f)) for f in show_fields))
    if a.why and failed:
        print("\nFailed:")
        for s, _, why in failed:
            print("  %-12s %s" % (s, "; ".join(why)))
    out = {"created": dt.datetime.now().isoformat(timespec="seconds"), "rules": cfg,
           "passed": [{"symbol": s, "score": round(scores[s], 4)} for s, _, _ in ranked],
           "failed": [{"symbol": s, "why": w} for s, _, w in failed]}
    path = os.path.join(TRADER, "screen.json")
    os.makedirs(TRADER, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print("\nsaved " + path)
    if a.targets:
        pick = ranked[:top]
        if not pick:
            die("nothing passed; no targets written")
        if a.targets == "score":
            tot = sum(max(scores[s], 1e-9) for s, _, _ in pick)
            w = {s: max(scores[s], 1e-9) / tot * 100 for s, _, _ in pick}
        else:
            w = {s: 100.0 / len(pick) for s, _, _ in pick}
        tg = {"source": "india-screener", "source_kind": "screen",
              "as_of": dt.date.today().isoformat(), "created": out["created"],
              "targets": [{"symbol": s, "name": s, "series": "EQ", "weight": round(w[s], 2)} for s, _, _ in pick]}
        drift = round(100 - sum(t["weight"] for t in tg["targets"]), 2)
        tg["targets"][0]["weight"] = round(tg["targets"][0]["weight"] + drift, 2)
        with open(os.path.join(TRADER, "targets.json"), "w", encoding="utf-8") as f:
            json.dump(tg, f, indent=2)
        print("wrote trader/targets.json (%s weights, %d stocks); next: india-budget" % (a.targets, len(pick)))


def cmd_fields(a):
    metrics = load(a.metrics)
    fields = {}
    for m in metrics.values():
        if isinstance(m, dict):
            for k, v in m.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    fields[k] = fields.get(k, 0) + 1
    for k in sorted(fields):
        print("  %-24s in %d stock(s)" % (k, fields[k]))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run")
    s.add_argument("--rules", default=os.path.join(TRADER, "rules.json"))
    s.add_argument("--metrics", default=os.path.join(TRADER, "metrics.json"))
    s.add_argument("--top", type=int)
    s.add_argument("--why", action="store_true", help="show why each stock failed")
    s.add_argument("--targets", choices=["equal", "score"], help="also write trader/targets.json")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("fields")
    s.add_argument("--metrics", default=os.path.join(TRADER, "metrics.json"))
    s.set_defaults(fn=cmd_fields)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
