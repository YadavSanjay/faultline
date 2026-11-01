# Cash equity & ETFs

*Used by trades 1–6 (NVDA, TSM, AVGO, MU long; XLK long; SMH short) and the Part-2/N5 rebalance
additions 31–34, 37–38 (CEG, VST, GOOGL long; GLD ETF long; INTC long; AMD short).*

---

## In plain English

A cash-equity position is the simplest thing in the book: you own (or are short) a number of
shares, and the position is worth **shares × today's price**. There is no future cash flow to
discount and no optionality to model — the market has already told us the price, so "pricing" is
just looking up the latest close and multiplying. A short position (negative share count) simply
produces a negative value.

The only nuance is *which* price: we use the **last closing price on or before the valuation
date**. We also compute a trailing dividend yield as a diagnostic (it doesn't change the mark; it
just tells you how much income the holding throws off).

---

## How it's priced

> **Quantitative detail.** `PV = quantity × spot`, where `quantity` is signed (long > 0,
> short < 0) and `spot` is the daily close. That is the entire valuation — there is no
> discounting, no QuantLib instrument. (A `ql.SimpleQuote` wraps the price for interface
> consistency, but no QuantLib pricing engine runs.) Delta is trivially the position itself:
> `delta_shares = quantity`, `cash_delta = quantity × spot`. Trailing dividend yield is
> `Σ(dividends paid in the last 365 days) / spot`.

Code: [`src/pricer/ql/cash_equity/stock_pricer.py`](../../../src/pricer/ql/cash_equity/stock_pricer.py)
(`CashEquityPricer`).

---

## Market data

**Conceptually required:** a current share price; (optionally) a dividend stream and any
corporate-action adjustments.

**Actually used (free sources):**

| Input | Source file | Free source | Notes |
|---|---|---|---|
| Daily close | [`data/marketdata/equity/prices_daily.csv`](../../../data/marketdata/equity/prices_daily.csv) | Stooq historical OHLCV | `close` column; "latest on/before valuation date" |
| Dividends (diagnostic) | [`data/marketdata/equity/dividends.csv`](../../../data/marketdata/equity/dividends.csv) | hand-curated | trailing 365-day sum / spot |
| Splits | [`data/marketdata/equity/splits.csv`](../../../data/marketdata/equity/splits.csv) | hand-curated | audit trail only — **not** applied at valuation |

The as-of selection (latest close on/before the valuation date, with a staleness warning if the
gap exceeds tolerance) is the shared `pick_snapshot` logic in
[`staleness.py`](../../../src/pricer/loader/staleness.py).

---

## Reading the diagnostics

| Field | Meaning |
|---|---|
| `spot` | the close price used |
| `quantity` / `delta_shares` | signed share count (delta equals the position) |
| `cash_delta` | market value = PV |
| `gross_notional` | `abs(quantity) × spot` |
| `dividend_yield` | trailing 12-month dividend / spot |
| `direction` | `long` if quantity ≥ 0 else `short` |
| `spot_as_of` / `spot_source` | the date and source of the close used |

---

## Limitations & assumptions

- **Raw close, not adjusted close.** We use `close`; the CSV's `adj_close` exists but the pricer
  trusts the upstream feed to be split-adjusted. `splits.csv` is not actively applied.
- **Dividend yield is a trailing simple sum**, not ex-date adjusted — a diagnostic, not a mark.
- **No FX conversion for ADRs** (e.g. TSM): prices are stored in USD and the trade currency is
  assumed USD.
- **Share quantities are representative placeholders** sized to ≈$50mm gross per name.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/cash_equity/stock_pricer.py`](../../../src/pricer/ql/cash_equity/stock_pricer.py) |
| Market-data adapter | [`src/pricer/loader/cash_equity_adapter.py`](../../../src/pricer/loader/cash_equity_adapter.py) |
| Trade definition | [`src/pricer/input/cash_equity/instruments.py`](../../../src/pricer/input/cash_equity/instruments.py) |
