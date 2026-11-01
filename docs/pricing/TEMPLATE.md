# TRADE_<n> — <one-line description>

> Copy this file to `trades/TRADE_<n>.md` and fill in each section. Keep the trade page short
> (≈1–2 pages): the deep "how the math works" belongs in the linked methodology doc, not here.
> Delete these quote blocks when done.

**Asset class:** <equity | options | bonds | rates | fx | credit> ·
**Trade type:** `<trade_type from portfolio.json>` ·
**Methodology:** [<doc title>](../methodology/<doc>.md)

---

## In plain English

> 2–4 sentences a non-quant can follow: what the position *is*, why the book holds it
> (hedge? carry? directional?), and what makes it worth money today.

## The trade (economics)

> The contractual terms straight from `portfolio.json` — notional, rate/strike, tenor,
> direction, dates. State clearly which terms are **representative placeholders** vs. real.

| Term | Value |
|---|---|
| Notional | |
| … | |

## Market data

**Conceptually required** to price this instrument:

> List the curves / vols / spots / spreads the *model* needs in principle.

**Actually used** (free sources only):

> The exact file(s) under `data/marketdata/…` and the public source behind each, plus any
> assumption we substitute where no free data exists (flat vol, config fallback spread, etc.).

| Input | Source file | Free source | Notes / assumption |
|---|---|---|---|
| | | | |

## How it's priced

> A short, trade-specific walk-through: the engine used, the 3–6 steps from inputs to PV,
> and a pointer to the methodology doc for the full derivation. Include the key formula in a
> "Quantitative detail" box if it helps.

## Reading the live diagnostics

> Reproduce the trade's diagnostics block (from the UI / per-trade JSON) and explain each
> field in one line, so a reader looking at the screen knows what they're seeing.

## Risk & stress treatment

> **Auto-generated** by `scripts/pricer/gen_trade_docs.py` — do not hand-edit. How the trade is
> treated in the risk & stress engines (market-intelligence P0–P7): its liquidity horizon (F1) and
> margined/collateral status (F2), read from `configs/scenariogen/liquidity.json` +
> `collateral.json`, plus per-trade basis / scenario / crowding / driver notes (the `STRESS_NOTES`
> map in the script). Pricing (PV) is unchanged by that work — these are scenario/VaR dimensions.

## Limitations & assumptions

> What a reviewer should know: free-data gaps, flat-vol/flat-rate simplifications, placeholder
> economics, and how the number would differ with production (licensed) data.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | |
| Market-data adapter | |
| Trade definition | |
| Config | |
