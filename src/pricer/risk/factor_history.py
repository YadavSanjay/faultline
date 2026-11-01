"""Historical risk-factor moves for full-revaluation VaR (P2).

Loads each factor's market-data time series, converts it to the daily move that the
bump engine consumes, and aligns everything on common dates so the simulated days are
real co-moving market states (not independently shuffled factors). One aligned row =
one historical scenario, turned into a list of bump transforms by `DayShock.transforms`.

Move conventions (per the coverage analysis):
  IR      DGS2/5/10/30 daily change, pct -> decimal rate (curve-shape shock)
  EQUITY  per-name daily return for the cash book + option underliers
          (NVDA/TSM/AVGO/MU/SMH/XLK/AAPL/MSFT/META/TSLA/QQQ/SPX, GAP 2); XLK remains the
          systematic-proxy default for any unmapped name
  EQ_VOL  VIX daily change, vol points -> decimal IV shift (additive, level)
  EQ_VOL_TERM  (VIX3M-VIX) daily change -> decimal term-slope tilt (GAP 4)
  EQ_VOL_SKEW  CBOE SKEW daily change x skew_vol_per_point -> put-skew tilt (GAP 4)
  RT_VOL  MOVE daily return (relative normal-vol shift)
  FX      usdjpy / usdtwd daily return (per currency)
  CREDIT  FRED ICE-BofA IG OAS daily change (decimal, additive); DTCC CDX median fallback
  FX_RATE BoJ TONAR daily change (decimal, additive) — JPY foreign-rate rho (GAP 5)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from src.common import storage
from src.common.snapshot import FileSnapshot

from ..config_models import RiskConfig, risk_config
from . import bumps
from .issuer_credit import issuer_daily_spreads, issuer_spread_moves, proportional_backfill

# Curve-shape control nodes, ascending in years (GAP 3: front-end + belly + 20Y added so
# the curve shock resolves steepeners/flatteners/butterflies, not just a 4-node parallel-ish
# move). Must stay ascending — DayShock feeds these years to np.interp. Each is used only if
# present in fred_rates.csv. Keep in sync with scenariogen.factor_keys.IR_TENOR_YEARS.
_IR_TENOR_YEARS = {
    "DGS1MO": 1.0 / 12.0, "DGS3MO": 0.25, "DGS6MO": 0.5, "DGS1": 1.0,
    "DGS2": 2.0, "DGS3": 3.0, "DGS5": 5.0, "DGS7": 7.0,
    "DGS10": 10.0, "DGS20": 20.0, "DGS30": 30.0,
}
# Cash book + option underliers (GAP 2: AAPL/MSFT/META/TSLA/QQQ/SPX now have spot history).
# Part-2 diversifiers get their OWN factors: GLD (gold ballast — low/neg correlation to tech) and,
# since their price history is already on disk for pricing, the AI-power/hyperscaler longs CEG/VST/
# GOOGL too (2026-07-26 refinement — own factors credit their real, lower correlation instead of the
# earlier shared XLK proxy). CEG only trades from its Feb-2022 spin-off (~1,132 days), still well
# inside the 750/250-day risk windows, so it does not truncate the aligned panel. See
# docs/design/portfolio_rebalance_review.md.
#   N5 (2026-07-27): INTC/AMD are the legs of the semis dispersion pair (long laggard / short leader).
#   They MUST be own factors — a shared proxy would net the correlated long/short to ~zero and erase
#   the very dispersion risk the pair expresses.
_EQUITY_TICKERS = ("NVDA", "TSM", "AVGO", "MU", "SMH", "XLK",
                   "AAPL", "MSFT", "META", "TSLA", "QQQ", "SPX",
                   "GLD", "CEG", "VST", "GOOGL", "INTC", "AMD")
_SYSTEMATIC_EQUITY = "XLK"
# The single-name semiconductor longs (book trades #1-4) that trade #6 shorts SMH against; the
# ETF-vs-basket dispersion basis (F3) is SMH's return minus this equal-weight basket's.
_SEMI_BASKET = ("NVDA", "TSM", "AVGO", "MU")


# Per-issuer credit factor keys (scenariogen factor_keys.issuer_credit_key; pricer cannot import scenariogen).
_ISSUER_CREDIT_PREFIX = "credit:"

# Panel-carry defaults, from the validated config model (single source of truth).
_PANEL_DEFAULTS = RiskConfig()


def splice_backfill(primary: pd.Series, backfill: pd.Series | None,
                    beta: float | None = None, min_overlap: int = 20) -> pd.Series:
    """Extend a factor's daily-move series backwards with a long-history proxy.

    The ICE-licensed IG-OAS series (BAMLC0A0CM) is only served ~3y back by FRED, which
    throttles the whole VaR window at the credit factor (all other factors reach ~2y further).
    We keep the OAS moves where they exist and prepend the proxy's moves for the earlier dates
    only, vol-matched so the spliced tail has the OAS's amplitude (``beta`` = sigma_primary /
    sigma_proxy over the overlap, or a configured override). This is a transparent proxy splice;
    the result is labelled in the report so a reviewer sees where the history came from.
    """
    if backfill is None or backfill.empty:
        return primary
    if primary is None or primary.empty:
        return (backfill * beta) if beta is not None else backfill
    if beta is None:
        overlap = primary.index.intersection(backfill.index)
        if len(overlap) >= min_overlap:
            b_std = float(backfill.reindex(overlap).std(ddof=1))
            beta = float(primary.reindex(overlap).std(ddof=1) / b_std) if b_std > 0 else 1.0
        else:
            beta = 1.0
    start = primary.index.min()
    pre = backfill[backfill.index < start] * beta
    if pre.empty:
        return primary
    return pd.concat([pre, primary]).sort_index()


def carry_short_gaps(panel: pd.DataFrame, *, max_gap: int, min_coverage: float) -> pd.DataFrame:
    """Carry each factor's level across short gaps in the outer-joined move panel.

    Keeps only the eligible rows (share of non-NaN columns >= ``min_coverage``); on those rows,
    fills each column's interior run of at most ``max_gap`` consecutive NaNs with ``0.0`` (a zero
    move). Longer runs and leading/trailing runs stay NaN; rows still holding a NaN are not dropped.

    Args:
        panel: Daily factor moves; rows are the union of dates (strictly increasing), columns are
            factors, NaN means the factor has no move that date. Not mutated.
        max_gap: Longest interior NaN run to fill, in rows; 0 fills nothing.
        min_coverage: Minimum observed share of columns for a row to stay, in (0, 1].

    Returns:
        The eligible rows in input order, all columns in input order, short interior gaps filled.

    Raises:
        ValueError: If ``max_gap < 0``, ``min_coverage`` is outside (0, 1], or the index is not
            strictly increasing.
    """
    if max_gap < 0:
        raise ValueError(f"max_gap must be >= 0, got {max_gap}")
    if not 0.0 < min_coverage <= 1.0:   # also rejects NaN
        raise ValueError(f"min_coverage must be in (0, 1], got {min_coverage}")
    if panel.empty:
        return panel.copy()
    if not (panel.index.is_monotonic_increasing and panel.index.is_unique):
        raise ValueError("panel index must be strictly increasing")

    observed = panel.notna()
    share = observed.sum(axis=1) / panel.shape[1]
    out = panel.loc[share >= min_coverage].copy()
    if max_gap == 0 or out.empty:
        return out

    missing = out.isna()
    seen = ~missing
    # Interior = an observed value earlier and later in the column (on the eligible rows).
    interior = seen.cumsum().gt(0) & seen.iloc[::-1].cumsum().iloc[::-1].gt(0)
    # Length of each NaN run: NaNs sharing the same count of observed values before them.
    fill = pd.DataFrame(False, index=out.index, columns=out.columns)
    run_key = seen.cumsum()
    for col in out.columns:
        run_len = missing[col].groupby(run_key[col]).transform("sum")
        fill[col] = missing[col] & interior[col] & (run_len <= max_gap)
    return out.mask(fill, 0.0)


@dataclass(frozen=True)
class DayShock:
    """One historical day's factor moves -> bump transforms."""

    date: str
    ir_points_years: tuple[float, ...]
    ir_points_shift: tuple[float, ...]   # decimal rate change per control tenor
    equity_by_name: dict[str, float]     # ticker -> return
    equity_systematic: float             # proxy return for unmapped underliers
    equity_vol_pts: float                # decimal IV shift (additive, level)
    rates_vol_rel: float                 # relative normal-vol shift
    fx_by_ccy: dict[str, float]          # ccy -> return
    credit_shift: float                  # decimal spread change (additive)
    fxrate_by_ccy: dict[str, float]      # ccy -> foreign-rate change (decimal, additive)
    equity_vol_term: float               # decimal vol term-slope change (GAP 4)
    equity_vol_skew: float               # decimal put-skew tilt change (GAP 4)
    # issuer -> decimal spread change (issuer-credit-factors); overrides credit_shift for that issuer
    credit_by_issuer: dict[str, float] = field(default_factory=dict)

    def transforms(self) -> list[bumps.Transform]:
        return [
            bumps.shift_ir_curve(self.ir_points_years, self.ir_points_shift),
            bumps.scale_equity_named(self.equity_by_name, default=self.equity_systematic),
            bumps.shift_equity_vol(self.equity_vol_pts),
            bumps.scale_rates_vol(self.rates_vol_rel),
            bumps.scale_fx_named(self.fx_by_ccy, default=0.0),
            bumps.shift_credit_issuers(self.credit_by_issuer, self.credit_shift),
            bumps.shift_fx_rate_named(self.fxrate_by_ccy, default=0.0),
            bumps.shift_equity_vol_term(self.equity_vol_term),
            bumps.shift_equity_vol_skew(self.equity_vol_skew),
        ]


