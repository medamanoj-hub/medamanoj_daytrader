---
name: india-tax
description: Capital-gains arithmetic for listed Indian shares. Keeps buy/sell lots (FIFO, as for demat holdings), works out STCG (20 %) and LTCG (12.5 % above ₹1.25 lakh a year), applies 31 Jan 2018 grandfathering and Indian loss set-off rules, estimates the year's tax, shows when each open lot turns long term, and suggests loss harvesting and LTCG-exemption harvesting. Use when the user asks about capital gains tax, which shares to sell for less tax, tax-loss harvesting, or their realised gains for a financial year in India.
---

# India Tax Helper

```
python3 <this-skill-folder>/tax.py --help
```

## Get the trades in

- Paper trades: `tax.py import-paper`
- Real trades: ask the user for their broker tradebook as CSV (Angel One:
  Reports → Tradebook) and run `tax.py import-csv FILE`. Include older
  buys of anything sold this year, or matching will be wrong.
- One-offs: `tax.py add BUY INFY 10 1450 --date 2024-03-12 --charges 18`
- Shares bought before 1 Feb 2018 need their 31 Jan 2018 fair market value
  in `trader/fmv_2018.json` (`{"INFY": 1150.0}`); it's the highest price
  that day. The tool warns when one is missing.

## Commands

- `tax.py gains --fy 2026-27`: realised gains, set-off, exemption used,
  tax estimate (with 4 % cess), losses to carry forward.
- `tax.py open --fetch`: open lots, unrealised gain, and the date each
  lot turns long term.
- `tax.py harvest --fetch`: (1) lots with losses and the tax each would
  save this year; (2) long-term gains that fit in the unused ₹1.25 lakh
  exemption.

## Rules it applies (check `trader/config.json` → `tax`)

- Held 12 months or less: short term, 20 %. More than 12 months: long
  term, 12.5 % above ₹1.25 lakh per financial year (April-March).
- Short-term losses offset short- or long-term gains; long-term losses
  only long-term gains. Unused losses carry forward 8 years if the return
  is filed on time; put them in `tax.brought_forward`.
- STT is not deductible; other charges are.
- Unchanged under the Income-tax Act, 2025 (from 1 April 2026); only the
  section numbers moved.

## Always say

- India has no US-style wash-sale rule, but a sale made only to book a
  loss can be questioned by the tax department, and selling and buying
  back costs charges both ways.
- Surcharge, slab-rate effects, and frequent trading being treated as
  business income are not modelled.
- It's an estimate, not tax advice. Suggest checking with a CA before
  filing or acting.
