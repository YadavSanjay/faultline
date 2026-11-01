"""Factor covariance estimation (S0 — foundations).

Builds the covariance/correlation matrix Σ of daily factor moves from the aligned
factor-move panel (`FactorHistory.aligned_panel`). Σ is the engine of *correlation*:
the conditional-covariance propagation in `scenario_gen` uses it to derive coherent
co-moves of every factor from a few narrative-set drivers (design §5B).

Three window policies (design §4B):
  full     equal-weight sample covariance over the lookback window
  ewma     exponentially-weighted (RiskMetrics-style), recent days weighted more
  crisis   sample covariance over named crisis date windows only (fat-tailed co-moves)

Each estimate carries a content-addressed ``cov_id`` so a scenario number can be pinned
to the exact Σ that produced it (provenance discipline, design §9).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.pricer.risk.factor_history import FactorHistory

DEFAULT_EWMA_LAMBDA = 0.94


@dataclass(frozen=True)
class FactorCovariance:
    """An estimated factor covariance with reproducibility metadata.

    ``factors`` indexes the rows/cols of ``mean``/``cov``; values are in the canonical
    native units of ``factor_keys`` (decimal moves).
    """

    factors: tuple[str, ...]
    mean: np.ndarray   # (n,)   sample/weighted mean daily move per factor
    cov: np.ndarray    # (n, n) covariance of daily moves
    n_obs: int
    window: str        # "full" | "ewma:<lambda>" | "crisis"
    cov_id: str
    # F7 market-implied Σ regime that set the shrinkage (None when F7 is off -> fixed shrinkage).
    regime: dict[str, object] | None = None

    def index(self, name: str) -> int:
        try:
            return self.factors.index(name)
        except ValueError as exc:
            raise KeyError(f"unknown factor {name!r}; known: {self.factors}") from exc

    def correlation(self) -> np.ndarray:
        """Pearson correlation matrix derived from ``cov`` (zero-variance factors -> 0)."""
        sd = np.sqrt(np.clip(np.diag(self.cov), 0.0, None))
        denom = np.outer(sd, sd)
        with np.errstate(divide="ignore", invalid="ignore"):
            corr = np.where(denom > 0.0, self.cov / denom, 0.0)
        np.fill_diagonal(corr, 1.0)
        return corr

    def conditional_mean(self, drivers: Mapping[str, float]) -> dict[str, float]:
        """Gaussian conditional expectation of every factor given the ``drivers``.

        Implements (design §5B)::

            x_rest = mu_rest + Sigma(rest,drivers) . Sigma(drivers,drivers)^-1 . (d - mu_drivers)

        The driver factors are returned at their asserted values; all other factors are
        returned at their market-consistent conditional expectation, so the result is a
        complete, internally-consistent shock vector. ``Sigma(drivers,drivers)`` is
        inverted with a pseudo-inverse, so collinear drivers don't blow up.
        """
        if not drivers:
            raise ValueError("conditional_mean requires at least one driver")
        d_idx = [self.index(k) for k in drivers]
        r_idx = [i for i in range(len(self.factors)) if i not in set(d_idx)]

        d_vec = np.array([drivers[self.factors[i]] for i in d_idx], dtype=float)
        mu_d = self.mean[d_idx]
        sigma_dd = self.cov[np.ix_(d_idx, d_idx)]
        beta = np.linalg.pinv(sigma_dd)
        delta = d_vec - mu_d

        out: dict[str, float] = {self.factors[i]: float(drivers[self.factors[i]]) for i in d_idx}
        if r_idx:
            mu_r = self.mean[r_idx]
            sigma_rd = self.cov[np.ix_(r_idx, d_idx)]
            x_r = mu_r + sigma_rd @ (beta @ delta)
            for i, val in zip(r_idx, x_r, strict=True):
                out[self.factors[i]] = float(val)
        return out

    def as_dict(self) -> dict[str, object]:
        d: dict[str, object] = {"factors": list(self.factors), "n_obs": self.n_obs,
                                "window": self.window, "cov_id": self.cov_id}
        if self.regime is not None:
            d["regime"] = self.regime
        return d


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def _cov_id(factors: Sequence[str], window: str, mean: np.ndarray, cov: np.ndarray) -> str:
    h = hashlib.sha256()
    h.update("|".join(factors).encode("utf-8"))
    h.update(window.encode("utf-8"))
    h.update(np.ascontiguousarray(np.round(mean, 12)).tobytes())
    h.update(np.ascontiguousarray(np.round(cov, 12)).tobytes())
    return h.hexdigest()[:16]


def _weighted_moments(x: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Weighted mean and (reliability-unbiased) covariance for weights summing to 1."""
    mean = w @ x
    xc = x - mean
    denom = 1.0 - float(np.sum(w**2))           # unbiased estimator for normalized weights
    cov = (xc.T * w) @ xc / denom if denom > 0 else (xc.T * w) @ xc
    return mean, cov


