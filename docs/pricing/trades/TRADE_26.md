# TRADE_26 — 5Y USD/TWD cross-currency swap (hedge TSM ADR FX)

**Asset class:** fx · **Trade type:** `xccy_swap` · **Methodology:** [FX forwards & cross-currency swaps](../methodology/40-fx.md)

## In plain English

A 5-year cross-currency swap in which we **receive USD fixed** and **pay TWD fixed**, with the two
principals exchanged. It hedges the Taiwan-dollar currency risk of the long TSM ADR
([TRADE_2](TRADE_2.md)). It is **seasoned** — it started in May 2025 at a contract rate of 30.00
TWD/USD and matures May 2030 — so its value reflects how the TWD has moved since: as TWD weakened
toward ~31.6, the receive-USD position gained.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $40.10mm (core book) |
| foreign ccy | TWD |
| usd notional | $40.10mm |
| contract fx | 30 |
| usd fixed rate | 3.800% |
| foreign fixed rate | 2.000% |
| start date | 2025-05-29 |
| maturity date | 2030-05-29 |
| direction | long_usd |
| frequency months | 12 |
| Currency | USD |

## Market data

**Conceptually required:** USD and TWD discount curves, plus the current USD/TWD spot.

**Actually used (free sources):** USD leg on the SOFR curve (Pensford); TWD leg on a **flat curve at
the CBC policy rate (2.0%)** — the free feeds give only a TWD spot fixing, no forward points, so no
term structure can be bootstrapped. Spot USD/TWD now comes from the **CBC NDF fixing**
([`cbc_twd_ndf_fix.csv`](../../../data/marketdata/fx/cbc_twd_ndf_fix.csv), B3) — the reference this
non-deliverable book settles against, and current where Fed H.10 (the old source, now backup) is
stale from ~2026-05-29. **Cross-currency basis is a config hook, `xccy_basis_bp` (C2), defaulting
to zero** — no free USD/TWD basis feed exists; a licensed basis drops in without code change. See
[FX methodology](../methodology/40-fx.md).

## How it's priced

Each leg is valued as a fixed-coupon stream plus notional redemption discounted on its own curve;
the TWD leg is converted to USD at the current spot, and `PV = PV_usd − PV_twd/spot`.

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $1.29mm**.

| Measure | Value |
|---|---|
| `spot` | 31.78 |
| `contract_fx` | 30 |
| `fx_delta_1pct` | 377,559.54 |
| `usd_notional` | $40.10mm |
| `foreign_notional` | 1,202,969,490 |
| `foreign_rate` | 0.02 |
| `xccy_basis_bp` | 0 |

Leg PVs: `usd_leg` = $39.42mm, `foreign_leg_usd` = $38.13mm.

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 60d — the illiquid tail (TWD is not freely convertible); a `√(60/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** **margined** — a MtM loss posts variation margin (in the cash-outflow line), and an IM-increase proxy is added under a vol shock.
- **The book's standout liquidity case:** in `taiwan_geopolitical` this trade is simultaneously the *least liquid* (60d, above) and the *most stressed* leg — the single largest exit-value correction in the book.
- **Taiwan (F8):** amplified by the GPR-TWN severity multiplier (×1.32, trigger live) — the same event that stresses it also makes its 60d horizon bite.

## Limitations & assumptions

XCCY basis via a config hook, default 0 (no free basis feed — understates basis risk until a licensed basis is set); flat TWD curve at a manually-maintained
CBC rate (no free forward points); spot from the CBC NDF fixing (B3).

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/fx/fx_pricer.py`](../../../src/pricer/ql/fx/fx_pricer.py) (`CrossCurrencySwapPricer`) |
| Market-data adapter | [`src/pricer/loader/fx_adapter.py`](../../../src/pricer/loader/fx_adapter.py) |
| Trade definition | [`src/pricer/input/fx/instruments.py`](../../../src/pricer/input/fx/instruments.py) |
