# Signal health: one status per scenario source (MacroRAG, XbrlRag, news, crowding, options, GPR,
# throughput) for a stress run, rolled up into an overall status.
#
# ``source_status`` turns what a run resolved for one source (its newest data date, how many of the
# expected items it covered, and the reasons it was held) into a status dict; ``signal_health`` rolls a
# list of those dicts up into an overall status, per-status counts and a worst-first source list.
#
# The stress runner collects one ``Observation`` per source from what the run already resolved (the
# ``*_observation`` helpers read the resolution objects by attribute only, so this module imports no
# provider) and ``build_signal_health`` turns them into the report's ``signal_health`` block.

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from src.common.config.models import ConfigError

from .config import config_dir
from .events import crowding_reliable

# Worst first: the roll-up's source order and the precedence for ``overall`` (``unused`` never counts).
STATUS_ORDER = ("down", "degraded", "ok", "unused")


def source_status(
    source: str,
    *,
    as_of: date,
    latest: date | None,
    max_staleness_days: int,
    covered: int,
    expected: int,
    holds: Sequence[str],
) -> dict[str, Any]:
    """Builds one source's signal-health status for a run.

    Args:
        source: The source name, e.g. ``"macrorag"`` or ``"options"``.
        as_of: The run's as-of date.
        latest: The newest date the source's data carries, or ``None`` when the source is not dated.
        max_staleness_days: The largest ``as_of - latest`` gap, in days, that is not stale.
        covered: How many of the expected items the source covered.
        expected: How many items the run expected from the source.
        holds: One reason per hold (fallback, stop or gate hold) the source caused.

    Returns:
        A dict with keys ``source``, ``latest`` (ISO string or ``None``), ``staleness_days``, ``stale``,
        ``covered``, ``expected``, ``coverage``, ``holds`` (reason -> count, in order of first
        appearance) and ``status`` (``"ok"``, ``"degraded"``, ``"down"`` or ``"unused"``).

    Raises:
        ValueError: If ``covered`` or ``expected`` is negative, ``covered > expected``,
            ``max_staleness_days`` is negative, or ``latest`` is after ``as_of``.
    """
    if covered < 0 or expected < 0:
        raise ValueError(f"{source}: covered ({covered}) and expected ({expected}) must be non-negative")
    if covered > expected:
        raise ValueError(f"{source}: covered ({covered}) exceeds expected ({expected})")
    if max_staleness_days < 0:
        raise ValueError(f"{source}: max_staleness_days ({max_staleness_days}) must be non-negative")
    if latest is not None and latest > as_of:
        raise ValueError(f"{source}: latest {latest.isoformat()} is after as_of {as_of.isoformat()}")

    staleness_days = None if latest is None else (as_of - latest).days
    stale = staleness_days is not None and staleness_days > max_staleness_days
    hold_counts: dict[str, int] = {}
    for reason in holds:
        hold_counts[reason] = hold_counts.get(reason, 0) + 1

    if expected == 0:
        status = "unused"
    elif covered == 0:
        status = "down"
    elif stale or covered < expected or hold_counts:
        status = "degraded"
    else:
        status = "ok"
    return {
        "source": source,
        "latest": None if latest is None else latest.isoformat(),
        "staleness_days": staleness_days,
        "stale": stale,
        "covered": covered,
        "expected": expected,
        "coverage": covered / expected if expected > 0 else None,
        "holds": hold_counts,
        "status": status,
    }


