---
name: india-risk
description: Check proposed Indian stock orders against the user's limits before anything can be approved. Blocks intraday, margin, F&O, market orders, trade-to-trade series, ASM/GSM surveillance stocks, blocked symbols, oversized orders, positions above the cap, sells of shares not held, stale prices, limit prices far from market, and anything that would eat the cash buffer. Writes PASS or BLOCK with reasons into trader/orders.json. Use after india-budget (or any change to an order) and always before india-broker.
---

# India Risk Manager

The gate between a proposed order and the broker. Only `PASS` orders can
be approved.

```
python3 <this-skill-folder>/check.py
```

Exit code 0 = everything passed, 2 = something was blocked.

## What it checks

Per order:
- Equity cash only: NSE or BSE, product DELIVERY, series EQ. No intraday,
  no margin (MTF), no futures or options, no BE/BZ trade-to-trade series
  unless `allow_non_eq_series` is on.
- Whole positive quantity. LIMIT orders only, unless `allow_market_orders`.
  (Under SEBI's retail algo rules from April 2026, API market orders are
  converted to market-price-protection orders anyway.)
- Limit within `price_band_pct` of the last price; prices not older than
  `max_price_age_hours`.
- Order value ≤ `max_order_value`. Not in `blocked_symbols`. Not on the
  user's surveillance list (`trader/surveillance.json`, e.g.
  `{"ASM": ["XYZ"], "GSM": ["ABC"]}` copied from NSE's current lists).
- Sells: never more than the user owns now (no short selling).
- Position after the fill ≤ `max_position_pct` of the portfolio.

Across the batch, in order: buys plus charges never dip into the cash
buffer, and no more than `max_orders_per_day` approvals a day. Sale
proceeds are not counted as cash in the same batch (settlement is T+1).

Orders already approved, filled, placed or rejected are not re-checked.

## How to use it

- Run it after **india-budget**, and again after *any* edit to an order.
  Each verdict stores a fingerprint of the order; if the order changes,
  the broker refuses it until it is checked again.
- Report blocks plainly: "ITC blocked: it's on your blocked list." Then
  offer the fix (raise the limit, drop the stock, refresh prices). Never
  edit `trader/config.json` limits without the user asking.
- Paper and live are both checked the same way.
