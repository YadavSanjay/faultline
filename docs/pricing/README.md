# Pricing Methodology & Per-Trade Write-Ups

This folder explains, in plain English first and quantitative detail second, **how every
trade in the portfolio is priced** — what market data the pricing *conceptually* needs,
what data we *actually* feed it (we restrict ourselves to **free public sources**), and the
step-by-step method the QuantLib engine uses to turn that data into a present value (PV).

It is written for two readers at once:

- **Business / non-quant readers** — read the *"In plain English"* section at the top of
  every page. You can understand what a trade is worth and why without any math.
- **Quants / model validation / engineers** — read the *"Quantitative detail"* boxes and the
  *"Where this lives in the code"* tables for the exact engine, formula, and config keys.

> Everything here is grounded in the actual code in [`src/pricer/`](../../src/pricer/), the
> config in [`configs/pricer/pricer_config.json`](../../configs/pricer/pricer_config.json),
> and real per-trade output under `data/pricer/PV/`. Example numbers are taken from a specific
> valuation snapshot (the current default is **2026-10-06**; some older pages cite an earlier
> snapshot) and will move with the market data.

---

## How the docs are organised

```
docs/pricing/
  README.md            ← you are here (index)
  TEMPLATE.md          ← copy this to document any NEW trade
  methodology/         ← the deep "how it's priced" by instrument type (shared across trades)
  trades/              ← one 1-2 page page per trade (economics + data + which method applies)
```

The **methodology** docs hold the heavy, reusable explanation (multi-curve construction,
Bachelier swaptions, Hull-White Bermudans, the ISDA CDS model, …). The **trade** pages are
short and specific: each one states the trade's economics, the exact market data it consumes,
points to the relevant methodology doc, and interprets that trade's live diagnostics.

---

## Methodology library

| Topic | Doc | Used by |
|---|---|---|
| The shared pricing envelope (valuation date, provenance, snapshot, data-quality) | [methodology/00-foundations.md](methodology/00-foundations.md) | **all trades** |
| Discounting & the SOFR multi-curve (bootstrap from Pensford forwards) | [methodology/01-sofr-multicurve.md](methodology/01-sofr-multicurve.md) | rates, bonds, options, FX, credit |
| Cash equity & ETFs (mark-to-market) | [methodology/10-cash-equity.md](methodology/10-cash-equity.md) | 1–6, 31–34, 37–38 |
| Equity options (Black-Scholes-Merton, American FD, multi-leg) | [methodology/11-equity-options.md](methodology/11-equity-options.md) | 7–14, 35 |
| Fixed-rate bonds (corporate + Treasury, credit spread) | [methodology/20-bonds.md](methodology/20-bonds.md) | 15–21, 36 |
| Interest-rate swaps (SOFR OIS) | [methodology/30-rates-swaps.md](methodology/30-rates-swaps.md) | 22 |
| European swaptions (normal / Bachelier vol) | [methodology/31-swaptions.md](methodology/31-swaptions.md) | 23 |
| Bermudan swaptions (Hull-White, tree pricing) | [methodology/32-bermudan-swaption.md](methodology/32-bermudan-swaption.md) | 24 |
| Caps / floors (Bachelier caplets) | [methodology/33-caps.md](methodology/33-caps.md) | 25 |
| FX forwards & cross-currency swaps (covered interest parity) | [methodology/40-fx.md](methodology/40-fx.md) | 26–27 |
| Credit default swaps (ISDA model, hazard calibration) | [methodology/50-credit-cds.md](methodology/50-credit-cds.md) | 28–30 |

## Per-trade write-ups

