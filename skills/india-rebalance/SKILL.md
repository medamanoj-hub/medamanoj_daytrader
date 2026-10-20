---
name: india-rebalance
description: Rebalance an Indian stock portfolio back to its target weights. Compares holdings in trader/portfolio.json to trader/targets.json at today's prices, shows the drift table, and when a stock drifts past the band proposes whole-share LIMIT sells first and cash-limited buys, keeping the cash buffer and per-stock cap, into trader/orders.json for india-risk. Use when the user asks to rebalance, says their portfolio has drifted, or after a new month's fund portfolio from india-copycat.
---

# India Rebalancer

```
python3 <this-skill-folder>/rebalance.py --help
```

## Run it

1. Dry run with fresh prices: `rebalance.py --fetch` (or `--prices FILE`).
2. Show the drift table. Only stocks drifting more than the band (default
   5 percentage points; `--band 3` for tighter) get trades.
3. If the user agrees: `rebalance.py --fetch --write`, then **india-risk**,
   then **india-broker** for each order.

## What it does

- Targets are applied to the portfolio minus the cash buffer, and no stock
  goes above `max_position_pct`.
- Sells come first, a little below the last price. Buys are paid only from
  cash already in the account, keeping the buffer; sale money isn't
  assumed to be available the same day. If buys are short of cash, it
  says so: run it again after the sells settle (T+1).
- Holdings not in targets are left alone unless `--sell-untargeted`.
  Ask before using it.
- It won't overwrite `trader/orders.json` while it still has open orders,
  unless `--force`.

## Tell the user

Selling can trigger capital-gains tax. Before a rebalance with sells,
check **india-tax** (`tax.py open`) for which lots are short term and how
close they are to becoming long term. Not investment advice.
