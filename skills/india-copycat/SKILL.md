---
name: india-copycat
description: Copy an Indian mutual fund's or a well-known investor's stock portfolio. Reads an AMC's monthly portfolio disclosure (ISIN, % to NAV) or companies' shareholding patterns (named holders above 1 %), rebuilds the holdings as weights, shows what was bought, added, trimmed and sold since last month, and writes trader/targets.json for india-budget. Use when the user wants to copy, mirror, follow or "clone" a fund (e.g. "copy Parag Parikh Flexi Cap's top 10") or an investor (e.g. "what does Kacholia hold"), for NSE/BSE stocks.
---

# India Copycat

Rebuild someone else's Indian stock portfolio as weights you can copy.

Scripts are next to this file. Run them from the user's project folder, so
everything lands in its `trader/` folder:

```
python3 <this-skill-folder>/copycat.py --help
```

## Two sources

**1. Mutual funds (best, monthly, complete).** SEBI requires every AMC to
publish each scheme's full portfolio every month as a spreadsheet, with ISIN
and "% to Net Assets" for every line. It's on the AMC's website under
"Monthly Portfolio" / "Portfolio Disclosure". Usually within 10 days of
month end.

**2. Individual investors (experimental, quarterly, partial).** Each listed
company files a shareholding pattern every quarter that names every public
shareholder above 1 %. So you only see an investor's *larger* stakes, and
you have to name the stocks to check.

## Steps: copy a fund

1. Get the file. Ask the user to download the month's portfolio file from
   the AMC website and attach it (the workspace usually can't reach AMC
   sites). If it's `.xls`, ask them to save it as `.xlsx` or `.csv`.
2. One-time: the ISIN → NSE symbol table.
   `copycat.py symbols --fetch` (or `--file EQUITY_L.csv` they downloaded
   from nseindia.com).
3. Many AMCs put every scheme on its own sheet. List them first:
   `copycat.py sheets FILE.xlsx`, then pick the scheme:
   `copycat.py mf FILE.xlsx --sheet <name> --label ppfas-flexi`
   It keeps only equity shares (ISIN `INE…01…`), skipping bonds, T-bills,
   cash and foreign stocks, and saves `trader/holdings/<label>_<date>.json`.
4. If last month's file is saved too, show the changes:
   `copycat.py diff OLD.json NEW.json`
5. Write the targets: `copycat.py copy trader/holdings/<file>.json --top 10`.
   This renormalises the top N to 100 % and writes `trader/targets.json`.
6. Hand over to **india-budget**.

Report back in plain words: the scheme, the date, how much of the fund is in
equity, the top holdings, and what changed. Mention when the data is from.

## Steps: an individual investor

`copycat.py investor "Ashish Kacholia" --symbols SYM1,SYM2,...`
fetches each company's latest shareholding pattern from NSE and finds the
name. Use `|` for name variants (`"Kacholia|Bengal Finance"`). If NSE
blocks the request, the user can download each filing's XBRL and run
`--xbrl SYM=file.xml ...`. Add `--prices prices.json` to weight by rupee
value instead of equally. Good places to get candidate symbols: the user,
or recent bulk deals from **india-insider** (`deals --who NAME`).

Always tell the user: only stakes above 1 % show, the data is a quarter
old, and name matches must be checked (two people can share a name).

## Honest limits

- A fund's portfolio is as of the month end; it may have changed since.
  Copying holdings doesn't copy the fund's timing, cash calls or results.
- Some holdings won't map to an NSE symbol (BSE-only, unlisted, merged).
  They're kept with `symbol: null`; use `copy --skip-unmapped` to drop them.
- NSE's endpoints are unofficial and can change. Every command that fetches
  also accepts a downloaded file.
- This is not investment advice. It reads public data and does arithmetic.