| # | Trade | Type | Page |
|---|---|---|---|
| 1 | NVDA common — long | cash equity | [trades/TRADE_1.md](trades/TRADE_1.md) |
| 2 | TSM ADR — long | cash equity | [trades/TRADE_2.md](trades/TRADE_2.md) |
| 3 | AVGO common — long | cash equity | [trades/TRADE_3.md](trades/TRADE_3.md) |
| 4 | MU common — long | cash equity | [trades/TRADE_4.md](trades/TRADE_4.md) |
| 5 | XLK ETF — long | cash equity | [trades/TRADE_5.md](trades/TRADE_5.md) |
| 6 | SMH ETF — short | cash equity | [trades/TRADE_6.md](trades/TRADE_6.md) |
| 7 | AAPL Jan-27 LEAPS call (American) | equity option | [trades/TRADE_7.md](trades/TRADE_7.md) |
| 8 | NVDA 6M ATM put (American) | equity option | [trades/TRADE_8.md](trades/TRADE_8.md) |
| 9 | MSFT 3M call spread (American) | equity option | [trades/TRADE_9.md](trades/TRADE_9.md) |
| 10 | META 6M put spread (American) | equity option | [trades/TRADE_10.md](trades/TRADE_10.md) |
| 11 | TSLA 1M straddle (American) | equity option | [trades/TRADE_11.md](trades/TRADE_11.md) |
| 12 | SPX 3M ATM put (European) | equity option | [trades/TRADE_12.md](trades/TRADE_12.md) |
| 13 | SPX 6M put spread (European) | equity option | [trades/TRADE_13.md](trades/TRADE_13.md) |
| 14 | QQQ 3M collar (American) | equity option | [trades/TRADE_14.md](trades/TRADE_14.md) |
| 15 | Apple 4.75% 2035 | corp bond | [trades/TRADE_15.md](trades/TRADE_15.md) |
| 16 | Microsoft 4.10% 2037 | corp bond | [trades/TRADE_16.md](trades/TRADE_16.md) |
| 17 | Oracle 5.55% 2053 | corp bond | [trades/TRADE_17.md](trades/TRADE_17.md) |
| 18 | Meta 5.40% 2054 | corp bond | [trades/TRADE_18.md](trades/TRADE_18.md) |
| 19 | Amazon 4.70% 2032 | corp bond | [trades/TRADE_19.md](trades/TRADE_19.md) |
| 20 | UST 10Y on-the-run | Treasury | [trades/TRADE_20.md](trades/TRADE_20.md) |
| 21 | UST 2Y on-the-run | Treasury | [trades/TRADE_21.md](trades/TRADE_21.md) |
| 22 | 10Y SOFR payer IRS | rates swap | [trades/TRADE_22.md](trades/TRADE_22.md) |
| 23 | 5Y×10Y European payer swaption | swaption | [trades/TRADE_23.md](trades/TRADE_23.md) |
| 24 | 2Y×5Y Bermudan receiver swaption | swaption | [trades/TRADE_24.md](trades/TRADE_24.md) |
| 25 | 1Y SOFR cap | cap | [trades/TRADE_25.md](trades/TRADE_25.md) |
| 26 | 5Y USD/TWD cross-currency swap | FX | [trades/TRADE_26.md](trades/TRADE_26.md) |
| 27 | 3M USD/JPY FX forward | FX | [trades/TRADE_27.md](trades/TRADE_27.md) |
| 28 | 5Y ORCL CDS — buy protection | credit | [trades/TRADE_28.md](trades/TRADE_28.md) |
| 29 | 5Y META CDS — buy protection | credit | [trades/TRADE_29.md](trades/TRADE_29.md) |
| 30 | 5Y CDX.NA.IG — sell protection | credit | [trades/TRADE_30.md](trades/TRADE_30.md) |
| 31 | CEG common — long (AI-power) | cash equity | [trades/TRADE_31.md](trades/TRADE_31.md) |
| 32 | VST common — long (AI-power) | cash equity | [trades/TRADE_32.md](trades/TRADE_32.md) |
| 33 | GOOGL common — long (hyperscaler) | cash equity | [trades/TRADE_33.md](trades/TRADE_33.md) |
| 34 | GLD ETF — long (gold ballast) | cash equity | [trades/TRADE_34.md](trades/TRADE_34.md) |
| 35 | SPX 6M ~85% put (European) — convexity | equity option | [trades/TRADE_35.md](trades/TRADE_35.md) |
| 36 | UST 1Y note — long (liquidity buffer) | Treasury | [trades/TRADE_36.md](trades/TRADE_36.md) |
| 37 | INTC common — long (dispersion pair) | cash equity | [trades/TRADE_37.md](trades/TRADE_37.md) |
| 38 | AMD common — short (dispersion pair) | cash equity | [trades/TRADE_38.md](trades/TRADE_38.md) |

---

## Adding a new trade

1. Add the trade to [`src/pricer/portfolio/portfolio.json`](../../src/pricer/portfolio/portfolio.json).
2. Copy [TEMPLATE.md](TEMPLATE.md) to `trades/TRADE_<n>.md` and fill in the hand-authored sections.
   The **The trade (economics)**, **Reading the live diagnostics**, and **Risk & stress treatment**
   sections are **regenerated** by `scripts/pricer/gen_trade_docs.py` — the first two from the stored
   PV result, the third from the scenariogen liquidity/collateral configs + the script's per-trade
   notes; add a `STRESS_NOTES` entry there for any distinctive basis/scenario/crowding treatment.
3. If the trade introduces a genuinely new *instrument type* (not just new economics on an
   existing type), add a methodology doc under `methodology/` and link it from the table above.
   Otherwise just point the new trade page at the existing methodology doc.
4. Re-run `python -m src.pricer.lineage_runner --trade TRADE_<n>` so the UI's calculation-lineage
   view matches the write-up.

The point of the split is that **economics live in the trade page, method lives once in the
methodology doc** — so a tenth bond doesn't re-explain bond pricing, it just states its coupon,
maturity and credit spread and links out.