def signal_health(statuses: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Rolls per-source statuses up into a run's signal-health block.

    Args:
        statuses: Per-source status dicts as returned by ``source_status``.

    Returns:
        A dict with ``overall`` (the worst non-``"unused"`` status by ``down > degraded > ok``, or
        ``"unused"`` when there is none), ``counts`` (``ok``, ``degraded``, ``down`` and ``unused``, each
        always present) and ``sources`` (the statuses ordered ``down``, ``degraded``, ``ok``,
        ``unused``, then by source name).

    Raises:
        ValueError: If two statuses share the same ``source``.
    """
    seen: set[str] = set()
    for s in statuses:
        if s["source"] in seen:
            raise ValueError(f"duplicate signal-health source {s['source']!r}")
        seen.add(s["source"])

    counts = dict.fromkeys(STATUS_ORDER, 0)
    for s in statuses:
        counts[s["status"]] += 1
    used = [status for status in STATUS_ORDER if status != "unused" and counts[status]]
    ordered = sorted(statuses, key=lambda s: (STATUS_ORDER.index(s["status"]), s["source"]))
    return {
        "overall": used[0] if used else "unused",
        "counts": {status: counts[status] for status in ("ok", "degraded", "down", "unused")},
        "sources": [dict(s) for s in ordered],
    }


# Every source a stress run can draw on.
SOURCES = ("macrorag", "xbrlrag", "news", "crowding", "options", "gpr", "throughput")
# Value of the ``scenariogen_signal_status{source}`` gauge per status.
STATUS_GAUGE = {"ok": 0, "degraded": 1, "down": 2, "unused": -1}
# Hold reasons the collectors use when a source left nothing to read.
HOLD_NO_FEED = "no_feed"
HOLD_NOT_RESOLVED = "not_resolved"
HOLD_GATE_HELD = "gate_held"
HOLD_NOT_APPLIED = "not_applied"


@dataclass(frozen=True)
class Observation:
    """What a run resolved for one source: the inputs of ``source_status`` other than the limit."""

    latest: date | None = None
    covered: int = 0
    expected: int = 0
    holds: tuple[str, ...] = ()


@dataclass(frozen=True)
class SignalHealthConfig:
    """``configs/scenariogen/signal_health.json``: per-source staleness limits (``None`` = undated)."""

    max_staleness_days: dict[str, int | None] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SignalHealthConfig:
        """Validates the parsed file.

        Raises:
            ConfigError: An unknown top-level key, a missing or unknown source, or a limit that is not
                ``null`` or an integer >= 0.
        """
        unknown = {k for k in raw if not k.startswith(("_", "description"))} - {"max_staleness_days"}
        if unknown:
            raise ConfigError(f"signal_health.json: unknown key(s) {sorted(unknown)}")
        limits = raw.get("max_staleness_days")
        if not isinstance(limits, Mapping):
            raise ConfigError("signal_health.json: max_staleness_days must be an object")
        if set(limits) != set(SOURCES):
            raise ConfigError(
                f"signal_health.json: max_staleness_days keys {sorted(limits)} != {sorted(SOURCES)}")
        out: dict[str, int | None] = {}
        for source in SOURCES:
            v = limits[source]
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
                raise ConfigError(f"signal_health.json: {source} limit must be null or an integer >= 0, got {v!r}")
            out[source] = v
        return cls(out)

    @classmethod
    def load(cls, root: Path | None = None) -> SignalHealthConfig:
        """Reads and validates the committed file (``FileNotFoundError`` when it is missing)."""
        path = config_dir(root) / "signal_health.json"
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def build_signal_health(as_of: date, config: SignalHealthConfig,
                        observations: Mapping[str, Observation]) -> dict[str, Any]:
    """The run's signal-health block: one ``source_status`` per source in ``SOURCES``, rolled up.

    Args:
        as_of: The run's as-of date.
        config: The staleness limits; an undated source (limit ``None``) reports no date.
        observations: Per source what the run resolved; a missing source is unused.

    Returns:
        ``signal_health(...)`` over the seven statuses.

    Raises:
        ValueError: An observation for a source outside ``SOURCES``, or invalid counts or dates
            (``source_status``).
    """
    unknown = set(observations) - set(SOURCES)
    if unknown:
        raise ValueError(f"unknown signal-health source(s) {sorted(unknown)}")
    statuses = []
    for source in SOURCES:
        obs = observations.get(source, Observation())
        limit = config.max_staleness_days.get(source)
        statuses.append(source_status(
            source, as_of=as_of, latest=obs.latest if limit is not None else None,
            max_staleness_days=limit if limit is not None else 0,
            covered=obs.covered, expected=obs.expected, holds=obs.holds))
    return signal_health(statuses)


def tally(expected: Iterable[str], outcomes: Mapping[str, str | None], *,
          missing: str = HOLD_NOT_RESOLVED, latest: date | None = None) -> Observation:
    """An observation over named items, each covered or held with a reason.

    Args:
        expected: The items the run expected (a duplicate counts once).
        outcomes: Per item ``""`` when covered, else the hold reason; ``None`` or absent is held with
            reason ``missing``.
        missing: The hold reason of an item without an outcome.
        latest: The source's newest data date.

    Returns:
        ``covered`` = items with outcome ``""``; ``holds`` = one reason per other item, in first-seen order.
    """
    names = list(dict.fromkeys(expected))
    holds: list[str] = []
    covered = 0
    for name in names:
        outcome = outcomes.get(name)
        if outcome == "":
            covered += 1
        else:
            holds.append(outcome or missing)
    return Observation(latest=latest, covered=covered, expected=len(names), holds=tuple(holds))


def macro_observation(expected: Iterable[str], resolutions: Mapping[str, Any],
                      stopped: Mapping[str, str]) -> Observation:
    """MacroRAG: the scenarios expected to use macro legs vs those whose macro legs applied.

    Args:
        expected: Scenarios that needed MacroRAG (required in ``arag.json`` on the dual-RAG path; with a
            ``macro.json`` entry on the macro-only path) and reached generation.
        resolutions: Scenario -> its macro resolution (a ``DriverResolution`` or its ``to_dict()``).
        stopped: Scenario -> the reason the run stopped it, used when it has no resolution.

    Returns:
        Undated; holds are the fallback reasons.
    """
    names = list(dict.fromkeys(expected))
    outcomes: dict[str, str | None] = {}
    for name in names:
        res = resolutions.get(name)
        if res is None:
            outcomes[name] = stopped.get(name)
        else:
            reason = _field(res, "fallback_reason")
            outcomes[name] = "" if _field(res, "used_macro") else (str(reason) if reason else None)
    return tally(names, outcomes)


def xbrlrag_observation(configured: Mapping[str, int], resolutions: Mapping[str, Any],
                        stopped: Mapping[str, str]) -> Observation:
    """XbrlRag: company legs configured vs resolved for the scenarios that reached it.

    Args:
        configured: Scenario -> its configured company legs (``arag.json`` ``companies``), for the scenarios
            that reached the XbrlRag step (resolved, or stopped by it).
        resolutions: Scenario -> its ``AragResolution`` (``company_drivers`` read by attribute or key).
        stopped: Scenario -> stop reason, for the configured scenarios XbrlRag stopped.

    Returns:
        Undated; ``covered`` = resolved legs, ``expected`` = configured legs, holds = stop reasons.
    """
    covered = expected = 0
    holds: list[str] = []
    for name, n_legs in configured.items():
        if n_legs <= 0:
            continue
        expected += n_legs
        res = resolutions.get(name)
        if res is not None:
            covered += min(n_legs, len(_field(res, "company_drivers") or {}))
        else:
            holds.append(stopped.get(name) or HOLD_NOT_RESOLVED)
    return Observation(covered=covered, expected=expected, holds=tuple(holds))


def news_observation(resolution: Any | None) -> Observation:
    """News: the newest 8-K, configured tickers vs names with events in the window, the gate hold.

    Args:
        resolution: The run's ``NewsResolution`` (``gate`` and ``legs`` read); ``None`` (no feed, news off
            or not the dual-RAG path) is unused.
    """
    if resolution is None:
        return Observation()
    gate = resolution.gate
    n = len(resolution.legs)
    return Observation(latest=_iso_date(gate.newest_filing), covered=min(int(gate.n_names_with_events), n),
                       expected=n, holds=() if gate.ok else (HOLD_GATE_HELD,))


def crowding_observation(resolution: Any | None, as_of: date, *, min_obs: int | None = None,
                         max_staleness_days: int | None = None) -> Observation:
    """Crowding: the newest settlement date, names in the feed vs reliable names.

    Args:
        resolution: The run's ``CrowdingResolution`` (``history`` and ``legs`` read); ``None`` is unused.
        as_of: The run date.
        min_obs: ``events.json`` ``crowding_min_obs``; with ``max_staleness_days`` it decides a reliable
            name (``events.crowding_reliable``). ``None`` (no ``events.json``) counts the names with a leg.
        max_staleness_days: ``events.json`` ``crowding_max_staleness_days``.
    """
    if resolution is None:
        return Observation()
    history: Mapping[str, Sequence[str]] = resolution.history
    dates = [d for ds in history.values() for d in map(_iso_date, ds) if d is not None and d <= as_of]
    if min_obs is not None and max_staleness_days is not None:
        reliable = sum(1 for ds in history.values()
                       if crowding_reliable(ds, as_of, min_obs=min_obs, max_staleness_days=max_staleness_days))
    else:
        reliable = sum(1 for t in history if t in resolution.legs)
    return Observation(latest=max(dates) if dates else None, covered=reliable, expected=len(history))


def options_observation(provider: Any | None, underliers: Collection[str]) -> Observation:
    """Options: the newest PIT snapshot or index date, book option underliers vs names with PIT data.

    Args:
        provider: The run's ``OptionSignalProvider`` (``names()`` and ``data_date()`` read); ``None`` means
            no option data at or before as_of.
        underliers: The book's option underliers.
    """
    wanted = sorted({u.upper() for u in underliers})
    if provider is None:
        return Observation(expected=len(wanted), holds=(HOLD_NO_FEED,) if wanted else ())
    names = provider.names()
    have = {n.upper() for n in names}
    dates = [d for n in names if (d := _iso_date(provider.data_date(n))) is not None]
    return Observation(latest=max(dates) if dates else None, covered=sum(1 for u in wanted if u in have),
                       expected=len(wanted))


def state_observation(tagged: Iterable[str], resolutions: Mapping[str, Any], *,
                      date_attr: str | None = None) -> Observation:
    """GPR or throughput: tagged scenarios vs those whose multiplier applied.

    Args:
        tagged: The scenarios tagged for the source that reached generation.
        resolutions: Scenario -> its ``GeoResolution`` / ``ThroughputResolution`` (``applied`` read); absent
            or ``None`` when the provider had no feed.
        date_attr: The resolution attribute holding the series date used (``"asof_date"`` for GPR);
            ``None`` for an undated source.
    """
    names = list(dict.fromkeys(tagged))
    outcomes: dict[str, str | None] = {}
    dates: list[date] = []
    for name in names:
        res = resolutions.get(name)
        if res is None:
            outcomes[name] = HOLD_NO_FEED
            continue
        outcomes[name] = "" if res.applied else HOLD_NOT_APPLIED
        if res.applied and date_attr is not None and (d := _iso_date(getattr(res, date_attr, None))):
            dates.append(d)
    return tally(names, outcomes, latest=max(dates) if dates else None)


def problem_lines(block: Mapping[str, Any]) -> list[str]:
    """One log line per source in a signal-health block that is not ``ok`` or ``unused``, worst first."""
    lines = []
    for s in block.get("sources", []):
        if s["status"] in ("ok", "unused"):
            continue
        age = ("undated" if s["staleness_days"] is None
               else f"{s['staleness_days']}d old" + (" (stale)" if s["stale"] else ""))
        held = ", ".join(f"{k} x{v}" for k, v in s["holds"].items()) or "none"
        lines.append(f"{s['source']} {s['status'].upper()}: covered {s['covered']}/{s['expected']}, "
                     f"{age}, holds {held}")
    return lines


def _field(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)


def _iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
