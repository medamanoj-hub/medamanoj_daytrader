---
name: india-screener
description: Screen Indian stocks against the user's own rules (growth, margins, P/E, loss quarters, any numeric field) using trader/metrics.json from india-analyst, rank what passes with weighted percentiles, explain why others failed, and optionally write trader/targets.json for india-budget. Use when the user wants to filter or shortlist NSE stocks by their criteria, e.g. "find companies growing profits over 15 % with P/E under 30".
---

# India Stock Screener

```
python3 <this-skill-folder>/screen.py --help
```

## Steps

1. Get the numbers: run **india-analyst** on the stocks to screen
   (`analyst.py facts A B C --prices prices.json`). The screener works only
   on what's in `trader/metrics.json`; it doesn't search the whole market.
2. Turn the user's words into `trader/rules.json`. Example: "profit growth
   over 15 %, no losses, P/E under 30, rank by profit growth then cheapness":

```json
{
  "rules": [
    {"field": "profit_growth_yoy", "op": ">", "value": 15},
    {"field": "loss_quarters", "op": "==", "value": 0},
    {"field": "pe_ttm", "op": "<", "value": 30}
  ],
  "rank": [
    {"field": "profit_growth_yoy", "weight": 2},
    {"field": "pe_ttm", "weight": -1}
  ]
}
```

   Show the user the rules before running. `screen.py fields` lists what's
   available (rev/profit/eps growth QoQ and YoY, net_margin_pct,
   margin_change_yoy_pts, eps_ttm, pe_ttm, loss_quarters, ...).
3. Run: `screen.py run --why`
4. If the user wants to buy the shortlist: `screen.py run --targets equal`
   (or `score`) writes `trader/targets.json`, replacing any previous
   targets. Say so first. Then **india-budget**.

Missing data fails a rule by default (`"missing": "skip"` to ignore it).
A screen is a filter, not a recommendation.
