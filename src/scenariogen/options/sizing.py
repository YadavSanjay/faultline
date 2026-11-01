"""Option-implied sizing helpers for scenariogen (``docs/specs/options-shock-sizing.md``).

Pure functions: point-in-time snapshot selection, risk-neutral and lognormal quantiles, option-implied
tiers and the option-tail check. ``OptionSignalProvider`` and the anchoring wiring call them.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import date
from itertools import pairwise
from pathlib import Path
from statistics import NormalDist
from typing import Any

from src.common.config.models import ConfigError
from src.scenariogen.config import config_dir

_N = NormalDist()


def _default_vol_index() -> dict[str, str]:
    return {"AAPL": "VXAPL", "AMZN": "VXAZN", "GOOGL": "VXGOG", "QQQ": "VXN", "SPX": "VIX"}


@dataclass(frozen=True)
class OptionSizingConfig:
    """``configs/scenariogen/options_sizing.json`` (a mistyped key raises ``ConfigError``).

    Attributes:
        max_staleness_days: Oldest usable option snapshot / vol-index close, in days before as_of.
        mild_q: Quantile for the mild tier of an option-anchored scenario.
        adverse_q: Quantile for its adverse tier (severe = adverse x severity.json severe_factor).
        check_q: Quantile of the option-tail severity check.
        check_max_horizon_days: Longest scenario horizon (trading days) the check applies to.
        vol_index: Ticker -> CBOE vol index used when the name has no qualifying chain snapshot.
    """

    max_staleness_days: int = 30
    mild_q: float = 0.05
    adverse_q: float = 0.01
    check_q: float = 0.01
    check_max_horizon_days: int = 21
    vol_index: Mapping[str, str] = field(default_factory=_default_vol_index)

    @classmethod
    def load(cls, root: Path | None = None) -> OptionSizingConfig:
        """Read and validate the config; defaults when the file is absent.

        Raises:
            ConfigError: An unknown key, or a quantile outside (0, 1) with ``adverse_q > mild_q``.
        """
        path = config_dir(root) / "options_sizing.json"
        if not path.exists():
            return cls()
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in raw.items() if k != "description" and not k.startswith("_")}
        unknown = set(values) - known
        if unknown:
            raise ConfigError(f"options_sizing.json: unknown key(s) {sorted(unknown)}; expected {sorted(known)}")
        cfg = cls(**values)
        for q in (cfg.mild_q, cfg.adverse_q, cfg.check_q):
            if not 0.0 < q < 1.0:
                raise ConfigError(f"options_sizing.json: quantiles must be in (0, 1), got {q}")
        if not cfg.adverse_q < cfg.mild_q:
            raise ConfigError("options_sizing.json: adverse_q must be below mild_q (a further tail)")
        if cfg.max_staleness_days < 0 or cfg.check_max_horizon_days < 1:
            raise ConfigError("options_sizing.json: max_staleness_days >= 0 and check_max_horizon_days >= 1")
        return cfg


def select_snapshot(dates: Sequence[str], as_of: date, *, max_staleness_days: int) -> str | None:
    """Pick the latest option snapshot date usable as of ``as_of``.

    Args:
        dates: Snapshot dates as ISO strings; only the first 10 characters are read.
        as_of: The run's as-of date. Dates after it never count.
        max_staleness_days: Largest allowed age in days (inclusive).

    Returns:
        The latest qualifying date as a 10-character string, or None when none qualifies.

    Raises:
        ValueError: If ``max_staleness_days`` is negative.
    """
    if max_staleness_days < 0:
        raise ValueError(f"max_staleness_days must be >= 0, got {max_staleness_days}")
    best: date | None = None
    for raw in dates:
        d = date.fromisoformat(raw[:10])
        if d <= as_of and (as_of - d).days <= max_staleness_days and (best is None or d > best):
            best = d
    return best.isoformat() if best is not None else None


def rnd_quantile(strikes: Sequence[float], cdf: Sequence[float], spot: float, q: float) -> float | None:
    """Return the risk-neutral ``q`` quantile as a return ``K_q / spot - 1``.

    Args:
        strikes: Strictly increasing strikes.
        cdf: Non-decreasing risk-neutral CDF at each strike.
        spot: Underlying spot, positive.
        q: Probability in (0, 1).

    Returns:
        The return at the linearly interpolated strike where the CDF equals ``q`` (first strike on a
        flat segment), or None when ``q`` lies outside ``[cdf[0], cdf[-1]]``.

    Raises:
        ValueError: For ``q`` outside (0, 1), non-positive spot, mismatched lengths, fewer than 2
            points, strikes not strictly increasing or a decreasing CDF.
    """
    if not 0.0 < q < 1.0:
        raise ValueError(f"q must be in (0, 1), got {q}")
    if not spot > 0:
        raise ValueError(f"spot must be positive, got {spot}")
    if len(strikes) != len(cdf):
        raise ValueError(f"strikes and cdf differ in length: {len(strikes)} vs {len(cdf)}")
    if len(strikes) < 2:
        raise ValueError("need at least 2 grid points")
    if any(b <= a for a, b in pairwise(strikes)):
        raise ValueError("strikes must be strictly increasing")
    if any(b < a for a, b in pairwise(cdf)):
        raise ValueError("cdf must be non-decreasing")
    if q < cdf[0] or q > cdf[-1]:
        return None
    i = next(j for j, c in enumerate(cdf) if c >= q)
    if cdf[i] == q:
        k = float(strikes[i])
    else:
        c0, c1 = float(cdf[i - 1]), float(cdf[i])
        k = float(strikes[i - 1]) + (float(strikes[i]) - float(strikes[i - 1])) * (q - c0) / (c1 - c0)
    return k / spot - 1.0


def lognormal_quantile(q: float, vol: float, t_years: float) -> float:
    """Return the ``q`` quantile of the return under the zero-rate risk-neutral lognormal.

    Args:
        q: Probability in (0, 1).
        vol: Annualised implied vol, positive and finite.
        t_years: Horizon in years, positive and finite.

    Returns:
        ``exp(-vol**2 * t / 2 + vol * sqrt(t) * PhiInv(q)) - 1``.

    Raises:
        ValueError: For invalid ``q``, ``vol`` or ``t_years``.
    """
    if not 0.0 < q < 1.0:
        raise ValueError(f"q must be in (0, 1), got {q}")
    _check_vol_t(vol, t_years)
    return math.exp(-vol * vol * t_years / 2.0 + vol * math.sqrt(t_years) * _N.inv_cdf(q)) - 1.0


def lognormal_tail_prob(move: float, vol: float, t_years: float) -> float:
    """Return the lognormal tail probability of a return at least as extreme as ``move``.

    Args:
        move: Return; ``P(R <= move)`` when negative, ``P(R >= move)`` when positive.
        vol: Annualised implied vol, positive and finite.
        t_years: Horizon in years, positive and finite.

    Returns:
        The tail probability.

    Raises:
        ValueError: For ``move == 0``, ``move <= -1`` or invalid ``vol`` / ``t_years``.
    """
    if move == 0 or move <= -1 or not math.isfinite(move):
        raise ValueError(f"move must be non-zero, finite and > -1, got {move}")
    _check_vol_t(vol, t_years)
    z = (math.log1p(move) + vol * vol * t_years / 2.0) / (vol * math.sqrt(t_years))
    return _N.cdf(z) if move < 0 else _N.cdf(-z)


def option_tiers(mild_move: float, adverse_move: float, *, severe_factor: float,
                 bound: float | None) -> dict[str, float]:
    """Build mild/adverse/severe tier moves from two option-implied quantile moves.

    Args:
        mild_move: Mild-tier move.
        adverse_move: Adverse-tier move, same sign and at least as large in magnitude.
        severe_factor: Multiplier (>= 1) from adverse to severe.
        bound: Positive magnitude cap on the severe move (sign kept), or None for no cap.

    Returns:
        ``{"mild": mild_move, "adverse": adverse_move, "severe": s}``.

    Raises:
        ValueError: For zero or opposite-sign moves, ``|adverse| < |mild|``, ``severe_factor < 1`` or
            ``bound <= 0``.
    """
    if mild_move == 0 or adverse_move == 0 or (mild_move > 0) != (adverse_move > 0):
        raise ValueError(f"mild and adverse moves must be non-zero with one sign, got {mild_move}, {adverse_move}")
    if abs(adverse_move) < abs(mild_move):
        raise ValueError(f"|adverse| {abs(adverse_move)} < |mild| {abs(mild_move)}")
    if not severe_factor >= 1:
        raise ValueError(f"severe_factor must be >= 1, got {severe_factor}")
    if bound is not None and not bound > 0:
        raise ValueError(f"bound must be positive, got {bound}")
    severe = adverse_move * severe_factor
    if bound is not None and abs(severe) > bound:
        severe = math.copysign(bound, severe)
    return {"mild": mild_move, "adverse": adverse_move, "severe": severe}


def exceeds_option_tail(tier_move: float, option_move: float | None) -> bool:
    """Return whether the option market's move is more extreme than the tier's, same direction.

    Args:
        tier_move: The tier's move.
        option_move: The option-implied move, or None when unavailable.

    Returns:
        True when both are non-zero, share a sign and ``|option_move| > |tier_move|``.
    """
    if option_move is None or tier_move == 0 or option_move == 0:
        return False
    return (tier_move > 0) == (option_move > 0) and abs(option_move) > abs(tier_move)


def _check_vol_t(vol: float, t_years: float) -> None:
    if not (math.isfinite(vol) and vol > 0):
        raise ValueError(f"vol must be positive and finite, got {vol}")
    if not (math.isfinite(t_years) and t_years > 0):
        raise ValueError(f"t_years must be positive and finite, got {t_years}")
