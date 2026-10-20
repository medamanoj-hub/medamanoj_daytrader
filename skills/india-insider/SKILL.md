---
name: india-insider
description: Track Indian insider trades and big-investor deals from NSE. Reads SEBI PIT disclosures (promoters, directors, key managers buying or selling their own company's shares) and NSE bulk and block deals (large trades published with the client's name), flags cluster buying, and alerts once per new filing for a watchlist or named investors. Use when the user asks what insiders or promoters are buying, wants alerts on their stocks, or asks about bulk/block deals or a big investor's latest trades in India. The Indian counterpart of a "Pelosi tracker".
---

# India Insider & Deals Tracker

India doesn't publish politicians' trades. It publishes two things that
are more useful for stocks:

- **Insider trades (SEBI PIT).** Promoters, directors, key managerial
  personnel and their relatives must report trades in their own company.
  An open-market *buy* by several insiders is the classic signal.
- **Bulk and block deals.** Any client trading over 0.5 % of a company's
  shares in a day (bulk), or a large negotiated trade in the morning block
  window (block), is published by NSE the same evening, with the client's
  name. This is where well-known investors and funds show up.

Scripts are next to this file. Run from the user's project folder:

```
python3 <this-skill-folder>/insider.py --help
```

## Commands

- Insider trades, last 30 days, open-market trades by real insiders only:
  `insider.py pit --days 30 --market-only --insiders-only`
  Add `--symbol LT`, `--side buy`, `--min-value 10000000` (₹1 crore).
  The output flags **cluster buying**: 2+ different insiders buying on market.
- Bulk / block deals: `insider.py deals --kind bulk --days 7`,
  `--kind block`, `--who KACHOLIA` to follow a name.
- Alerts, for a schedule: `insider.py new --days 5 --who KACHOLIA DAMANI`
  Watches `trader/watchlist.json` (`{"symbols": [...]}`), plus the symbols
  in `trader/targets.json` and `trader/portfolio.json`. Prints only filings
  it hasn't shown before, and saves them in `trader/alerts/`. `--all-big`
  also flags any insider market buy above `--min-value` (default ₹1 crore).

## When NSE blocks the request

NSE's site sometimes refuses scripts. Then ask the user to open the page,
click "Download (.csv)" and attach the file:
- Insider trades: nseindia.com → Companies → Corporate Filings → Insider Trading
- Deals: nseindia.com → Market Data → Bulk/Block Deals

and run the same command with `--file`. For `new`, name files so they
contain `insider`, `bulk` or `block`.

## Running it on a schedule

If the user wants alerts, set up a scheduled task (weekday evenings, after
about 7 pm IST, when NSE has published the day's deals) that runs `new` and
messages them only when something is new.

## How to report

Lead with what matters: who, which stock, buy or sell, how much in ₹,
open market or not, and when. Say why it was flagged (watchlist, named
investor, cluster, large buy). Keep context honest:

- Insider *sells* are common and often personal (tax, diversification);
  insider *buys* on the open market say more.
- ESOP allotments, gifts, pledges and inter-se promoter transfers are not
  trading signals; `--market-only` drops them.
- Disclosures arrive days after the trade. Bulk deals are same-day.
- This is public information, not inside information, and not advice.
