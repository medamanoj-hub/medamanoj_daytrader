---
name: india-analyst
description: Pull an Indian listed company's reported quarterly results from NSE (revenue, net profit, EPS, net margin) and compute quarter-on-quarter and year-on-year growth, trailing EPS and P/E, saving them to trader/metrics.json for india-screener. Use when the user asks how a company's results look, wants growth or margins for NSE stocks, or before screening.
---

# India Earnings Analyst

```
python3 <this-skill-folder>/analyst.py --help
```

## Run it

- One or more companies: `analyst.py facts TCS INFY HCLTECH`
- With prices, for P/E: `analyst.py facts TCS --price 3650`, or
  `--prices prices.json` for several (`{"prices": {"TCS": 3650}}`).
- Show what's saved: `analyst.py show`

NSE gives about the last five quarters, as filed. Amounts arrive in ₹ lakh
and are shown in ₹ crore. YoY growth needs the same quarter a year back.
TTM EPS sums the last four quarters.

## When NSE blocks the request or a field is missing

NSE doesn't document this data, so field names are matched loosely. If a
number looks wrong or the call fails, use a CSV with one row per quarter
(from the company's results filing, or a results page the user trusts):

```
quarter_end,revenue_cr,net_profit_cr,eps,pbt_cr
2026-09-30,24500,3120,18.4,4150
```

`analyst.py facts SYMBOL --file results.csv`

## How to report

Give the latest quarter, then the trend: "Revenue ₹24,500 cr, up 16.7 %
from a year ago; profit up 30 %; margin 12.7 %, up 1.3 points." Say which
quarter it is. Mention loss quarters. Consolidated and standalone numbers
can differ; if the user cares which one, check the filing. Not investment
advice.
