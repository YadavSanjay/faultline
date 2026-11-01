"""Point-in-time event replay of the scenario set (``docs/specs/scenario-event-replay.md``).

Could the scenario set as it stood the day before a past episode have represented what happened?
Pure functions:

* ``load_event_replay_config`` / ``event_replay_settings`` validate ``configs/scenariogen/event_replay.json``;
* ``scenario_available`` drops scenarios whose own replay window ends on or after the as-of date;
* ``latest_filed_fiscal_years`` pins XbrlRag fiscal years to the annual reports filed by the as-of;
* ``nearest_scenario`` picks the scenario whose vector is closest (cosine) to the realised move;
* ``sleeve_direction_accuracy`` scores direction hits per book sleeve.

``event_replay_runner`` applies them.
"""

from __future__ import annotations

import calendar
import math
import re
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any, NamedTuple

from src.common.config.models import ConfigError

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_TOP_KEYS = frozenset({"description", "episodes", "min_abs_z", "sleeves"})
_EPISODE_KEYS = frozenset({"name", "as_of", "horizon_days"})


class Episode(NamedTuple):
    """One replayed episode: the scenario set is rebuilt as of the day before it.

    Fields, in order: ``name: str``, ``as_of: date``, ``horizon_days: int``.
    """

    name: str
    as_of: date
    horizon_days: int


def load_event_replay_config(raw: Mapping[str, Any]) -> list[Episode]:
    """Validate a parsed ``event_replay.json`` and return its episodes.

    Args:
        raw: The parsed JSON object. ``description`` and ``_``-prefixed keys are ignored.

    Returns:
        The episodes, in file order.

    Raises:
        ConfigError: For an unknown key, a missing or empty ``episodes``, a duplicate or empty name,
            a bad ``as_of`` date, a ``horizon_days`` that is not an integer >= 1, a ``min_abs_z``
            that is not a number > 0, or a non-string or empty sleeve entry.
    """
    return _validate(raw)[0]


def event_replay_settings(raw: Mapping[str, Any]) -> tuple[float, dict[str, str]]:
    """Return the scoring settings of a parsed ``event_replay.json``.

    Args:
        raw: The parsed JSON object, validated as by ``load_event_replay_config``.

    Returns:
        ``(min_abs_z, sleeves)``: the z-score threshold (default 1.0) and factor key -> sleeve name
        (default empty).

    Raises:
        ConfigError: For any input ``load_event_replay_config`` rejects.
    """
    _, min_abs_z, sleeves = _validate(raw)
    return min_abs_z, sleeves


def scenario_available(spec: Mapping[str, Any], as_of: date) -> bool:
    """Whether a scenario existed as of a date.

    Args:
        spec: The scenario spec; an optional ``replay`` block carries an ISO ``end`` date.
        as_of: The replay's as-of date.

    Returns:
        True without a ``replay`` block or when ``replay.end`` is strictly before ``as_of``.

    Raises:
        ValueError: For a ``replay`` block without a parsable ``end``.
    """
    replay = spec.get("replay")
    if replay is None:
        return True
    end = replay.get("end") if isinstance(replay, Mapping) else None
    try:
        end_date = _parse_iso(end)
    except ValueError as exc:
        raise ValueError(f"replay block without a parsable end: {end!r}") from exc
    return end_date < as_of


