"""Value-at-Risk and Expected Shortfall (P2) — sensitivity-based historical simulation.

The book's sensitivities (DV01, equity delta, CS01, FX delta, vega) are mapped to historical
daily moves of a representative risk factor each, giving a hypothetical daily P&L vector over
the lookback window. Equity factors additionally carry a **second-order ½·Γ·move² gamma term**
so the measure is delta-gamma (it captures the options book's convexity, not just the linear
approximation — Tier 1.2), while remaining the fast sensitivity-based estimate that the heavier
full-revaluation VaR (historical_var.py) is the headline for. VaR is the loss quantile; Expected
Shortfall is the average loss beyond it (ES at 97.5% is the FRTB market-risk measure). Factors
are aligned by date so co-movements (and thus diversification) are captured, not assumed away.

Factor -> sensitivity mapping is config-driven (`config['risk']['var']['factors']`). Book
sensitivities are **keyed by exposure** so the model is genuinely multi-factor (GAP 7):
equity delta is attributed per underlier and FX delta per currency (by inspecting each
trade's resolved market data), so the config can map a per-name / per-currency history
series to the matching `EQUITY_DELTA:<ticker>` / `FX_DELTA:<ccy>` sensitivity:
  IR_<t>    per-tenor DGS daily change (bp) x IR_KR:<tenor>       (PV per +1bp key-rate)
  CREDIT    BofA IG OAS daily change (bp)  x CS01                 (PV per +1bp)
  RATES_VOL MOVE daily change (pts ~ bp)   x VEGA_RATES           (PV per +1bp vol)
  EQVOL_<n> per-name vol daily change (pts) x VEGA_EQUITY:<name>  (PV per +1 vol pt)
  EQVOL_TERM (VIX3M - VIX) daily change    x VEGA_EQ_TERM         (PV per +1 vol-pt slope)
  EQVOL_SKEW SKEW daily change x skew_scale x VEGA_EQ_SKEW         (PV per +1 skew tilt unit)
  EQ_<name> per-name daily return (%)      x EQUITY_DELTA:<name>  (PV per +1%)
  FX_<ccy>  per-pair daily return (%)      x FX_DELTA:<ccy>       (PV per +1%)

`maps_to` may be a **list** of sensitivity keys, in which case the factor's P&L column uses
their *summed* sensitivity. That is the representation for names that share one proxy series:
n names driven by the same move are `sum_i(sens_i x move) = (sum_i sens_i) x move`, so one
column is arithmetically identical to n collinear ones while keeping the factor count honest
(see `EQVOL_VIX` in `pricer_config.json`). Use it only where the series is genuinely the same
file+column — distinct series must stay distinct factors.

Credit uses the clean FRED ICE-BofA IG OAS (GAP 1). IR is now **key-rate** across the
DGS1MO..DGS30 grid (GAP 3) rather than a single parallel DGS10 point. All equity names use
their own spot history (GAP 2); vol uses a published per-name index where CBOE has one
(AAPL -> VXAPL, QQQ -> VXN) and a single shared VIX proxy factor for the names that have none
(GAP 4) — see the data-gap analysis.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd

from src.common import storage
from src.common.obs import metrics as obs_metrics

from ..input.common.market_data import EquityMarketData, EquitySpotData, FxData
from ..input.common.trade import Trade
from ..input.common.valuation import ValuationContext
from .factor_history import FactorHistory, splice_backfill
from .sensitivities import RiskEngine

logger = logging.getLogger("pricer.risk.var")

# An aligned_panel lookback longer than any factor history: keeps every aligned date.
_ALL_HISTORY = 1_000_000

# A configured factor that maps to a non-zero book sensitivity but has no usable history is
# silently dropped from VaR — which understates risk. Surface it so it can alert.
RISK_FACTOR_MISSING = obs_metrics.counter(
    "risk", "factor_missing_total", "VaR factors with exposure but missing data", ("factor",))


def _maps_to(fcfg: dict[str, Any]) -> list[str]:
    """Sensitivity keys a factor drives. Accepts a single key or a list (see module docstring)."""
    raw = fcfg.get("maps_to", "")
    if isinstance(raw, (list, tuple)):
        return [str(k) for k in raw if str(k)]
    return [str(raw)] if str(raw) else []


def _var_es(loss: np.ndarray, level: float) -> tuple[float, float]:
    """VaR (loss quantile) and ES (mean loss beyond it) from a loss array."""
    if loss.size == 0:
        return 0.0, 0.0
    var_q = float(np.quantile(loss, level))
    tail = loss[loss >= var_q]
    return var_q, (float(tail.mean()) if tail.size else var_q)


def _parametric(pnl: np.ndarray, levels: list[float]) -> dict[str, Any]:
    """Gaussian variance-covariance VaR/ES as a model-risk cross-check (Tier 3.7).

    var(book P&L) = s'.Cov(factor moves).s is exactly the variance of the aggregated P&L
    series, so sigma is that series' std. Under normality VaR = z_a.sigma and ES =
    sigma.phi(z_a)/(1-a) (zero-mean 1-day). Divergence from the empirical HS quantile is the
    signal: HS >> parametric means the book's tail is fatter than Gaussian (the usual case),
    so the parametric number is a floor, not a substitute.
    """
    if pnl.size < 2:
        return {}
    sigma = float(np.std(pnl, ddof=1))
    nd = NormalDist()
    out: dict[str, Any] = {"method": "variance_covariance_normal", "sigma": sigma,
                           "mean": float(np.mean(pnl)), "var": {}, "es": {}}
    for lvl in levels:
        z = nd.inv_cdf(lvl)
        out["var"][f"{lvl}"] = z * sigma
        out["es"][f"{lvl}"] = sigma * nd.pdf(z) / (1.0 - lvl)
    return out


def _ewma_scaled(r: pd.Series, lam: float) -> pd.Series:
    """Devolatize a move series by its RiskMetrics EWMA vol and re-inflate to the latest vol.

    z_t = r_t / sigma_t (EWMA, decay lambda) is the standardized residual; the FHS move is
    z_t * sigma_T with sigma_T the latest EWMA vol -- every historical day rescaled to today's
    volatility regime.
    """
    r = r.dropna()
    if len(r) < 3:
        return r
    sigma = r.ewm(alpha=1.0 - lam).std(bias=True).replace(0.0, np.nan).bfill()
    sigma_t = float(sigma.iloc[-1])
    if not np.isfinite(sigma_t) or sigma_t == 0.0:
        return r
    return (r / sigma * sigma_t).dropna()


def align_to_window(cols: Mapping[str, pd.Series], window: pd.DatetimeIndex, *, max_gap: int) -> pd.DataFrame:
    """Put per-factor P&L columns on a date window, filling short interior gaps with 0.0.

    Args:
        cols: Per-factor daily P&L contributions, each a date-indexed series.
        window: Strictly increasing dates to replay.
        max_gap: Longest interior run of missing window rows that is filled with 0.0.

    Returns:
        One column per entry of ``cols`` on the window rows that have no missing value left.

    Raises:
        ValueError: If ``max_gap`` is negative or ``window`` is not strictly increasing.
    """
    if max_gap < 0:
        raise ValueError(f"max_gap must be >= 0, got {max_gap}")
    idx = pd.DatetimeIndex(window)
    if not (idx.is_unique and idx.is_monotonic_increasing):
        raise ValueError("window must be strictly increasing")
    if not cols:
        return pd.DataFrame()
    frame = pd.DataFrame({name: s.reindex(idx) for name, s in cols.items()}, index=idx)
    return _fill_interior_gaps(frame, max_gap).dropna(how="any")


def _fill_interior_gaps(frame: pd.DataFrame, max_gap: int) -> pd.DataFrame:
    """Fill each column's interior runs of at most ``max_gap`` missing rows with 0.0.

    A run is interior when the column has a value on the row before and the row after it; leading
    and trailing runs, and longer runs, stay missing.
    """
    if max_gap == 0:
        return frame
    out = frame.copy()
    for name in out.columns:
        missing = out[name].isna()
        if not missing.any():
            continue
        run_id = (missing != missing.shift()).cumsum()           # one id per run of equal state
        run_len = missing.groupby(run_id).transform("size")
        observed = ~missing
        before = observed.cumsum() > 0                           # some value earlier in the column
        after = observed[::-1].cumsum()[::-1] > 0                # some value later in the column
        fill = missing & before & after & (run_len <= max_gap)
        out.loc[fill, name] = 0.0
    return out


@dataclass
class VarResult:
    valuation_date: str
    horizon_days: int
    lookback_days: int
    n_scenarios: int
    book_sensitivities: dict[str, float]
    var: dict[str, float] = field(default_factory=dict)   # "0.99" -> loss (positive)
    es: dict[str, float] = field(default_factory=dict)
    factor_stdev: dict[str, float] = field(default_factory=dict)
    missing_factors: list[str] = field(default_factory=list)  # exposure but no usable history
    pnl_vector: list[float] = field(default_factory=list)     # historical daily P&L (UI histogram)
    dates: list[str] = field(default_factory=list)            # ISO date per pnl_vector entry
    decomposition: dict[str, Any] = field(default_factory=dict)  # standalone/component by factor class
    # Filtered Historical Simulation — EWMA vol-scaled VaR/ES (Tier 2.4)
    fhs: dict[str, Any] = field(default_factory=dict)
    # Stressed VaR/ES over the worst historical sub-window (Basel 2.5, Tier 2.5)
    stressed: dict[str, Any] = field(default_factory=dict)
    # Parametric (variance-covariance, Gaussian) VaR/ES cross-check (Tier 3.7)
    parametric: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valuation_date": self.valuation_date,
            "horizon_days": self.horizon_days,
            "lookback_days": self.lookback_days,
            "n_scenarios": self.n_scenarios,
            "book_sensitivities": self.book_sensitivities,
            "var": self.var,
            "es": self.es,
            "factor_stdev": self.factor_stdev,
            "missing_factors": self.missing_factors,
            "pnl_vector": self.pnl_vector,
            "dates": self.dates,
            "decomposition": self.decomposition,
            "fhs": self.fhs,
            "stressed": self.stressed,
            "parametric": self.parametric,
        }


class VarEngine:
    def __init__(self, risk_engine: RiskEngine, config: dict[str, Any], project_root: Path):
        self.risk = risk_engine
        self.cfg = config.get("risk", {}).get("var", {})
        self.marketdata = storage.join(storage.data_base(project_root), "marketdata")
        # Issuer -> IG/HY tag (shared with the JTD add-on) so CS01 is keyed per rating bucket.
        self.credit_buckets: dict[str, str] = config.get("risk", {}).get("jtd", {}).get("buckets", {}) or {}
        # The sensitivity family replays the full-revaluation panel's dates (spec var-panel-window).
        self._history = FactorHistory(project_root, config)
        self._history_dates: pd.DatetimeIndex | None = None

    def _aligned_dates(self) -> pd.DatetimeIndex:
        """Every date of the full-revaluation factor panel (all aligned history), computed once."""
        if self._history_dates is None:
            panel = self._history.aligned_panel(lookback=_ALL_HISTORY)
            self._history_dates = pd.DatetimeIndex(panel.index)
        return self._history_dates

    def _panel(self, cols: Mapping[str, pd.Series], lookback: int | None) -> pd.DataFrame:
        """Columns on the full-reval panel's last ``lookback`` dates (``None``: every date)."""
        dates = self._aligned_dates()
        window = dates if lookback is None else dates[-lookback:]
        return align_to_window(cols, window, max_gap=self._history.panel_max_gap_days)

    def historical_var(self, trades: list[Trade], ctx: ValuationContext,
                       include_gamma: bool = True) -> VarResult:
        """Sensitivity-based historical VaR/ES. ``include_gamma=False`` drops the second-order
        equity term for a purely first-order (linear) VaR — used where an additive per-trade
        decomposition must reconcile to the book (e.g. the sizing trade-risk model)."""
        book = self._book_sensitivities(trades, ctx)
        horizon = int(self.cfg.get("horizon_days", 1))
        lookback = int(self.cfg.get("lookback_days", 250))
        levels = [float(x) for x in self.cfg.get("confidence_levels", [0.95, 0.975, 0.99])]

        # Per-factor daily P&L-unit moves, date-indexed (incl. the equity delta-gamma term).
        cols, missing = self._factor_pnl_columns(book, include_gamma=include_gamma)
        for fname in missing:                  # has exposure but no data -> understates VaR
            RISK_FACTOR_MISSING.labels(factor=fname).inc()
            logger.warning("VaR: factor %r has book exposure but no usable history "
                           "(it is dropped — VaR is understated).", fname)

        result = VarResult(ctx.valuation_date.isoformat(), horizon, lookback,
                           0, book)
        result.missing_factors = missing
        if not cols:
            logger.warning("VaR: no usable risk factors mapped to book sensitivities.")
            return result

        full_panel = self._panel(cols, None)
        panel = self._panel(cols, lookback)
        if panel.empty:
            return result
        scale = float(np.sqrt(horizon))                         # sqrt-time horizon scaling
        pnl = panel.sum(axis=1).to_numpy() * scale

        result.n_scenarios = int(pnl.size)
        result.pnl_vector = [float(x) for x in pnl]   # horizon-scaled daily P&L for the UI histogram
        result.dates = [d.date().isoformat() for d in panel.index]
        result.factor_stdev = {f: float(panel[f].std(ddof=1)) for f in panel.columns}
        for lvl in levels:
            var_q, es_q = _var_es(-pnl, lvl)
            result.var[f"{lvl}"] = var_q
            result.es[f"{lvl}"] = es_q
        result.decomposition = self._decompose(panel, levels, horizon)
        result.fhs = self._filtered_hs(book, levels, horizon)
        result.stressed = self._stressed(full_panel, levels, horizon)
        result.parametric = _parametric(pnl, levels)
        return result

    # -- Filtered Historical Simulation (Tier 2.4) --------------------------- #
    def _filtered_hs(self, book: dict[str, float], levels: list[float], horizon: int) -> dict[str, Any]:
        """EWMA-filtered (vol-scaled) historical simulation.

        Equal-weighted HS treats a shock from a calm year as equally likely today; over a short
        window that under-reacts to the current regime. FHS (Barone-Adesi/Hull-White) devolatizes
        each factor's moves by its RiskMetrics EWMA vol and re-inflates by the *latest* vol, so the
        simulated day co-moves are drawn from standardized residuals scaled to today's volatility —
        the principled way to get regime-responsive VaR from a short history.
        """
        fcfg = self.cfg.get("fhs", {}) or {}
        if not fcfg.get("enabled", True):
            return {}
        lam = float(fcfg.get("lambda", 0.94))
        lookback = int(self.cfg.get("lookback_days", 250))
        cols, _ = self._factor_pnl_columns(book, fhs=True, lam=lam)
        if not cols:
            return {}
        panel = self._panel(cols, lookback)
        if panel.empty:
            return {}
        pnl = panel.sum(axis=1).to_numpy() * float(np.sqrt(horizon))
        out: dict[str, Any] = {"lambda": lam, "n_scenarios": int(pnl.size),
                               "var": {}, "es": {}, "pnl_vector": [float(x) for x in pnl]}
        for lvl in levels:
            var_q, es_q = _var_es(-pnl, lvl)
            out["var"][f"{lvl}"] = var_q
            out["es"][f"{lvl}"] = es_q
        return out

    # -- Stressed VaR (Tier 2.5) --------------------------------------------- #
    def _stressed(self, full_panel: pd.DataFrame, levels: list[float], horizon: int) -> dict[str, Any]:
        """VaR/ES calibrated to the most stressful sub-window in the available history (Basel 2.5).

        Our aligned window is post-2022 and contains no full crisis, so equal-weighted VaR is
        anchored to a benign regime. We locate the worst contiguous ``stress_window`` days (by ES
        at the top confidence level) and report VaR/ES there, plus the window dates — a defensible
        stressed measure from the data we have (upgrades automatically once the credit backfill and
        longer factor histories extend the window through 2020/2022).
        """
        scfg = self.cfg.get("stressed", {}) or {}
        if not scfg.get("enabled", True):
            return {}
        win = int(scfg.get("stress_window", 250))
        scale = float(np.sqrt(horizon))
        total = full_panel.sum(axis=1) * scale
        if len(total) < win:
            win = len(total)
        if win < 20:
            return {}
        loss = -total.to_numpy()
        top = max(levels)
        # Slide a window of `win` days; pick the one whose ES at the top level is worst.
        best_i, best_es = 0, -np.inf
        for i in range(len(loss) - win + 1):
            _, es_i = _var_es(loss[i:i + win], top)
            if es_i > best_es:
                best_es, best_i = es_i, i
        seg = loss[best_i:best_i + win]
        dates = total.index[best_i:best_i + win]
        out: dict[str, Any] = {
            "window_days": int(win),
            "window_start": str(pd.Timestamp(dates[0]).date()),
            "window_end": str(pd.Timestamp(dates[-1]).date()),
            "var": {}, "es": {},
        }
        for lvl in levels:
            var_q, es_q = _var_es(seg, lvl)
            out["var"][f"{lvl}"] = var_q
            out["es"][f"{lvl}"] = es_q
        return out

    # -- VaR decomposition (Tier 1.3) ---------------------------------------- #
    def _decompose(self, panel: pd.DataFrame, levels: list[float], horizon: int) -> dict[str, Any]:
        """Standalone-vs-diversified VaR/ES by risk-factor class, plus Euler component ES.

        Groups the per-factor P&L columns into factor classes (rates, credit, equity,
        equity_vol, rates_vol, fx). For each class it reports (i) the **standalone** VaR/ES —
        the loss quantile of that class alone, as if the rest of the book were flat — and
        (ii) the **component ES** — the class's average loss on the days the *whole book* is in
        its ES tail. Component ES is the Euler/expected-shortfall allocation: the class figures
        sum exactly to the diversified book ES, so it answers "how much of our tail loss is this
        class actually driving?". Sum(standalone VaR) - diversified VaR is the diversification
        benefit the book earns from imperfect factor co-movement.
        """
        classes = self._column_classes(list(panel.columns))
        class_names = sorted(set(classes.values()))
        scale = float(np.sqrt(horizon))
        total_loss = -panel.sum(axis=1).to_numpy() * scale
        class_loss = {
            c: -panel[[col for col in panel.columns if classes[col] == c]].sum(axis=1).to_numpy() * scale
            for c in class_names
        }
        by_level: dict[str, Any] = {}
        for lvl in levels:
            var_q = float(np.quantile(total_loss, lvl))
            tail = total_loss >= var_q
            div_es = float(total_loss[tail].mean()) if tail.any() else var_q
            comps: list[dict[str, Any]] = []
            sum_standalone_var = 0.0
            for c in class_names:
                cl = class_loss[c]
                s_var = float(np.quantile(cl, lvl))
                c_tail = cl >= s_var
                comps.append({
                    "class": c,
                    "standalone_var": s_var,
                    "standalone_es": float(cl[c_tail].mean()) if c_tail.any() else s_var,
                    "component_es": float(cl[tail].mean()) if tail.any() else 0.0,  # Euler, sums to book ES
                })
                sum_standalone_var += s_var
            for comp in comps:
                comp["es_share"] = float(comp["component_es"]) / div_es if div_es else 0.0
            comps.sort(key=lambda d: float(d["component_es"]), reverse=True)
            by_level[f"{lvl}"] = {
                "diversified_var": var_q,
                "diversified_es": div_es,
                "sum_standalone_var": sum_standalone_var,
                "diversification_benefit": sum_standalone_var - var_q,
                "components": comps,
            }
        return {"classes": class_names, "by_level": by_level}

    @staticmethod
    def _class_of(maps_to: str) -> str:
        if maps_to.startswith("IR_"):
            return "rates"
        if maps_to.startswith("CS01"):     # CS01, CS01:IG, CS01:HY
            return "credit"
        if maps_to.startswith("EQUITY_"):
            return "equity"
        if maps_to.startswith("VEGA_EQ"):
            return "equity_vol"
        if maps_to.startswith("VEGA_RATES"):
            return "rates_vol"
        if maps_to.startswith("FX_"):
            return "fx"
        return "other"

    def _column_classes(self, cols: list[str]) -> dict[str, str]:
        """Map each P&L column (incl. the ``::gamma`` second-order equity terms) to its factor class."""
        factors = self.cfg.get("factors", {})
        out: dict[str, str] = {}
        for col in cols:
            base = col[:-7] if col.endswith("::gamma") else col
            classes = {self._class_of(k) for k in _maps_to(factors.get(base, {}))}
            # A multi-key factor is only meaningful when its keys share a class (they always do
            # today — same-proxy vega names); a mixed one has no single class to report under.
            out[col] = classes.pop() if len(classes) == 1 else "other"
        return out

    def pnl_series(self, trades: list[Trade], ctx: ValuationContext,
                   lookback: int) -> pd.Series:
        """Date-indexed daily hypothetical book P&L (sum of sensitivity x factor move), unscaled.

        The historical-simulation P&L the VaR quantile is taken over — exposed so the
        backtester can roll a VaR estimate over it and count exceptions.
        """
        book = self._book_sensitivities(trades, ctx)
        cols, _ = self._factor_pnl_columns(book)
        if not cols:
            return pd.Series(dtype=float)
        panel = pd.concat(cols, axis=1).dropna(how="any").tail(lookback)
        return panel.sum(axis=1)

    def _factor_pnl_columns(self, book: dict[str, float], fhs: bool = False,
                            lam: float = 0.94, include_gamma: bool = True,
                            ) -> tuple[dict[str, pd.Series], list[str]]:
        """Date-indexed daily P&L contribution per risk factor, plus factors with no usable
        history. Linear term is (book sensitivity x factor move); equity factors additionally
        carry a **second-order gamma** term 0.5*Gamma*move^2 so the historical-sim VaR is
        delta-gamma (captures the options book's convexity), not purely first-order (Tier 1.2).

        With ``fhs=True`` each factor's moves are EWMA vol-scaled (Filtered HS, Tier 2.4) before
        being turned into P&L, so the gamma term convexity scales with today's vol regime too."""
        cols: dict[str, pd.Series] = {}
        missing: list[str] = []
        for fname, fcfg in self.cfg.get("factors", {}).items():
            targets = _maps_to(fcfg)
            # Summed across targets: keys sharing one factor share its move, so the aggregate
            # sensitivity is what multiplies it (identical P&L to one column per key).
            sens = sum(book.get(k, 0.0) for k in targets)
            if sens == 0.0:
                continue
            moves = self._factor_moves(fname, fcfg)
            if moves is None or moves.empty:
                missing.append(fname)
                continue
            if fhs:
                moves = _ewma_scaled(moves, lam)
            cols[fname] = moves * sens            # first-order P&L contribution
            if include_gamma:
                gamma = sum(book.get(f"EQUITY_GAMMA:{k.split(':', 1)[1]}", 0.0)
                            for k in targets if k.startswith("EQUITY_DELTA:"))
                if gamma:                          # 0.5*Gamma*(move)^2 convexity, same move units as delta
                    cols[f"{fname}::gamma"] = 0.5 * gamma * moves.pow(2)
        return cols, missing

    # -- internals ----------------------------------------------------------- #
    def _book_sensitivities(self, trades: list[Trade], ctx: ValuationContext) -> dict[str, float]:
        """Aggregate book sensitivities, keying equity delta by underlier and FX delta by
        currency so the VaR is a true multi-factor model (GAP 7)."""
        agg: dict[str, float] = {}
        for trade in trades:
            try:
                rr = self.risk.sensitivities(trade, ctx, key_rates=True)
            except Exception:  # a trade that won't price shouldn't sink the whole VaR
                continue
            eq_key, fx_key = self._exposure_keys(trade, ctx)
            credit_key = self._credit_key(trade)
            for f, v in rr.by_factor().items():
                if f in ("EQUITY_DELTA", "EQUITY_GAMMA", "VEGA_EQUITY") and eq_key:
                    key = f"{f}:{eq_key}"          # per-underlier delta + gamma + vega
                elif f in ("FX_DELTA", "FX_RATE") and fx_key:
                    key = f"{f}:{fx_key}"          # per-currency spot + rho
                elif f == "CS01" and credit_key:
                    key = f"CS01:{credit_key}"     # per rating bucket (IG / HY), Tier 3.9 full split
                else:
                    key = f
                agg[key] = agg.get(key, 0.0) + v
        return agg

    def _credit_key(self, trade: Trade) -> str | None:
        """IG/HY bucket for a credit-exposed trade (bond issuer / CDS reference) from
        ``risk.jtd.buckets`` — HY only when explicitly tagged HY, IG (incl. index) otherwise."""
        ref = str(getattr(trade, "issuer", "") or getattr(trade, "reference", "") or "")
        if not ref:
            return None
        return "HY" if self.credit_buckets.get(ref) == "HY" else "IG"

    def _exposure_keys(self, trade: Trade, ctx: ValuationContext) -> tuple[str | None, str | None]:
        """The trade's equity underlier and FX currency (for per-name/per-ccy attribution)."""
        try:
            mds = self.risk.engine.loader.resolve(trade.market_data_requirements(ctx), ctx)
        except Exception:
            return None, None
        eq: str | None = None
        fx: str | None = None
        for item in mds.items.values():
            if isinstance(item, EquitySpotData):
                eq = item.ticker
            elif isinstance(item, EquityMarketData):
                eq = item.underlier
            elif isinstance(item, FxData):
                fx = item.foreign_ccy
        return eq, fx

    def _load_level(self, file: str, column: str,
                    ticker_col: str | None = None, ticker: str | None = None,
                    warn: bool = True) -> pd.Series | None:
        path = storage.join(self.marketdata, file)
        if not storage.exists(path):
            if warn:
                logger.warning("VaR: file not found %s", path)
            return None
        df = storage.read_csv(path)
        date_col = next((c for c in df.columns if c.lower() in ("date", "as_of_date")), df.columns[0])
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        if ticker and ticker_col:
            df = df[df[ticker_col] == ticker]
        if column not in df.columns:
            if warn:
                logger.warning("VaR: column %s missing in %s", column, file)
            return None
        return (df.dropna(subset=[date_col]).sort_values(date_col)
                .set_index(date_col)[column].apply(pd.to_numeric, errors="coerce").dropna())

    def _factor_moves(self, name: str, fcfg: dict[str, Any]) -> pd.Series | None:
        s = self._load_level(str(fcfg.get("file", "")), str(fcfg.get("column", "")),
                             fcfg.get("ticker_col"), fcfg.get("ticker"))
        if s is None:
            return None
        # Optional derived spread (e.g. VIX3M - VIX for the term-structure factor, GAP 4).
        if fcfg.get("minus_file") and fcfg.get("minus_column"):
            s_minus = self._load_level(str(fcfg["minus_file"]), str(fcfg["minus_column"]))
            if s_minus is None:
                return None
            s = (s - s_minus.reindex(s.index)).dropna()

        moves = self._level_to_moves(s, fcfg)
        # Optional long-history proxy backfill (Tier 1.1): a factor whose primary history is
        # short (e.g. the ICE-licensed IG-OAS credit factor, ~3y) is extended backwards with a
        # free proxy's vol-matched moves so it stops throttling the aligned VaR window.
        bf_file, bf_col = fcfg.get("backfill_file"), fcfg.get("backfill_column")
        if bf_file and bf_col:
            bs = self._load_level(str(bf_file), str(bf_col), warn=False)  # absent until pulled
            if bs is not None and not bs.empty:
                moves = splice_backfill(moves, self._level_to_moves(bs, fcfg),
                                        fcfg.get("backfill_beta"))
        return moves

    @staticmethod
    def _level_to_moves(s: pd.Series, fcfg: dict[str, Any]) -> pd.Series:
        """Convert a level series to the daily move the factor's sensitivity is quoted against."""
        if fcfg.get("return_pct"):
            return s.pct_change().mul(100.0).dropna()            # % return
        if fcfg.get("move_unit_bp"):
            return s.diff().mul(100.0).dropna()                  # pct rate -> bp change
        proxy = float(fcfg.get("proxy_bp_per_move_pt", 1.0))
        return s.diff().mul(proxy).dropna()                       # level change -> unit proxy
