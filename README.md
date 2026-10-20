# India Trader skills

5 Claude skills for the Indian stock market. Copy the mutual funds and
investors you trust, watch what company insiders and big investors trade,
size every order to your budget in rupees, check it against your limits,
and never place an order without your yes.

Inspired by [trader-skill](https://github.com/Jakeschincariol/trader-skill),
rebuilt for NSE/BSE, SEBI disclosures and Angel One.

Free, MIT, no signup, no API key. Plain Python (standard library only)
reading public data: AMC portfolio disclosures, NSE filings, Angel One's
public instrument file.

Paper trading is the default. Nothing in this pack can place a real order.

## The five

| command          | job                     | what it does |
| ---------------- | ----------------------- | ------------ |
| `/india-copycat` | Fund & investor copier  | Reads a mutual fund's monthly portfolio (ISIN, % to NAV) or companies' shareholding patterns (named holders above 1 %), rebuilds it as weights, shows what was bought, added, trimmed and sold. |
| `/india-insider` | Insider & deals tracker | SEBI insider trades (promoters, directors, KMP), NSE bulk and block deals, cluster-buying flags, alerts once per new filing. |
| `/india-budget`  | Budget manager          | Whole-share LIMIT orders in ₹, a cash buffer, a cap per stock, charges included (brokerage, STT, stamp duty, GST). |
| `/india-risk`    | Risk manager            | Blocks intraday, margin, F&O, market orders, T2T series, ASM/GSM stocks, oversized orders, short sells, stale prices, buffer breaches. |
| `/india-broker`  | Order ticket (Angel One)| Exact Angel One ticket, your typed yes per order, paper fills, and the steps to place real orders yourself in the app. |

Why not a "Pelosi tracker"? Indian MPs don't disclose stock trades. Insider
(SEBI PIT) and bulk/block deal disclosures are India's public equivalent,
and they're about the companies themselves.

## Install

As a plugin, in Claude Code:

```
/plugin marketplace add <your-github-user>/india-trader-skill
/plugin install india-trader-skill@india-trader-skill
```

Or copy the skill folders:

```
git clone https://github.com/<your-github-user>/india-trader-skill.git
cp -r india-trader-skill/skills/india-* ~/.claude/skills/
```

No Claude Code? Upload a skill folder as a zip in Claude's settings
(Capabilities → Skills), or paste a `SKILL.md` into a chat to use the
method without the tools.

Python 3.8 or newer. Nothing to pip install.

## How it fits together

Everything is written to a `trader/` folder in your project. Each skill
reads what the last one wrote:

```
copycat -> budget -> risk -> broker     (copy a fund or investor)
insider                                 (watch, on a schedule)
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
6. Every month: run copycat on the new file, diff, and re-size.

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

**Taxes.** Not covered here yet. Not tax advice.

## Files

```
skills/india-copycat/  SKILL.md, copycat.py, nse_http.py
skills/india-insider/  SKILL.md, insider.py, nse_http.py
skills/india-budget/   SKILL.md, size.py, trader_common.py, config.example.json
skills/india-risk/     SKILL.md, check.py, trader_common.py
skills/india-broker/   SKILL.md, ticket.py, trader_common.py
tests/                 unittest suite, fixture builder, synthetic fixtures
```

Shared helpers are copied into each skill so any one skill works on its
own; a test keeps the copies identical.

MIT.