def _shrink_to_diagonal(cov: np.ndarray, intensity: float) -> np.ndarray:
    """Ledoit-Wolf-style linear shrinkage of a sample covariance toward its diagonal.

    ``C_shrunk = (1-d).C + d.diag(C)`` scales every off-diagonal covariance by ``(1-d)`` while
    keeping variances, i.e. it shrinks correlations toward zero. A 31-factor sample covariance
    over a few hundred days has very noisy cross-asset correlations; used raw in the Gaussian
    conditional-mean propagation (with near-collinear rate drivers) they amplify into implausible
    cross-factor shocks (e.g. a +100bp rate move propagating to a +96% equity move). Shrinkage is
    the standard stabilizer — it keeps each factor's own volatility exact and damps the spurious
    co-moves that drive the blow-up.
    """
    if intensity <= 0.0:
        return cov
    d = float(min(1.0, intensity))
    diag = np.diag(np.diag(cov))
    return (1.0 - d) * cov + d * diag


def build_covariance(panel: pd.DataFrame, *, window: str = "full",
                     ewma_lambda: float = DEFAULT_EWMA_LAMBDA,
                     shrinkage: float = 0.0,
                     crisis_windows: Iterable[tuple[str, str]] | None = None,
                     regime: dict[str, object] | None = None) -> FactorCovariance:
    """Estimate Σ from an aligned factor-move panel.

    ``window`` is ``"full"`` (equal weight), ``"ewma"`` (recent-weighted by
    ``ewma_lambda``), or ``"crisis"`` (rows inside any ``crisis_windows`` date range).
    ``shrinkage`` (0..1) applies Ledoit-Wolf shrinkage toward the diagonal so the estimate is
    well-conditioned for the conditional-covariance propagation.
    """
    if panel.shape[1] < 1:
        raise ValueError("covariance needs at least one factor column")
    factors = tuple(str(c) for c in panel.columns)

    if window == "crisis":
        if not crisis_windows:
            raise ValueError("window='crisis' requires crisis_windows")
        idx = pd.to_datetime(panel.index)
        mask = np.zeros(len(panel), dtype=bool)
        for start, end in crisis_windows:
            mask |= (idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))
        sub = panel.loc[mask]
        if len(sub) < 2:
            raise ValueError("crisis windows select fewer than 2 aligned days")
        x = sub.to_numpy(dtype=float)
        mean = x.mean(axis=0)
        cov = np.cov(x, rowvar=False, ddof=1)
        label = "crisis"
    elif window == "ewma":
        x = panel.to_numpy(dtype=float)
        n = len(x)
        if n < 2:
            raise ValueError("ewma covariance needs at least 2 days")
        # most-recent day (last row) gets the largest weight
        w = ewma_lambda ** np.arange(n - 1, -1, -1, dtype=float)
        w /= w.sum()
        mean, cov = _weighted_moments(x, w)
        label = f"ewma:{ewma_lambda:g}"
    elif window == "full":
        x = panel.to_numpy(dtype=float)
        if len(x) < 2:
            raise ValueError("full covariance needs at least 2 days")
        mean = x.mean(axis=0)
        cov = np.cov(x, rowvar=False, ddof=1)
        label = "full"
    else:
        raise ValueError(f"unknown window {window!r}; expected full|ewma|crisis")

    cov = np.atleast_2d(cov)
    mean = np.atleast_1d(mean)
    if shrinkage > 0.0:
        cov = _shrink_to_diagonal(cov, shrinkage)
        label = f"{label}+shrink:{float(min(1.0, shrinkage)):g}"
    return FactorCovariance(factors=factors, mean=mean, cov=cov, n_obs=len(x),
                            window=label, cov_id=_cov_id(factors, label, mean, cov),
                            regime=regime)


