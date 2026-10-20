---
name: india-news
description: Watch news for Indian stocks the user holds or follows. Pulls NSE corporate announcements (what companies file with the exchange) and Google News headlines for each symbol, flags what matters (HIGH for auditor/CEO/CFO exits, defaults, fraud, SEBI orders, pledge invocation, downgrades; MEDIUM for results, dividends, bonus, split, buyback, acquisitions, fund raising, big orders), hides routine filings, and can alert only on new items. Use when the user asks "any news on my stocks", "what did my companies announce", or wants news alerts for NSE stocks.
---

# India News Monitor

```
python3 <this-skill-folder>/news.py scan --help
```

## Run it

- Everything on the watchlist, last 24 hours:
  `news.py scan`
  The watchlist is `trader/watchlist.json` (`{"symbols": [...]}`) plus the
  symbols in `trader/targets.json` and `trader/portfolio.json`.
- Specific stocks, last 3 days, only important items:
  `news.py scan --symbols LT,ITC --hours 72 --min-flag MEDIUM`
- For a schedule (alerts only on new items): `news.py scan --new`
  Good times: weekday evenings after 7 pm IST, when most filings are in.
- `--all` also shows routine filings (trading window closures, newspaper
  ads, share-certificate losses), hidden by default.

## When NSE blocks the request

Ask the user to download announcements from nseindia.com → Companies →
Corporate Filings → Announcements ("Download (.csv)") and run with
`--ann-file FILE`. Headlines can be skipped with `--no-headlines`.

## How to report

Group by stock. Lead with HIGH items and say in one line why each matters
(e.g. "the auditor resigning mid-term is a red flag worth reading in
full"). Link the exchange filing when there is one. Don't guess at price
impact. Flags come from keywords, so read the filing before treating a
flag as fact. Headlines come from Google News and can repeat or be wrong.

Not investment advice.