class FactorHistory:
    # Class-level defaults so a subclass that skips __init__ still aligns; __init__ sets them from config.
    panel_max_gap_days: int = _PANEL_DEFAULTS.panel_max_gap_days
    panel_min_coverage: float = _PANEL_DEFAULTS.panel_min_coverage

    def __init__(self, project_root: Path, config: dict[str, Any]):
        self.md = storage.join(storage.data_base(project_root), "marketdata")
        self.cfg = config.get("risk", {}).get("hist_var", {})
        # CDS reference name filters (credit.references), shared with the CDS curve build.
        self.credit_refs: dict[str, Any] = dict(config.get("credit", {}).get("references", {}))
        # issuer -> provenance of its credit:<ISSUER> series (DTCC span, backfill beta).
        self.issuer_credit_info: dict[str, dict[str, Any]] = {}
        risk = risk_config(config)   # validated: raises ConfigError on a bad panel setting
        self.panel_max_gap_days = risk.panel_max_gap_days
        self.panel_min_coverage = risk.panel_min_coverage
        # factor -> cells carried (a 0.0 move filled by carry_short_gaps) in the last aligned window.
        self.panel_carried_cells: dict[str, int] = {}

    def input_relpaths(self) -> list[str]:
        """Every factor-history file this loader can read (posix, relative to data/marketdata).

        The single source of truth for "what data a VaR/scenario run consumes"; fingerprinting
        this set yields the content-addressed `factor_snapshot_id` that pins the run.
        """
        return sorted({
            "rates/fred_rates.csv",
            "equity/prices_daily.csv",
            "options/vol_indices/VIX_History.csv",
            "options/vol_indices/VIX3M_History.csv",
            "options/vol_indices/SKEW_History.csv",
            "stress/move_index.csv",
            "fx/ecb_fx_rates.csv",
            "fx/fed_h10_fx_rates.csv",
            "fx/boj_tonar.csv",
            str(self.cfg.get("credit_file", "stress/fred_stress_macro.csv")),
            "credit/dtcc_index_cds.csv",
            "credit/dtcc_single_name_cds.csv",   # credit:<ISSUER> factors (issuer-credit-factors)
            "factors/ff_factors.csv",   # F4 style:MOM driver (scenariogen include_style); pinned for reproducibility
            "rates/fred_real_rates.csv",       # F6 real:/be: drivers (scenariogen include_style)
            "rates/nyfed_acm_term_premium.csv",  # F6 tp: term-premium driver
        })

    def snapshot(self) -> FileSnapshot:
        """Content-addressed fingerprint of the factor-history inputs for this run."""
        return FileSnapshot.build(self.md, self.input_relpaths())

    def day_shocks(self, lookback: int = 250) -> list[DayShock]:
        panel = self._aligned_moves(lookback)
        ir_cols = [c for c in panel.columns if c.startswith("ir:")]
        years = tuple(_IR_TENOR_YEARS[c[3:]] for c in ir_cols)
        eq_cols = [c for c in panel.columns if c.startswith("eq:")]
        issuer_cols = [c for c in panel.columns if c.startswith(_ISSUER_CREDIT_PREFIX)]

        shocks: list[DayShock] = []
        for dt, row in panel.iterrows():
            shocks.append(DayShock(
                date=str(pd.Timestamp(dt).date()),
                ir_points_years=years,
                ir_points_shift=tuple(float(row[c]) for c in ir_cols),
                equity_by_name={c[3:]: float(row[c]) for c in eq_cols},
                equity_systematic=float(row.get(f"eq:{_SYSTEMATIC_EQUITY}", 0.0)),
                equity_vol_pts=float(row.get("eqvol", 0.0)),
                rates_vol_rel=float(row.get("ratesvol", 0.0)),
                fx_by_ccy={"JPY": float(row.get("fx:JPY", 0.0)),
                           "TWD": float(row.get("fx:TWD", 0.0))},
                credit_shift=float(row.get("credit", 0.0)),
                fxrate_by_ccy={"JPY": float(row.get("fxrate:JPY", 0.0))},
                equity_vol_term=float(row.get("eqvol_term", 0.0)),
                equity_vol_skew=float(row.get("eqvol_skew", 0.0)),
                credit_by_issuer={c[len(_ISSUER_CREDIT_PREFIX):]: float(row[c]) for c in issuer_cols},
            ))
        return shocks

    def aligned_panel(self, lookback: int = 250, *, include_style: bool = False) -> pd.DataFrame:
        """Public accessor: the date-aligned factor-move panel (one row per common
        trading day; columns are the canonical factor keys ``ir:DGS*`` / ``eq:<TKR>`` /
        ``eqvol`` / ``ratesvol`` / ``fx:<CCY>`` / ``credit``).

        The full-revaluation VaR replays this to reprice, and scenariogen's covariance builder
        consumes it, so the estimated Σ is over the same aligned, co-moving series.

        ``include_style`` (scenariogen only) additionally joins the Ken French ``style:MOM``
        factor (F4), so "momentum crash" can be an authorable covariance-propagated driver. It is
        **off by default** — the full-reval path leaves it off, so its window and P&L are unchanged.
        The style series can lag the market feeds by a few weeks (French's monthly refresh), so the
        style-augmented panel's inner join can end earlier; that is why only scenariogen (which
        wants the co-movement) opts in, not the daily full-reval measure.
        """
        return self._aligned_moves(lookback, include_style=include_style)

    # -- per-factor series --------------------------------------------------- #
    def factor_series(self, *, include_style: bool = False) -> dict[str, pd.Series]:
        """Each factor's own full daily-move series, not date-aligned or truncated.

        Same canonical keys and units as ``aligned_panel``. The scenario forward-scoring log uses it
        to measure each factor's realised move over its own trading days, so one factor's shorter
        history or holiday calendar doesn't shorten every other factor's window.
        """
        return self._collect_series(include_style=include_style)

    def _aligned_moves(self, lookback: int, *, include_style: bool = False) -> pd.DataFrame:
        series = self._collect_series(include_style=include_style)
        raw = pd.concat(series, axis=1)
        carried = carry_short_gaps(raw, max_gap=self.panel_max_gap_days, min_coverage=self.panel_min_coverage)
        window = carried.dropna(how="any").tail(lookback)
        filled = raw.reindex(index=window.index, columns=window.columns).isna()
        self.panel_carried_cells = {str(c): int(n) for c, n in filled.sum().items()}
        return window

    def _collect_series(self, *, include_style: bool = False) -> dict[str, pd.Series]:
        series: dict[str, pd.Series] = {}
        series.update(self._ir())
        series.update(self._equity())
        series.update({name: s for name, s in [
            ("eqvol", self._equity_vol()),
            ("eqvol_term", self._equity_vol_term()),
            ("eqvol_skew", self._equity_vol_skew()),
            ("ratesvol", self._rates_vol()),
            ("credit", self._credit()),
        ] if s is not None})
        series.update(self._fx())
        series.update(self._foreign_rate())
        series.update(self._issuer_credit(series.get("credit")))
        if include_style:
            series.update({k: s for k, s in self._style().items() if s is not None})
            series.update({k: s for k, s in self._basis().items() if s is not None})
            series.update({k: s for k, s in self._rates_decomp().items() if s is not None})
        return series

    def _rates_decomp(self) -> dict[str, pd.Series | None]:
        """Rate-decomposition drivers (F6): real / breakeven / term-premium daily changes.

        A nominal move (the ``ir:DGS*`` factors) is treated identically whether it comes from real
        rates or inflation compensation, but for a long-duration tech book they are not equivalent
        (FINDING 6): a real-rate-driven move sinks tech *and* long bonds together (2022), a
        breakeven-driven one need not. These three DRIVER-ONLY factors let a scenario assert *which*
        kind of move it is; Σ then propagates the co-move. Decimal daily changes (percent / 100) to
        match the ``ir:`` rate-change convention. Absent files -> omitted, so a run without the F6
        feeds simply drops the rate-decomposition drivers.
        """
        out: dict[str, pd.Series | None] = {}
        real = self._read("rates/fred_real_rates.csv")
        if real is not None:
            for col, key in (("DFII10", "real:DFII10"), ("T10YIE", "be:T10YIE")):
                if col in real.columns:
                    s = pd.to_numeric(real.set_index("date")[col], errors="coerce").dropna()
                    out[key] = s.diff().div(100.0).dropna()
        acm = self._read("rates/nyfed_acm_term_premium.csv")
        if acm is not None and "ACMTP10" in acm.columns:
            s = pd.to_numeric(acm.set_index("date")["ACMTP10"], errors="coerce").dropna()
            out["tp:ACMTP10"] = s.diff().div(100.0).dropna()
        return out

    def _style(self) -> dict[str, pd.Series | None]:
        """Ken French style factors as canonical driver keys (F4). Currently just ``style:MOM``.

        The French values are daily percent returns, converted to decimal to match the equity
        return convention so Σ's MOM-vs-equity covariance is unit-consistent. Absent file -> {} so
        a run without the factors feed simply omits the style driver.
        """
        df = self._read("factors/ff_factors.csv")
        if df is None or "mom" not in df.columns:
            return {}
        s = pd.to_numeric(df.set_index("date")["mom"], errors="coerce").dropna().div(100.0)
        return {"style:MOM": s}

    def _basis(self) -> dict[str, pd.Series | None]:
        """ETF-vs-basket dispersion basis as a driver-only factor (F3): ``basis:SMH_SEMI``.

        The book's trade #6 shorts SMH against the single-name semiconductor longs (#1-4:
        NVDA/TSM/AVGO/MU). Its entire risk is the *spread* between the two, yet Σ carries only the
        separate ``eq:*`` legs, which co-move so tightly that a name-level scenario nets the hedge
        to ~flat and understates the risk (FINDING 3, the clearest case). This factor is the daily
        ETF-minus-basket return; authored as a driver it propagates a dispersion (SMH and the semis
        pulling apart) that the individual equity legs then reprice. Driver-only: no bump transform.
        """
        df = self._read("equity/prices_daily.csv")
        if df is None or not {"ticker", "close"}.issubset(df.columns):
            return {}
        rets = {}
        for tkr in ("SMH", *_SEMI_BASKET):
            sub = df[df["ticker"] == tkr]
            if sub.empty:
                return {}                         # need every leg to form the basis
            rets[tkr] = pd.to_numeric(sub.set_index("date")["close"], errors="coerce").pct_change()
        basket = pd.concat([rets[t] for t in _SEMI_BASKET], axis=1).mean(axis=1)   # equal-weight
        basis = (rets["SMH"] - basket).dropna()
        return {"basis:SMH_SEMI": basis}

    def _read(self, rel: str, date_col: str = "date") -> pd.DataFrame | None:
        path = storage.join(self.md, rel)
        if not storage.exists(path):
            return None
        df = storage.read_csv(path)
        dc = date_col if date_col in df.columns else df.columns[0]
        df[dc] = pd.to_datetime(df[dc], errors="coerce")
        return df.dropna(subset=[dc]).sort_values(dc).rename(columns={dc: "date"})

    def _ir(self) -> dict[str, pd.Series]:
        df = self._read("rates/fred_rates.csv")
        out: dict[str, pd.Series] = {}
        if df is None:
            return out
        for tenor in _IR_TENOR_YEARS:
            if tenor in df.columns:
                s = pd.to_numeric(df.set_index("date")[tenor], errors="coerce").dropna()
                out[f"ir:{tenor}"] = s.diff().div(100.0).dropna()   # pct -> decimal rate change
        return out

    def _equity(self) -> dict[str, pd.Series]:
        df = self._read("equity/prices_daily.csv")
        out: dict[str, pd.Series] = {}
        if df is None:
            return out
        for tkr in _EQUITY_TICKERS:
            sub = df[df["ticker"] == tkr]
            if not sub.empty:
                s = pd.to_numeric(sub.set_index("date")["close"], errors="coerce").dropna()
                out[f"eq:{tkr}"] = s.pct_change().dropna()
        return out

    def _equity_vol(self) -> pd.Series | None:
        df = self._read("options/vol_indices/VIX_History.csv")
        if df is None or "close" not in df.columns:
            return None
        s = pd.to_numeric(df.set_index("date")["close"], errors="coerce").dropna()
        return s.diff().div(100.0).dropna()                         # vol points -> decimal IV

    def _equity_vol_term(self) -> pd.Series | None:
        """Term-slope (VIX3M - VIX) daily change, decimal vol (GAP 4)."""
        v3 = self._read("options/vol_indices/VIX3M_History.csv")
        v1 = self._read("options/vol_indices/VIX_History.csv")
        if v3 is None or v1 is None or "close" not in v3.columns or "close" not in v1.columns:
            return None
        s3 = pd.to_numeric(v3.set_index("date")["close"], errors="coerce").dropna()
        s1 = pd.to_numeric(v1.set_index("date")["close"], errors="coerce").dropna()
        spread = (s3 - s1.reindex(s3.index)).dropna()
        return spread.diff().div(100.0).dropna()                    # vol-pt slope -> decimal

    def _equity_vol_skew(self) -> pd.Series | None:
        """CBOE SKEW daily change x skew_vol_per_point -> put-skew tilt units (GAP 4).

        ``skew_vol_per_point`` (hist_var config, default 0.001) is the decimal-vol-per-unit-
        log-moneyness tilt induced by a 1-point SKEW move — a documented parametric coefficient
        (no historical option surfaces exist to calibrate it more tightly).
        """
        df = self._read("options/vol_indices/SKEW_History.csv")
        if df is None or "close" not in df.columns:
            return None
        s = pd.to_numeric(df.set_index("date")["close"], errors="coerce").dropna()
        scale = float(self.cfg.get("skew_vol_per_point", 0.001))
        return s.diff().mul(scale).dropna()

    def _rates_vol(self) -> pd.Series | None:
        df = self._read("stress/move_index.csv")
        if df is None or "close" not in df.columns:
            return None
        s = pd.to_numeric(df.set_index("date")["close"], errors="coerce").dropna()
        return s.pct_change().dropna()

    def _fx(self) -> dict[str, pd.Series]:
        out: dict[str, pd.Series] = {}
        for ccy, rel, col in [("JPY", "fx/ecb_fx_rates.csv", "usdjpy"),
                              ("TWD", "fx/fed_h10_fx_rates.csv", "usdtwd")]:
            df = self._read(rel)
            if df is not None and col in df.columns:
                s = pd.to_numeric(df.set_index("date")[col], errors="coerce").dropna()
                out[f"fx:{ccy}"] = s.pct_change().dropna()
        return out

    def _foreign_rate(self) -> dict[str, pd.Series]:
        """Foreign discount-rate daily change (decimal, additive) — FX rho factor (GAP 5).

        JPY from BoJ TONAR (the only free daily foreign-rate series); TWD has no free daily
        curve so it is held constant (no factor) and the USD XCCY basis has no free source.
        """
        out: dict[str, pd.Series] = {}
        df = self._read("fx/boj_tonar.csv")
        if df is not None and "tonar_rate" in df.columns:
            s = pd.to_numeric(df.set_index("date")["tonar_rate"], errors="coerce").dropna()
            out["fxrate:JPY"] = s.diff().div(100.0).dropna()        # pct -> decimal rate change
        return out

    def _credit(self) -> pd.Series | None:
        """Daily decimal additive spread change, backfilled with a long-history proxy.

        Prefer the clean FRED ICE-BofA IG OAS series (``stress/fred_stress_macro.csv``,
        ``BAMLC0A0CM``); fall back to the noisy DTCC CDX daily median only if the OAS is
        unavailable (GAP 1 in the risk data-gap analysis).

        FRED serves the ICE-licensed OAS only ~3y back, which caps the aligned VaR window at
        the credit factor. When a long-history free proxy is configured (``credit_backfill_column``,
        default ``BAA10Y`` — Moody's Baa-minus-10Y, daily since 1986, not ICE-licensed) its
        vol-matched moves extend the credit series to the earlier dates, unlocking the ~2y of
        history the other factors already have (Tier 1.1).
        """
        oas = self._credit_oas()
        primary = oas if oas is not None and not oas.empty else self._credit_dtcc()
        if primary is None or primary.empty:
            return primary
        return splice_backfill(primary, self._credit_backfill(), self.cfg.get("credit_backfill_beta"))

    def _issuer_credit(self, proxy: pd.Series | None) -> dict[str, pd.Series]:
        """Per-issuer credit factors ``credit:<ISSUER>`` from the DTCC single-name 5Y prints.

        For each issuer in ``issuer_credit.issuers``: the price-forming prints (``NEWT``/``TRAD``)
        matching its ``credit.references`` name filters give a daily 5Y-bucket spread
        (``issuer_daily_spreads``) and its moves on the ``credit`` factor's calendar
        (``issuer_spread_moves``). Calendar dates the bounded fill leaves without a move inside or after
        the issuer's span get 0 (spread unchanged), so one issuer's sparse prints never cut the aligned
        panel. Before the DTCC history the series is the ``credit`` moves scaled by the issuer-to-IG level
        ratio at the splice (``proportional_backfill``). Provenance goes to ``issuer_credit_info``.
        """
        icfg = self.cfg.get("issuer_credit") or {}
        issuers = [str(i).upper() for i in icfg.get("issuers", [])]
        if not issuers or proxy is None or proxy.empty:
            return {}
        df = self._read("credit/dtcc_single_name_cds.csv")
        levels = self._credit_levels()
        if df is None or levels is None or not {"Underlying Asset Name", "Spread-Leg 1",
                                                "Expiration Date"}.issubset(df.columns):
            return {}
        if "Action type" in df.columns:
            df = df[df["Action type"].astype(str) == "NEWT"]
        if "Event type" in df.columns:
            df = df[df["Event type"].astype(str) == "TRAD"]
        names_u = df["Underlying Asset Name"].fillna("").astype(str).str.upper()
        calendar = list(proxy.sort_index().index)
        out: dict[str, pd.Series] = {}
        for issuer in issuers:
            ref = self.credit_refs.get(issuer, {})
            names = [str(n).upper() for n in ref.get("names", [])]
            if not names:
                continue
            mask = names_u.apply(lambda n, names=names: any(x in n for x in names))
            for x in (str(e).upper() for e in ref.get("exclude", [])):
                mask &= ~names_u.str.contains(x, regex=False)
            sub = df[mask]
            prints = pd.DataFrame({
                "date": sub["date"],
                "spread": pd.to_numeric(sub["Spread-Leg 1"], errors="coerce"),
                "tenor_years": (pd.to_datetime(sub["Expiration Date"], errors="coerce") - sub["date"]).dt.days / 365.25,
            }).dropna()
            daily = issuer_daily_spreads(
                prints, min_tenor_years=float(icfg.get("min_tenor_years", 4.0)),
                max_tenor_years=float(icfg.get("max_tenor_years", 6.0)),
                outlier_ratio=float(icfg.get("outlier_ratio", 2.0)),
                outlier_lookback=int(icfg.get("outlier_lookback", 10)))
            if daily.empty:
                continue
            start = daily.index.min()
            moves = issuer_spread_moves(daily, calendar, max_gap_days=int(icfg.get("max_gap_days", 60)))
            span = pd.DatetimeIndex([d for d in calendar if d > start])
            moves = moves.reindex(span).fillna(0.0)
            proxy_level = levels[levels.index <= start]
            if proxy_level.empty:
                continue
            issuer_level, ig_level = float(daily.iloc[0]), float(proxy_level.iloc[-1])
            out[f"{_ISSUER_CREDIT_PREFIX}{issuer}"] = proportional_backfill(
                moves, proxy, issuer_level=issuer_level, proxy_level=ig_level)
            self.issuer_credit_info[issuer] = {
                "dtcc_start": str(start.date()), "dtcc_end": str(daily.index.max().date()),
                "n_daily": len(daily), "issuer_level": issuer_level, "proxy_level": ig_level,
                "beta": issuer_level / ig_level, "backfilled_before": str(start.date())}
        return out

    def _credit_levels(self) -> pd.Series | None:
        """IG OAS level (decimal) from ``credit_file`` / ``credit_column``: the issuer backfill's proxy level."""
        rel = str(self.cfg.get("credit_file", "stress/fred_stress_macro.csv"))
        col = str(self.cfg.get("credit_column", "BAMLC0A0CM"))
        df = self._read(rel)
        if df is None or col not in df.columns:
            return None
        return pd.to_numeric(df.set_index("date")[col], errors="coerce").dropna().div(100.0)

    def _credit_backfill(self) -> pd.Series | None:
        """Long-history credit-spread proxy daily change (decimal, additive).

        Reads ``credit_backfill_column`` (default BAA10Y) from ``credit_file``; absent until the
        stress market-data pull fetches it, in which case the splice is a no-op (current behaviour).
        """
        col = str(self.cfg.get("credit_backfill_column", "BAA10Y"))
        rel = str(self.cfg.get("credit_file", "stress/fred_stress_macro.csv"))
        df = self._read(rel)
        if df is None or col not in df.columns:
            return None
        s = pd.to_numeric(df.set_index("date")[col], errors="coerce").dropna()
        return s.diff().div(100.0).dropna()                        # pct spread -> decimal change

    def _credit_oas(self) -> pd.Series | None:
        rel = str(self.cfg.get("credit_file", "stress/fred_stress_macro.csv"))
        col = str(self.cfg.get("credit_column", "BAMLC0A0CM"))
        df = self._read(rel)
        if df is None or col not in df.columns:
            return None
        s = pd.to_numeric(df.set_index("date")[col], errors="coerce").dropna()
        return s.diff().div(100.0).dropna()                        # OAS pct -> decimal change

    def _credit_dtcc(self) -> pd.Series | None:
        df = self._read("credit/dtcc_index_cds.csv")
        if df is None or "Spread-Leg 1" not in df.columns:
            return None
        df = df.assign(sp=pd.to_numeric(df["Spread-Leg 1"], errors="coerce")).dropna(subset=["sp"])
        daily = df.groupby(df["date"].dt.normalize())["sp"].median()
        clean = daily[(daily > 1e-4) & (daily < 0.30)]              # drop obvious bad prints
        moves = clean.diff().dropna()
        # The DTCC daily median mixes on-the-run series/tenors, so day-to-day medians
        # can jump unrealistically (a 56bp move in a ~50bp index). Cap to a documented
        # sane bound so one composition artifact can't dominate VaR. Used only as a
        # fallback now that the clean BofA OAS series is wired in (_credit_oas).
        cap = float(self.cfg.get("credit_max_abs_bp", 25.0)) / 1e4
        moves = moves.clip(-cap, cap)
        return moves if not moves.empty else None
