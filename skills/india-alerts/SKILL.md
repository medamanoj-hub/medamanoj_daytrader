---
name: india-alerts
description: Price alerts for NSE stocks with the real Indian market calendar. Sets target, stop and level alerts, checks them against current prices only while NSE is open, fires each once, and answers whether NSE is open now, when it next opens, and which days are trading holidays (2026 list built in). Use when the user wants to be told when a stock crosses a price, asks if the market is open today, or asks about NSE holidays and timings.
---

# India Price Alerts

```
python3 <this-skill-folder>/alerts.py --help
```

## Commands

- Is NSE open? `alerts.py clock`
  Hours (IST, Mon-Fri): pre-open 9:00-9:15, market 9:15-15:30,
  post-close 15:40-16:00. Closed on weekends and trading holidays.
- Holidays: `alerts.py holidays` (2026 built in). `--fetch` adds NSE's
  current list, e.g. for next year.
- Add: `alerts.py add RELIANCE below 2800 --kind stop --note "exit plan"`,
  `alerts.py add ITC above 450 --kind target`
- `alerts.py list`, `alerts.py remove A2`
- Check: `alerts.py check --fetch`. Outside market hours it does nothing
  (use `--force` to check anyway). Fired alerts switch off unless `--keep`.

## On a schedule

Offer a scheduled task that runs `check --fetch` every hour (or 30 min)
on weekdays from 9:15 to 15:30 IST, plus `clock` at 9:15 as an opening
bell, and messages the user only when something fires.

## Be clear with the user

- An alert only tells. A "stop" here never sells anything. To act, go
  through **india-risk** and **india-broker**, with the user's yes.
- Prices come from Yahoo Finance (`--fetch`) and can lag a few minutes.
  For a hard stop, suggest a stop-loss or GTT order in the Angel One app.
- Special sessions like Diwali Muhurat trading aren't in the calendar;
  NSE announces them each year.
