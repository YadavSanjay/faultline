"""Theme-relevant key events and crowding for every single-name equity driver.

Pure functions over already-loaded data (``docs/specs/event-scenario-mapping.md``):

* ``load_events_config`` validates ``configs/scenariogen/events.json`` into an ``EventsConfig``;
* ``theme_event_score`` / ``theme_news_multiplier`` weight a name's 8-K items by the scenario's theme;
* ``scenario_triggers`` maps 8-K items to the scenarios they make live;
* ``equity_leg_multipliers`` / ``scale_driver_residuals`` carry news and crowding to every equity driver
  that is not a company leg, scaling only its company-specific part;
* ``crowding_reliable`` decides whether a name's short-interest history is usable.
"""

from __future__ import annotations

import json
import math
from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from src.common.config.models import ConfigError
from src.scenariogen import factor_keys as fk
from src.scenariogen.config import config_dir
from src.scenariogen.news.mapping import EventRow, NewsLeg, NewsMapConfig

if TYPE_CHECKING:
    from src.scenariogen.crowding import CrowdingResolution
    from src.scenariogen.news.provider import NewsResolution

_TOP_KEYS = frozenset({"description", "item_weights", "excluded", "crowding_min_obs",
                       "crowding_max_staleness_days", "themes"})
_THEME_KEYS = frozenset({"news_items", "tone", "crowding", "trigger_items", "trigger_names"})
_REQUIRED = ("item_weights", "crowding_min_obs", "crowding_max_staleness_days")


class ScenarioTheme(NamedTuple):
    """Which 8-K items, tone and crowding are relevant to one scenario, and which items trigger it."""

    news_items: frozenset[str]
    tone: bool
    crowding: bool
    trigger_items: frozenset[str]
    trigger_names: frozenset[str]


class EventsConfig(NamedTuple):
    """The validated ``events.json``: item weights, excluded tickers, crowding floors and themes."""

    item_weights: dict[str, float]
    excluded: frozenset[str]
    crowding_min_obs: int
    crowding_max_staleness_days: int
    themes: dict[str, ScenarioTheme]


class TriggerHit(NamedTuple):
    """One 8-K item on one name that makes a scenario live."""

    item: str
    ticker: str
    filing_date: str
    accession: str


EMPTY_THEME = ScenarioTheme(frozenset(), False, False, frozenset(), frozenset())


def _unknown(keys: Iterable[str], allowed: frozenset[str]) -> list[str]:
    return sorted(k for k in keys if not str(k).startswith("_") and k not in allowed)


def _str_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"events.json: {where} must be a list of strings")
    return list(value)


def _int_at_least(raw: Mapping[str, Any], key: str, low: int) -> int:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise ConfigError(f"events.json: {key} must be an integer >= {low}, got {value!r}")
    return int(value)


def _theme(name: str, spec: Any, weights: Mapping[str, float]) -> ScenarioTheme:
    if not isinstance(spec, Mapping):
        raise ConfigError(f"events.json: theme {name!r} must be an object")
    unknown = _unknown(spec, _THEME_KEYS)
    if unknown:
        raise ConfigError(f"events.json: theme {name!r} has unknown key(s) {unknown}")
    flags: dict[str, bool] = {}
    for key in ("tone", "crowding"):
        value = spec.get(key, False)
        if not isinstance(value, bool):
            raise ConfigError(f"events.json: theme {name!r} {key} must be a bool, got {value!r}")
        flags[key] = value
    items: dict[str, frozenset[str]] = {}
    for key in ("news_items", "trigger_items"):
        codes = _str_list(spec.get(key, []), f"theme {name!r} {key}")
        missing = sorted(set(codes) - set(weights))
        if missing:
            raise ConfigError(f"events.json: theme {name!r} {key} {missing} not in item_weights")
        items[key] = frozenset(codes)
    names = frozenset(t.upper() for t in _str_list(spec.get("trigger_names", []), f"theme {name!r} trigger_names"))
    return ScenarioTheme(items["news_items"], flags["tone"], flags["crowding"], items["trigger_items"], names)


