"""MacroRAG REST request/response models + the structured shock-vector handoff (S4).

The first group mirrors MacroRAG's *live* API (`src/api/models.py` over there): the
`POST /query` request/response and the `/citations/{id}` chunk. The second group is the
**agreed handoff contract** (catalog §9B): a `shock_vector` block of cited, unit-tagged
field moves that MacroRAG's scenario-synthesis step will return inside the query response.
That synthesis capability is a pending MacroRAG build item, so `MacroSynthesis.from_response`
tolerates its absence (empty `fields`) and the provider falls back to static drivers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

# MacroRAG validation codes that mark a field as unusable (MacroRAG `HARD_VALIDATION_CODES`,
# src/synthesis/validate.py). MacroRAG drops these itself only when its `synthesis.drop_invalid`
# is on, which is deployment config; faultline drops them regardless so the contract holds
# whichever MacroRAG serves the call. Other codes (e.g. `horizon_unknown`) are surface-only.
HARD_VALIDATION_CODES = frozenset({"implausible_magnitude", "unit_scale_mismatch", "horizon_mismatch",
                                   "direction_mismatch", "card_value_mismatch"})


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

@dataclass
class MacroFilter:
    """Mirrors MacroRAG's FilterModel — the facet filter narrowing retrieval."""

    tiers: list[int] = field(default_factory=list)
    buckets: list[str] = field(default_factory=list)
    doc_types: list[str] = field(default_factory=list)
    entity_tags: list[str] = field(default_factory=list)
    factor_tags: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)
    date_from: str | None = None
    date_to: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "tiers": self.tiers, "buckets": self.buckets, "doc_types": self.doc_types,
            "entity_tags": self.entity_tags, "factor_tags": self.factor_tags,
            "publishers": self.publishers, "date_from": self.date_from, "date_to": self.date_to,
        }

    @classmethod
    def from_config(cls, spec: dict[str, Any]) -> MacroFilter:
        return cls(
            tiers=list(spec.get("tiers", [])), buckets=list(spec.get("buckets", [])),
            doc_types=list(spec.get("doc_types", [])),
            entity_tags=list(spec.get("entity_tags", [])),
            factor_tags=list(spec.get("factor_tags", [])),
            publishers=list(spec.get("publishers", [])),
            date_from=spec.get("date_from"), date_to=spec.get("date_to"),
        )


def point_in_time_filter(filters: MacroFilter, as_of: date) -> MacroFilter:
    """Caps a MacroRAG filter's ``date_to`` at the run's as-of date (point-in-time retrieval).

    Args:
        filters: The scenario's facet filter; it is not mutated.
        as_of: The run's valuation date; no chunk released after it may be retrieved.

    Returns:
        A new ``MacroFilter`` equal to ``filters`` except that ``date_to`` is ``as_of.isoformat()``
        when ``filters.date_to`` is ``None`` or later than ``as_of``; an earlier or equal ``date_to``
        is kept as given.

    Raises:
        ValueError: If ``date_from`` or ``date_to`` is not an ISO date (``YYYY-MM-DD``; only the first
            10 characters are read), or ``date_from`` is later than ``as_of``.
    """
    date_from = _iso_date(filters.date_from, "date_from")
    date_to = _iso_date(filters.date_to, "date_to")
    if date_from is not None and date_from > as_of:
        raise ValueError(f"filter date_from {filters.date_from!r} is after the run's as-of {as_of.isoformat()}")
    capped = filters.date_to if date_to is not None and date_to <= as_of else as_of.isoformat()
    return replace(filters, date_to=capped)


