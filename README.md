# India Trader skills

11 Claude skills for the Indian stock market. Copy the mutual funds and
investors you trust, watch what company insiders and big investors trade,
follow the news and results, size every order to your budget in rupees, check it against your limits,
and never place an order without your yes.

Delivery (buy-and-hold) orders only: no intraday, F&O or margin, despite the repo name.

Inspired by [trader-skill](https://github.com/Jakeschincariol/trader-skill),
rebuilt for NSE/BSE, SEBI disclosures and Angel One.

Free, MIT, no signup, no API key. Plain Python (standard library only)
reading public data: AMC portfolio disclosures, NSE filings, Angel One's
public instrument file, Google News RSS, Yahoo Finance prices.

Paper trading is the default. Nothing in this pack can place a real order.

## The eleven

| command          | job                     | what it does |
| ---------------- | ----------------------- | ------------ |
| `/india-copycat` | Fund & investor copier  | Reads a mutual fund's monthly portfolio (ISIN, % to NAV) or companies' shareholding patterns (named holders above 1 %), rebuilds it as weights, shows what was bought, added, trimmed and sold. |
| `/india-insider` | Insider & deals tracker | SEBI insider trades (promoters, directors, KMP), NSE bulk and block deals, cluster-buying flags, alerts once per new filing. |
| `/india-budget`  | Budget manager          | Whole-share LIMIT orders in ₹, a cash buffer, a cap per stock, charges included (brokerage, STT, stamp duty, GST). |
| `/india-risk`    | Risk manager            | Blocks intraday, margin, F&O, market orders, T2T series, ASM/GSM stocks, oversized orders, short sells, stale prices, buffer breaches. |
| `/india-broker`  | Order ticket (Angel One)| Exact Angel One ticket, your typed yes per order, paper fills, and the steps to place real orders yourself in the app. |
| `/india-news`    | News monitor            | NSE announcements and headlines for your stocks, flagged HIGH / MEDIUM, routine filings hidden, alerts only on new items. |
| `/india-analyst` | Earnings analyst        | Quarterly revenue, profit, EPS and margins from NSE, growth QoQ and YoY, trailing EPS and P/E. |
| `/india-screener`| Stock screener          | Your own rules over the analyst's numbers, weighted ranking, reasons for every rejection. |
| `/india-tax`     | Tax helper              | FIFO lots, STCG 20 % / LTCG 12.5 % above ₹1.25 lakh, 2018 grandfathering, loss set-off, loss and exemption harvesting. |
| `/india-alerts`  | Price alerts            | Targets and stops, checked only while NSE is open, with the 2026 holiday calendar built in. |
| `/india-rebalance`| Rebalancer             | Drift table against your targets; sells first, cash-limited buys, buffer and cap kept. |

Why not a "Pelosi tracker"? Indian MPs don't disclose stock trades. Insider
(SEBI PIT) and bulk/block deal disclosures are India's public equivalent,
and they're about the companies themselves.

## Install

As a plugin, in Claude Code:

```
/plugin marketplace add medamanoj-hub/medamanoj_daytrader
/plugin install india-trader-skill@india-trader-skill
```

Or copy the skill folders:

```
git clone https://github.com/medamanoj-hub/medamanoj_daytrader.git
cp -r medamanoj_daytrader/skills/india-* ~/.claude/skills/
```

No Claude Code? Upload a skill folder as a zip in Claude's settings
(Capabilities → Skills), or paste a `SKILL.md` into a chat to use the
method without the tools.

Python 3.8 or newer. Nothing to pip install.

## How it fits together

Everything is written to a `trader/` folder in your project. Each skill
reads what the last one wrote:

```
copycat -> budget -> risk -> broker              (copy a fund or investor)
analyst -> screener -> budget -> risk -> broker  (pick your own)
news / insider / alerts                          (watch, on a schedule)
rebalance / tax -> risk -> broker                (maintain)
```

Example: copy a flexi-cap fund's top 5 with ₹50,000.

1. **`/india-copycat`** with the fund's September portfolio file from the
   AMC's website. It keeps only the equity lines, maps ISINs to NSE
   symbols, and shows the changes since August (new: L&T; exited: Axis
   Bank; added to HDFC Bank). It writes `trader/targets.json` with the top
   5 renormalised to 100 %.
2. **`/india-budget`** with ₹50,000, a 10 % buffer and a 25 % cap:
   7 HDFCBANK, 8 ICICIBANK, 3 RELIANCE, 4 INFY, 16 ITC as limit orders.
   ₹44,820 invested, about ₹106 in charges, ₹5,074 left as cash.
3. **`/india-risk`** checks each order. PASS or BLOCK, with the reason.
4. **`/india-broker`** shows each ticket: "Buy 7 HDFCBANK at ₹1,721.00
   limit, delivery, day order on NSE." You type yes. Paper mode fills it in
   `trader/portfolio.json`. In live mode you place it yourself in the
   Angel One app with the steps it prints.
5. On a schedule: **`/india-insider`** flags new insider trades and deals
   on your stocks, and anything by investors you name.
6. Every month: run copycat on the new file, diff it, and let
   **`/india-rebalance`** propose the trades. Before selling, **`/india-tax`**
   shows which lots are still short term.

## The tools

```
python3 skills/india-copycat/copycat.py symbols --fetch            # ISIN -> NSE symbol table
python3 skills/india-copycat/copycat.py sheets portfolio.xlsx      # one sheet per scheme
python3 skills/india-copycat/copycat.py mf portfolio.xlsx --sheet FLEXI --label flexi
python3 skills/india-copycat/copycat.py diff trader/holdings/flexi_2026-08-31.json trader/holdings/flexi_2026-09-30.json
python3 skills/india-copycat/copycat.py copy trader/holdings/flexi_2026-09-30.json --top 5
python3 skills/india-copycat/copycat.py investor "Kacholia" --symbols SYM1,SYM2

python3 skills/india-insider/insider.py pit --days 30 --market-only --insiders-only
python3 skills/india-insider/insider.py deals --kind bulk --days 7 --who KACHOLIA
python3 skills/india-insider/insider.py new --who KACHOLIA DAMANI    # for schedules

python3 skills/india-budget/size.py --budget 50000 --fetch --write
python3 skills/india-risk/check.py
python3 skills/india-broker/ticket.py master --fetch
python3 skills/india-broker/ticket.py show
python3 skills/india-broker/ticket.py approve O1 --confirm yes     # only after you typed yes
python3 skills/india-broker/ticket.py paper O1

python3 skills/india-news/news.py scan --hours 48 --min-flag MEDIUM
python3 skills/india-analyst/analyst.py facts TCS INFY --prices prices.json
python3 skills/india-screener/screen.py run --why --targets equal
python3 skills/india-rebalance/rebalance.py --fetch --band 5
python3 skills/india-tax/tax.py import-csv tradebook.csv
python3 skills/india-tax/tax.py gains --fy 2026-27
python3 skills/india-tax/tax.py harvest --fetch
python3 skills/india-alerts/alerts.py clock
python3 skills/india-alerts/alerts.py add RELIANCE below 2800 --kind stop
python3 skills/india-alerts/alerts.py check --fetch
```

Tests run offline on synthetic fixtures:

```
python3 -m unittest discover -s tests -v
```

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It doesn't know your situation and can't see the future.
Copying a fund's holdings doesn't copy its results. You can lose money.
Talk to a SEBI-registered adviser if you need advice.

**No order without your yes.** Only `/india-broker` leads toward an order,
only after `/india-risk` passes that exact order (it is fingerprinted; any
change needs a new check) and you type yes for it. Claude never places a
live order: you place real orders yourself in the Angel One app.

**Paper first.** `"paper": true` is the default. Keep your Angel One
password, PIN, TOTP secret and API keys out of the chat and out of `trader/`.

**SEBI's API rules.** Since 1 April 2026, retail orders sent through a
broker's API must come from a registered app on a whitelisted static IP,
with daily 2FA, and API market orders are converted to price-protected
orders. That's one more reason this pack uses limit orders and leaves
placing them to you.

**Data is delayed and partial.** Fund portfolios are as of month end.
Shareholding patterns are quarterly and only name holders above 1 %.
Insider disclosures arrive days after the trade. Bulk/block deals are
same-day.

**Unofficial endpoints.** NSE's JSON endpoints and Yahoo's price endpoint
are not official APIs and can change or block scripts. Every fetching
command also accepts a file you download by hand. Check anything that
matters against your broker before you act.

**Charges are estimates.** The rate card is in `trader/config.json`.
Check your plan; some Angel One plans charge zero delivery brokerage.

**Taxes.** `/india-tax` does the arithmetic for listed shares held by a
resident individual: 20 % short term, 12.5 % long term above ₹1.25 lakh a
year (rates as of FY 2026-27, unchanged under the Income-tax Act, 2025),
FIFO lots, 2018 grandfathering and set-off. It ignores surcharge and
business-income treatment. India has no wash-sale rule, but sales made
only to book a loss can be questioned. Not tax advice; check with a CA.

**Alerts only alert.** A "stop" in `/india-alerts` never sells. For a hard
stop, place a stop-loss or GTT order in the Angel One app yourself.

## Files

```
skills/india-copycat/  SKILL.md, copycat.py, nse_http.py
skills/india-insider/  SKILL.md, insider.py, nse_http.py
skills/india-budget/   SKILL.md, size.py, trader_common.py, config.example.json
skills/india-risk/     SKILL.md, check.py, trader_common.py
skills/india-broker/   SKILL.md, ticket.py, trader_common.py
skills/india-news/     SKILL.md, news.py, nse_http.py
skills/india-analyst/  SKILL.md, analyst.py, nse_http.py
skills/india-screener/ SKILL.md, screen.py
skills/india-tax/      SKILL.md, tax.py, trader_common.py
skills/india-alerts/   SKILL.md, alerts.py, trader_common.py, nse_http.py
skills/india-rebalance/ SKILL.md, rebalance.py, trader_common.py
tests/                 unittest suite, fixture builder, synthetic fixtures
```

Shared helpers are copied into each skill so any one skill works on its
own; a test keeps the copies identical.

MIT.