def latest_filed_fiscal_years(n: int, fye_month: int, fye_day: int, as_of: date, *,
                              filing_lag_days: int) -> list[int]:
    """The ``n`` most recent fiscal years whose annual report was filed by ``as_of``.

    Args:
        n: How many fiscal years to return.
        fye_month: Fiscal year-end month (1-12).
        fye_day: Fiscal year-end day (1-31), clamped to the month's last day.
        as_of: The replay's as-of date.
        filing_lag_days: Days from the fiscal year-end to the filing.

    Returns:
        Ascending fiscal years ``Y`` (ending in calendar year ``Y``) with end + lag <= ``as_of``;
        fewer than ``n`` when fewer qualify among the 50 years before ``as_of``.

    Raises:
        ValueError: For ``n < 1``, an out-of-range month or day, or a negative lag.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    if not 1 <= fye_month <= 12:
        raise ValueError(f"fye_month must be 1-12, got {fye_month}")
    if not 1 <= fye_day <= 31:
        raise ValueError(f"fye_day must be 1-31, got {fye_day}")
    if filing_lag_days < 0:
        raise ValueError(f"filing_lag_days must be >= 0, got {filing_lag_days}")
    found: list[int] = []
    for year in range(as_of.year, as_of.year - 51, -1):
        if year < 1:
            break
        end = date(year, fye_month, min(fye_day, calendar.monthrange(year, fye_month)[1]))
        if end + timedelta(days=filing_lag_days) <= as_of:
            found.append(year)
            if len(found) == n:
                break
    return sorted(found)


def nearest_scenario(scores: Mapping[str, Mapping[str, Any]]) -> str | None:
    """The scenario whose ``score_prediction`` cosine is highest.

    Args:
        scores: Scenario name -> a ``forward_log.score_prediction`` result.

    Returns:
        The name with the highest non-None ``cosine``, ties to the alphabetically first name;
        None when no scenario has a cosine.
    """
    best: str | None = None
    best_cos = -math.inf
    for name in sorted(scores):
        cos = scores[name].get("cosine")
        if cos is None:
            continue
        if float(cos) > best_cos:
            best, best_cos = name, float(cos)
    return best


def sleeve_direction_accuracy(predicted: Mapping[str, float], realised: Mapping[str, float],
                              scale: Mapping[str, float], sleeves: Mapping[str, str], *,
                              min_abs_z: float) -> dict[str, dict[str, float]]:
    """Direction hit rate per book sleeve.

    Args:
        predicted: Factor key -> predicted move.
        realised: Factor key -> realised move.
        scale: Factor key -> horizon volatility; only ``scale > 0`` counts.
        sleeves: Factor key -> sleeve name; factors without a sleeve are ignored.
        min_abs_z: Minimum ``|realised / scale|`` for a factor to count.

    Returns:
        Sleeve -> ``{"hits", "n", "rate"}``; a sleeve with no qualifying factor is absent.

    Raises:
        ValueError: For ``min_abs_z <= 0``.
    """
    if not min_abs_z > 0:
        raise ValueError(f"min_abs_z must be > 0, got {min_abs_z}")
    out: dict[str, dict[str, float]] = {}
    for factor, sleeve in sleeves.items():
        if factor not in predicted or factor not in realised or factor not in scale:
            continue
        sc = float(scale[factor])
        if not sc > 0:
            continue
        r = float(realised[factor])
        if abs(r / sc) < min_abs_z:
            continue
        p = float(predicted[factor])
        acc = out.setdefault(sleeve, {"hits": 0, "n": 0, "rate": 0.0})
        acc["n"] += 1
        if p != 0.0 and (p > 0) == (r > 0):
            acc["hits"] += 1
    for acc in out.values():
        acc["rate"] = acc["hits"] / acc["n"]
    return out


def _parse_iso(value: Any) -> date:
    if not isinstance(value, str) or not _ISO.fullmatch(value):
        raise ValueError(f"not an ISO date (YYYY-MM-DD): {value!r}")
    return date.fromisoformat(value)


def _unknown(keys: Any, allowed: frozenset[str]) -> list[str]:
    return sorted(str(k) for k in keys if not str(k).startswith("_") and k not in allowed)


def _validate(raw: Mapping[str, Any]) -> tuple[list[Episode], float, dict[str, str]]:
    """Validate the whole document once; ``ConfigError`` names the first problem class found."""
    if not isinstance(raw, Mapping):
        raise ConfigError("event_replay.json: the document must be an object")
    unknown = _unknown(raw, _TOP_KEYS)
    if unknown:
        raise ConfigError(f"event_replay.json: unknown key(s) {unknown}")
    eps = raw.get("episodes")
    if not isinstance(eps, list) or not eps:
        raise ConfigError("event_replay.json: 'episodes' must be a non-empty list of objects")
    episodes: list[Episode] = []
    seen: set[str] = set()
    for i, ep in enumerate(eps):
        if not isinstance(ep, Mapping):
            raise ConfigError(f"event_replay.json: episode {i} must be an object")
        bad = _unknown(ep, _EPISODE_KEYS)
        if bad:
            raise ConfigError(f"event_replay.json: episode {i} has unknown key(s) {bad}")
        missing = sorted(k for k in _EPISODE_KEYS if k not in ep)
        if missing:
            raise ConfigError(f"event_replay.json: episode {i} is missing {missing}")
        name = ep["name"]
        if not isinstance(name, str) or not name:
            raise ConfigError(f"event_replay.json: episode {i} needs a non-empty name, got {name!r}")
        if name in seen:
            raise ConfigError(f"event_replay.json: duplicate episode name {name!r}")
        seen.add(name)
        try:
            as_of = _parse_iso(ep["as_of"])
        except ValueError as exc:
            raise ConfigError(f"event_replay.json: episode {name!r} as_of: {exc}") from exc
        horizon = ep["horizon_days"]
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
            raise ConfigError(f"event_replay.json: episode {name!r} horizon_days must be an integer >= 1, "
                              f"got {horizon!r}")
        episodes.append(Episode(name, as_of, horizon))
    z = raw.get("min_abs_z", 1.0)
    if isinstance(z, bool) or not isinstance(z, int | float) or not math.isfinite(z) or z <= 0:
        raise ConfigError(f"event_replay.json: min_abs_z must be a number > 0, got {z!r}")
    sleeves_raw = raw.get("sleeves", {})
    if not isinstance(sleeves_raw, Mapping):
        raise ConfigError("event_replay.json: 'sleeves' must be an object (factor -> sleeve)")
    sleeves: dict[str, str] = {}
    for k, v in sleeves_raw.items():
        if not isinstance(k, str) or not k or not isinstance(v, str) or not v:
            raise ConfigError(f"event_replay.json: sleeve entry {k!r}: {v!r} must map a factor to a sleeve name")
        sleeves[k] = v
    return episodes, float(z), sleeves


def pit_issuer_drops(issuer_info: Mapping[str, Mapping[str, Any]], cutoff: date) -> set[str]:
    """Return the issuer credit factor keys a replay as of ``cutoff`` must drop.

    Args:
        issuer_info: ``FactorHistory.issuer_credit_info``: issuer -> provenance, with
            ``"dtcc_start"`` as an ISO date string.
        cutoff: The replay's point-in-time cutoff.

    Returns:
        The keys ``"credit:<ISSUER>"`` (issuer upper-cased) whose DTCC history starts after
        ``cutoff``, or whose ``dtcc_start`` is missing or empty. The input is not mutated.

    Raises:
        ValueError: For a ``dtcc_start`` that is not an ISO date.
    """
    drops: set[str] = set()
    for issuer, prov in issuer_info.items():
        start = prov.get("dtcc_start")
        # No PIT-clean history can be shown without a start -> drop; else drop when it starts later.
        if not start or _parse_iso(start) > cutoff:
            drops.add(f"credit:{str(issuer).upper()}")
    return drops