def load_events_config(raw: Mapping[str, Any], scenarios: Collection[str]) -> EventsConfig:
    """Validate a parsed ``events.json`` against the known scenario ids.

    Args:
        raw: The parsed JSON object.
        scenarios: Every scenario id; each gets a theme (the empty theme when none is configured).

    Returns:
        The validated config.

    Raises:
        ConfigError: For an unknown key, a theme for an unknown scenario, an item not in
            ``item_weights``, an invalid weight or flag, or a missing or out-of-range field.
    """
    unknown = _unknown(raw, _TOP_KEYS)
    if unknown:
        raise ConfigError(f"events.json: unknown key(s) {unknown}")
    missing = [k for k in _REQUIRED if k not in raw]
    if missing:
        raise ConfigError(f"events.json: missing key(s) {missing}")
    raw_weights = raw["item_weights"]
    if not isinstance(raw_weights, Mapping):
        raise ConfigError("events.json: item_weights must be an object")
    weights: dict[str, float] = {}
    for item, w in raw_weights.items():
        if isinstance(w, bool) or not isinstance(w, int | float) or not math.isfinite(w) or w < 0:
            raise ConfigError(f"events.json: item weight {item!r} must be a finite number >= 0, got {w!r}")
        weights[str(item)] = float(w)
    excluded = frozenset(t.upper() for t in _str_list(raw.get("excluded", []), "excluded"))
    min_obs = _int_at_least(raw, "crowding_min_obs", 1)
    staleness = _int_at_least(raw, "crowding_max_staleness_days", 0)
    raw_themes = raw.get("themes", {})
    if not isinstance(raw_themes, Mapping):
        raise ConfigError("events.json: themes must be an object")
    known = set(scenarios)
    themes: dict[str, ScenarioTheme] = {}
    for name, spec in raw_themes.items():
        if str(name).startswith("_"):
            continue
        if name not in known:
            raise ConfigError(f"events.json: theme for unknown scenario {name!r}")
        themes[name] = _theme(name, spec, weights)
    for name in scenarios:
        themes.setdefault(name, EMPTY_THEME)
    return EventsConfig(weights, excluded, min_obs, staleness, themes)


def _codes(e: EventRow) -> list[str]:
    """Item codes of an event: comma-separated, stripped, blanks dropped, each once."""
    out: list[str] = []
    for c in e.item_codes.split(","):
        c = c.strip()
        if c and c not in out:
            out.append(c)
    return out


def _age(e: EventRow, as_of: date) -> int | None:
    try:
        return (as_of - date.fromisoformat(e.filing_date[:10])).days
    except ValueError:
        return None


def theme_event_score(events: Sequence[EventRow], theme: ScenarioTheme, item_weights: Mapping[str, float],
                      as_of: date, cfg: NewsMapConfig) -> tuple[float, list[EventRow]]:
    """Age-decayed sum of the theme weights of in-window events.

    Args:
        events: One name's 8-K events.
        theme: The scenario's theme; only its ``news_items`` count.
        item_weights: 8-K item code -> weight.
        as_of: The run date; age is days from ``filing_date`` to it.
        cfg: Window, half-life and the other news-mapping parameters.

    Returns:
        ``(score, contributing)``: the score and the in-window events with a theme item, in input order.
    """
    score = 0.0
    contributing: list[EventRow] = []
    for e in events:
        age = _age(e, as_of)
        if age is None or age < 0 or age > cfg.window_days:
            continue
        items = [c for c in _codes(e) if c in theme.news_items]
        if not items:
            continue
        decay = 0.5 ** (age / cfg.decay_halflife_days) if cfg.decay_halflife_days > 0 else 1.0
        score += sum(float(item_weights[c]) for c in items) * decay
        contributing.append(e)
    return score, contributing


def theme_news_multiplier(ticker: str, events: Sequence[EventRow], tone_z: float | None, as_of: date,
                          cfg: NewsMapConfig, theme: ScenarioTheme, item_weights: Mapping[str, float]) -> NewsLeg:
    """The clamped, theme-weighted news multiplier for one name.

    Args:
        ticker: The name.
        events: Its 8-K events.
        tone_z: Its GDELT tone z-score, or ``None``.
        as_of: The run date.
        cfg: The news-mapping parameters.
        theme: The scenario's theme.
        item_weights: 8-K item code -> weight.

    Returns:
        The auditable news leg.
    """
    score, contributing = theme_event_score(events, theme, item_weights, as_of, cfg)
    use_tone = theme.tone and tone_z is not None
    tone_term = cfg.beta_tone * -float(tone_z) if use_tone and tone_z is not None else 0.0
    mult = max(cfg.min_scale, min(cfg.max_scale, 1.0 + cfg.beta_event * score + tone_term))
    codes: list[str] = []
    for e in contributing:
        codes.extend(c for c in _codes(e) if c in theme.news_items and c not in codes)
    applied = mult != 1.0 or bool(contributing) or use_tone
    return NewsLeg(ticker=ticker, multiplier=mult, event_score=score, tone_z=tone_z if theme.tone else None,
                   n_events=len(contributing), accessions=[e.accession for e in contributing],
                   item_codes=codes, applied=applied,
                   note="" if applied else "no theme events in window and no tone")