def _iso_date(value: str | None, name: str) -> date | None:
    """The ``YYYY-MM-DD`` date of a filter bound (first 10 characters), or None when unset."""
    if value is None:
        return None
    head = str(value)[:10]
    if not _ISO_DATE.fullmatch(head):
        raise ValueError(f"filter {name} {value!r} is not an ISO date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(head)
    except ValueError as exc:
        raise ValueError(f"filter {name} {value!r} is not a valid date: {exc}") from exc


@dataclass
class MacroChunk:
    """A retrieved/cited chunk's brief (from `chunks[]` or `/citations/{id}`)."""

    chunk_id: str
    source_id: str = ""
    publisher: str = ""
    publication: str = ""
    release_date: str = ""        # ISO date (the freshness key)
    doc_type: str = ""
    rerank_score: float = 0.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MacroChunk:
        return cls(
            chunk_id=str(d.get("chunk_id", "")),
            source_id=str(d.get("source_id") or ""),
            publisher=str(d.get("publisher") or ""),
            publication=str(d.get("publication") or ""),
            release_date=str(d.get("release_date") or ""),
            doc_type=str(d.get("doc_type") or ""),
            rerank_score=float(d.get("rerank_score") or 0.0),
        )


@dataclass
class MacroAnswer:
    """Mirrors MacroRAG's `/query` response (the cited-prose answer)."""

    summary: str = ""
    refused: bool = False
    details: list[dict[str, Any]] = field(default_factory=list)
    unanswered: list[str] = field(default_factory=list)
    used_chunk_ids: list[str] = field(default_factory=list)
    timeseries: list[dict[str, Any]] = field(default_factory=list)
    route: str = ""
    cached: bool = False
    chunks: list[MacroChunk] = field(default_factory=list)

    @classmethod
    def from_response(cls, d: dict[str, Any]) -> MacroAnswer:
        return cls(
            summary=str(d.get("summary", "")), refused=bool(d.get("refused", False)),
            details=list(d.get("details", [])), unanswered=list(d.get("unanswered", [])),
            used_chunk_ids=list(d.get("used_chunk_ids", [])),
            timeseries=list(d.get("timeseries", [])),
            route=str(d.get("route", "")), cached=bool(d.get("cached", False)),
            chunks=[MacroChunk.from_dict(c) for c in d.get("chunks", [])],
        )


@dataclass
class MacroShockField:
    """One cited, unit-tagged field of the structured shock vector (handoff §9B).

    ``name`` is a MacroRAG shock-vector field (UST_10Y, MOVE, VIX, IG_OAS, USDTWD, …);
    ``unit`` is bp | pct | pts | rel; ``chunk_ids`` are the citing macro chunks.
    ``grounding_mode`` is ``analog`` (anchored in a past episode) or ``direct``;
    ``source_horizon_days`` is the horizon the evidence measured the move over, as MacroRAG
    declared it (None if undeclared); ``validation`` holds MacroRAG's validation codes. All three
    are provenance; only a hard validation code changes what is applied (see `hard_invalid`).
    """

    name: str
    value: float
    unit: str = ""
    chunk_ids: list[str] = field(default_factory=list)
    rationale: str = ""
    grounding_mode: str = "direct"
    source_horizon_days: int | None = None
    validation: list[str] = field(default_factory=list)

    @property
    def hard_invalid(self) -> bool:
        """Whether MacroRAG flagged this field with a hard (field-unusable) validation code."""
        return any(code in HARD_VALIDATION_CODES for code in self.validation)

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> MacroShockField:
        horizon = d.get("source_horizon_days")
        return cls(
            name=name, value=float(d.get("value", 0.0)), unit=str(d.get("unit", "")),
            chunk_ids=list(d.get("chunk_ids", [])), rationale=str(d.get("rationale", "")),
            grounding_mode="analog" if d.get("grounding_mode") == "analog" else "direct",
            source_horizon_days=(horizon if isinstance(horizon, int) and not isinstance(horizon, bool)
                                 else None),
            validation=[str(code) for code in d.get("validation") or []],
        )


@dataclass
class MacroSynthesis:
    """The structured handoff: the shock-vector fields + the chunks that justify them.

    ``fields`` holds only usable fields: a field MacroRAG flagged with a hard validation code is
    moved to ``dropped`` (name -> its codes) and treated as if absent.
    """

    fields: list[MacroShockField] = field(default_factory=list)
    chunks: list[MacroChunk] = field(default_factory=list)
    dropped: dict[str, list[str]] = field(default_factory=dict)
    refused: bool = False
    summary: str = ""
    world_view_id: str = ""        # MacroRAG corpus-snapshot id, for provenance pinning

    @property
    def available(self) -> bool:
        return bool(self.fields) and not self.refused

    @classmethod
    def from_response(cls, d: dict[str, Any]) -> MacroSynthesis:
        sv = d.get("shock_vector") or {}
        parsed = [MacroShockField.from_dict(name, spec) for name, spec in sv.items()
                  if isinstance(spec, dict)]
        return cls(
            fields=[f for f in parsed if not f.hard_invalid],
            dropped={f.name: list(f.validation) for f in parsed if f.hard_invalid},
            chunks=[MacroChunk.from_dict(c) for c in d.get("chunks", [])],
            refused=bool(d.get("refused", False)),
            summary=str(d.get("summary", "")),
            world_view_id=str(d.get("world_view_id", "")),
        )
