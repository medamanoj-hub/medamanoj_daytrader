---
name: india-budget
description: Size Indian stock purchases to a rupee budget. Turns target weights (trader/targets.json, e.g. from india-copycat) into whole-share LIMIT buy orders on NSE that keep a cash buffer, cap any one stock, include estimated charges (brokerage, STT, stamp duty, GST), and never cost more than planned. Writes trader/orders.json as proposals only. Use when the user says how much they want to invest, e.g. "put ₹1 lakh into these", "size this for ₹50,000", or asks how many shares to buy.
---

# India Budget Manager

Turns weights into orders that fit the user's money.

```
python3 <this-skill-folder>/size.py --help
```

## Setup (first time)

Copy `config.example.json` (next to this file) to `trader/config.json` and
fill in, with the user: budget, cash buffer %, max % per stock. Defaults:
₹1,00,000, 10 % buffer, 25 % cap, paper trading on. Ask before assuming
their budget.

## Run it

1. Prices: `--fetch` gets last prices from Yahoo Finance (`SYMBOL.NS`,
   unofficial, may be delayed), or `--prices prices.json` with
   `{"prices": {"RELIANCE": 2915.0}, "as_of": "2026-10-20T15:35:00"}`.
   If neither works, ask the user for today's prices.
2. Dry run first: `size.py --budget 50000 --prices prices.json`
3. Show the table to the user. If they're happy: add `--write`.

## What it does

- Keeps the cash buffer uninvested.
- Caps each stock at `max_position_pct` of the budget and shares the
  excess among the others by weight.
- Whole shares only. A stock whose single share costs more than its
  allocation is dropped, and the reason is printed.
- Each order is a LIMIT, `limit_buffer_pct` (0.5 %) above the last price,
  rounded up to the tick. Sizing uses that limit plus estimated charges,
  so the real cost can't exceed the allocation.
- With `fill_leftover` on, leftover rupees buy one more share at a time of
  the most under-weight stock, without breaking the cap.
- Writes `trader/orders.json` with status `PROPOSED`. Nothing is sent.

Then hand over to **india-risk**.

## Explain the result simply

"₹50,000 budget, ₹5,000 kept as cash. 5 orders worth ₹44,820, about ₹106
in charges. ₹74 couldn't be used because shares are whole." Point out
where the actual weight differs from the target, and why (cap, whole
shares). Charges are estimates; the rate card is in the config and some
Angel One plans charge zero delivery brokerage.

Not investment advice. Sizing is arithmetic, not a view on the stocks.