def scenario_triggers(events_by_ticker: Mapping[str, Sequence[EventRow]], as_of: date, window_days: int,
                      themes: Mapping[str, ScenarioTheme]) -> dict[str, list[TriggerHit]]:
    """The trigger hits of every scenario whose theme has trigger items.

    Args:
        events_by_ticker: Ticker -> its 8-K events.
        as_of: The run date; later events never count.
        window_days: The trailing window, inclusive at both ends.
        themes: Scenario id -> theme.

    Returns:
        Scenario id -> hits sorted by ``(filing_date, ticker, item)``; ``[]`` for no hit, and no entry
        for a scenario without trigger items.
    """
    out: dict[str, list[TriggerHit]] = {}
    for name, theme in themes.items():
        if not theme.trigger_items:
            continue
        names = {t.upper() for t in theme.trigger_names}
        hits: list[TriggerHit] = []
        for ticker, events in events_by_ticker.items():
            tk = ticker.upper()
            if names and tk not in names:
                continue
            for e in events:
                age = _age(e, as_of)
                if age is None or age < 0 or age > window_days:
                    continue
                hits.extend(TriggerHit(c, tk, e.filing_date[:10], e.accession)
                            for c in _codes(e) if c in theme.trigger_items)
        out[name] = sorted(hits, key=lambda h: (h.filing_date, h.ticker, h.item))
    return out


def equity_leg_multipliers(drivers: Iterable[str], legs: Collection[str], news_mult: Mapping[str, float],
                           crowding_mult: Mapping[str, float], theme: ScenarioTheme, excluded: Collection[str],
                           lead: str | None) -> dict[str, float]:
    """Compound news x crowding multipliers for the equity drivers that are not company legs.

    Args:
        drivers: The scenario's driver factor keys.
        legs: The XbrlRag company-leg factor keys.
        news_mult: Upper-case ticker -> news multiplier.
        crowding_mult: Upper-case ticker -> crowding multiplier.
        theme: The scenario's theme; switches news and crowding on or off.
        excluded: Tickers never scaled.
        lead: The scenario's severity lead factor, or ``None``.

    Returns:
        Factor -> compound multiplier, only for compounds that are not exactly 1.
    """
    has_news = bool(theme.news_items) or theme.tone
    skip = {t.upper() for t in excluded}
    out: dict[str, float] = {}
    for f in drivers:
        if not fk.is_equity(f) or f in legs or f == lead:
            continue
        ticker = fk.equity_name(f).upper()
        if ticker in skip:
            continue
        n = news_mult.get(ticker, 1.0) if has_news else 1.0
        c = crowding_mult.get(ticker, 1.0) if theme.crowding else 1.0
        m = n * c
        if m != 1.0:
            out[f] = m
    return out


def scale_driver_residuals(drivers: Mapping[str, float], systematic: Mapping[str, float],
                           multipliers: Mapping[str, float], *, cap: float) -> dict[str, float]:
    """Scale the company-specific part of each driver by its capped multiplier.

    Args:
        drivers: Factor -> driver move.
        systematic: Factor -> its co-move from the other drivers (0 when absent).
        multipliers: Factor -> multiplier; non-drivers are ignored.
        cap: Multipliers are clamped to ``[1/cap, cap]``.

    Returns:
        A new driver dict.

    Raises:
        ValueError: For ``cap < 1`` or a multiplier that is not finite or is <= 0.
    """
    if not cap >= 1:
        raise ValueError(f"cap must be >= 1, got {cap}")
    out = dict(drivers)
    for f, m in multipliers.items():
        if f not in out:
            continue
        if not math.isfinite(m) or m <= 0:
            raise ValueError(f"multiplier for {f} must be finite and > 0, got {m}")
        s = float(systematic.get(f, 0.0))
        out[f] = s + (float(out[f]) - s) * max(1.0 / cap, min(cap, float(m)))
    return out


def crowding_reliable(settlement_dates: Sequence[str], as_of: date, *, min_obs: int,
                      max_staleness_days: int) -> bool:
    """Whether a name's short-interest history is deep and fresh enough as of ``as_of``.

    Args:
        settlement_dates: ISO dates; only the first 10 characters are read.
        as_of: The run date; later dates are ignored.
        min_obs: The minimum number of dates on or before ``as_of``.
        max_staleness_days: The latest such date may be at most this many days before ``as_of``.

    Returns:
        True when both hold.
    """
    past = [d for d in (date.fromisoformat(s[:10]) for s in settlement_dates) if d <= as_of]
    if not past or len(past) < min_obs:
        return False
    return (as_of - max(past)).days <= max_staleness_days