def covariance_from_history(history: FactorHistory, *, lookback: int = 250,
                            window: str = "full",
                            ewma_lambda: float = DEFAULT_EWMA_LAMBDA,
                            shrinkage: float = 0.0,
                            crisis_windows: Iterable[tuple[str, str]] | None = None,
                            include_style: bool = False,
                            regime: dict[str, object] | None = None,
                            ) -> FactorCovariance:
    """Convenience: build Σ directly from a `FactorHistory` aligned panel.

    ``include_style`` adds the Ken French ``style:MOM`` driver (F4) so "momentum crash" is an
    authorable, covariance-propagated scenario. It is a scenariogen-only opt-in: the style series
    can lag the daily feeds, so the style-augmented inner join may end a little earlier than the
    base panel — which is why the full-reval VaR (unchanged) does not use it.

    ``shrinkage`` may be the market-implied value resolved by the F7 regime switch; ``regime`` is
    that regime's metadata, stamped into the covariance for provenance (None when F7 is off).
    """
    panel = history.aligned_panel(lookback, include_style=include_style)
    return build_covariance(panel, window=window, ewma_lambda=ewma_lambda,
                            shrinkage=shrinkage, crisis_windows=crisis_windows, regime=regime)


def crisis_covariance(panel: pd.DataFrame, windows: Sequence[tuple[str, str]], *, shrinkage: float,
                      min_days: int) -> tuple[FactorCovariance, dict[str, int]]:
    """Estimates the crisis Σ from the panel rows dated inside any of the crisis windows.

    Args:
        panel: Aligned daily factor-move frame indexed by date (one column per factor).
        windows: ``(start, end)`` ISO date pairs, both ends inclusive; a row inside two windows counts once.
        shrinkage: Shrinkage toward the diagonal in ``[0, 1]``: variances are kept and every off-diagonal
            entry is multiplied by ``1 - shrinkage``.
        min_days: Fewest rows the windows must select in total.

    Returns:
        ``(covariance, coverage)``: the ddof-1 sample covariance and mean of the selected rows (shrunk) as a
        ``FactorCovariance`` over the panel's columns with ``n_obs`` the number of rows used, and a map from
        each window, keyed ``"<start>..<end>"``, to its number of panel rows (0 for one the history doesn't
        reach).

    Raises:
        ValueError: Fewer than ``min_days`` rows in total, or ``shrinkage`` outside ``[0, 1]``.
    """
    if not 0.0 <= shrinkage <= 1.0:
        raise ValueError(f"crisis shrinkage must be in [0, 1], got {shrinkage}")
    dates = pd.DatetimeIndex(pd.to_datetime(pd.Index(panel.index).astype(str))).normalize()
    mask = np.zeros(len(panel), dtype=bool)
    coverage: dict[str, int] = {}
    for start, end in windows:
        in_window = np.asarray((dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end)))
        coverage[f"{start}..{end}"] = int(in_window.sum())
        mask |= in_window
    n = int(mask.sum())
    if n < max(int(min_days), 2):
        raise ValueError(f"crisis windows select {n} aligned day(s), fewer than min_days={min_days} "
                         f"(coverage {coverage})")
    x = panel.loc[mask].to_numpy(dtype=float)
    mean = x.mean(axis=0)
    cov = np.atleast_2d(np.cov(x, rowvar=False, ddof=1))
    if shrinkage > 0.0:
        cov = _shrink_to_diagonal(cov, shrinkage)
    factors = tuple(str(c) for c in panel.columns)
    label = f"crisis+shrink:{float(shrinkage):g}"
    return FactorCovariance(factors=factors, mean=np.atleast_1d(mean), cov=cov, n_obs=n, window=label,
                            cov_id=_cov_id(factors, label, mean, cov)), coverage