def load_events(root: Path | None, scenarios: Collection[str]) -> EventsConfig | None:
    """``configs/scenariogen/events.json`` validated against ``scenarios``; ``None`` when the file is absent.

    Raises:
        ConfigError: An invalid file (``load_events_config``).
    """
    path = config_dir(root) / "events.json"
    if not path.exists():
        return None
    return load_events_config(json.loads(path.read_text(encoding="utf-8")), scenarios)


class ScenarioEvents:
    """One run's theme-relevant news, reliable crowding and trigger hits, per scenario.

    Built from the run's PIT news and crowding resolutions (either may be ``None``). A held news gate
    or a missing feed gives multiplier 1 everywhere, as before.
    """

    def __init__(self, config: EventsConfig, news: NewsResolution | None,
                 crowding: CrowdingResolution | None, as_of: date):
        self.config = config
        self.news = news
        self.crowding = crowding
        self.as_of = as_of
        self._news_cache: dict[str, dict[str, NewsLeg]] = {}

    def theme(self, name: str) -> ScenarioTheme:
        """The scenario's theme; the empty theme when it has none."""
        return self.config.themes.get(name, EMPTY_THEME)

    def news_legs(self, name: str) -> dict[str, NewsLeg]:
        """Ticker -> theme-weighted news leg; empty when the theme has no news or news is not applied."""
        if name not in self._news_cache:
            th = self.theme(name)
            news = self.news
            legs: dict[str, NewsLeg] = {}
            if news is not None and news.applied and news.map_cfg is not None and (th.news_items or th.tone):
                tickers = sorted(set(news.pit_events) | set(news.tone_z))
                for t in tickers:
                    legs[t] = theme_news_multiplier(t, news.pit_events.get(t, []), news.tone_z.get(t), self.as_of,
                                                    news.map_cfg, th, self.config.item_weights)
            self._news_cache[name] = legs
        return self._news_cache[name]

    def reliable(self, ticker: str) -> bool:
        """Whether the name's short-interest history passes ``crowding_reliable`` (False without a feed)."""
        if self.crowding is None:
            return False
        return crowding_reliable(self.crowding.history.get(ticker.upper(), []), self.as_of,
                                 min_obs=self.config.crowding_min_obs,
                                 max_staleness_days=self.config.crowding_max_staleness_days)

    def news_mult(self, name: str) -> dict[str, float]:
        """Ticker -> theme news multiplier, only where it is not 1."""
        return {t: leg.multiplier for t, leg in self.news_legs(name).items() if leg.multiplier != 1.0}

    def crowding_mult(self, name: str) -> dict[str, float]:
        """Ticker -> crowding multiplier for a crowding theme and a reliable name, only where it is not 1."""
        crowding = self.crowding
        if not self.theme(name).crowding or crowding is None or not crowding.applied:
            return {}
        return {t: leg.multiplier for t, leg in crowding.legs.items()
                if leg.multiplier != 1.0 and self.reliable(t)}

    def triggers(self, name: str) -> list[TriggerHit]:
        """The scenario's trigger hits (empty when it has no trigger items or no 8-K feed)."""
        news = self.news
        th = self.theme(name)
        if news is None or not th.trigger_items:
            return []
        window = news.map_cfg.window_days if news.map_cfg is not None else 90
        return scenario_triggers(news.pit_events, self.as_of, window, {name: th}).get(name, [])

    def provenance(self, name: str) -> dict[str, Any]:
        """Theme, trigger hits and the per-name legs that move this scenario, for the report."""
        th = self.theme(name)
        crowd = self.crowding
        crowd_legs = {t: dict(crowd.legs[t].to_dict(), reliable=self.reliable(t))
                      for t in self.crowding_mult(name)} if crowd is not None else {}
        return {
            "theme": {"news_items": sorted(th.news_items), "tone": th.tone, "crowding": th.crowding,
                      "trigger_items": sorted(th.trigger_items), "trigger_names": sorted(th.trigger_names)},
            "triggers": [h._asdict() for h in self.triggers(name)],
            "news": {t: leg.to_dict() for t, leg in self.news_legs(name).items() if leg.multiplier != 1.0},
            "crowding": crowd_legs,
        }
