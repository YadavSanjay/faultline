"""MacroRAG shock-vector field -> canonical factor key translation (S4).

MacroRAG emits macro-leg fields in the catalog §9B vocabulary
(``{ΔUST, ΔMOVE, ΔVIX, ΔIG_OAS, ΔHY_OAS, ΔUSDTWD, ΔUSDJPY, Δbasis}``); this maps them to
the canonical factor keys the generator/engine consume (`factor_keys`) and converts units
to canonical native (decimal) moves.

Notes on the (deliberately lossy) mapping:
  * MacroRAG provides macro legs only — **per-name equity (`eq:*`) stays covariance-derived**
    or comes from the house/expert driver, so equity fields are not produced here.
  * The book has a single systematic `credit` factor, so ``IG_OAS`` maps to it; ``HY_OAS`` is
    used only if no IG move is given.
  * ``XCCY_BASIS`` has no factor in the book today (basis held at 0), so it is dropped.
  * ``MOVE`` is an index level; a points move is converted to the relative `ratesvol` shift
    via the configured ``move_base`` (unit ``rel`` is taken as-is).
  * ``REAL_YIELD_10Y`` / ``TERM_PREMIUM_10Y`` (bp) map to the driver-only rate-decomposition
    factors ``real:DFII10`` / ``tp:ACMTP10`` (decimal). They are conditioned on jointly with any
    nominal ``ir:`` leg, not added to it, so a vector carrying both does not double-count.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .. import factor_keys as fk
from .models import MacroShockField

DEFAULT_MOVE_BASE = 100.0

# Per-tenor UST fields -> ir control node.
_UST_TENOR = {"UST_2Y": "DGS2", "UST_5Y": "DGS5", "UST_10Y": "DGS10", "UST_30Y": "DGS30"}

# Rate-decomposition fields -> driver-only F6 factors (bp -> decimal).
_RATES_DECOMP = {"REAL_YIELD_10Y": fk.REAL_10Y_KEY, "TERM_PREMIUM_10Y": fk.TERM_PREMIUM_10Y_KEY}

# Every MacroRAG field name `to_canonical_drivers` maps; any other name is unmapped (dropped).
MAPPED_FIELDS = frozenset({*_UST_TENOR, *_RATES_DECOMP, "UST", "IG_OAS", "HY_OAS", "MOVE", "VIX",
                           "USDTWD", "USDJPY"})


@dataclass
class MacroDriver:
    """A canonical-unit driver sourced from MacroRAG, with its citations."""

    factor: str               # canonical factor key (e.g. "ir:DGS10", "credit", "fx:TWD")
    move: float               # canonical native-unit move (decimal)
    macro_field: str          # the originating MacroRAG field
    chunk_ids: list[str] = field(default_factory=list)
    rationale: str = ""
    grounding_mode: str = "direct"            # from the originating field (provenance only)
    source_horizon_days: int | None = None    # evidence horizon MacroRAG declared for the field
    validation: list[str] = field(default_factory=list)   # MacroRAG's (soft) validation codes

    def as_source(self) -> dict[str, Any]:
        return {"origin": "macrorag", "macro_field": self.macro_field,
                "chunk_ids": list(self.chunk_ids), "rationale": self.rationale,
                "grounding_mode": self.grounding_mode,
                "source_horizon_days": self.source_horizon_days,
                "validation": list(self.validation)}


def _convert(target: str, value: float, unit: str, move_base: float) -> float:
    """MacroRAG (field value, unit) -> canonical native-unit move for `target`."""
    if fk.is_ir(target) or target == fk.CREDIT_KEY or fk.is_rates_decomp(target):   # bp -> decimal
        return value * 1e-4
    if fk.is_fx(target):                                    # pct -> return
        return value / 100.0
    if target == fk.EQUITY_VOL_KEY:                         # VIX points -> decimal IV
        return value / 100.0
    if target == fk.RATES_VOL_KEY:                          # MOVE: rel as-is, else pts/base
        return value if unit == "rel" else value / move_base
    return value


def to_canonical_drivers(fields: Iterable[MacroShockField],
                         move_base: float = DEFAULT_MOVE_BASE) -> list[MacroDriver]:
    """Translate MacroRAG shock-vector fields to canonical `MacroDriver`s.

    Unmapped fields (e.g. ``XCCY_BASIS``) are skipped; ``HY_OAS`` is used for `credit` only
    when no ``IG_OAS`` is present.
    """
    by_name = {f.name: f for f in fields}
    drivers: list[MacroDriver] = []

    def add(target: str, src: MacroShockField) -> None:
        drivers.append(MacroDriver(target, _convert(target, src.value, src.unit, move_base),
                                   src.name, list(src.chunk_ids), src.rationale,
                                   grounding_mode=src.grounding_mode,
                                   source_horizon_days=src.source_horizon_days,
                                   validation=list(src.validation)))

    for name, f in by_name.items():
        if name in _UST_TENOR:
            add(f"{fk.IR_PREFIX}{_UST_TENOR[name]}", f)
        elif name == "UST":                                # parallel -> all control nodes
            for tenor in fk.IR_TENOR_YEARS:
                add(f"{fk.IR_PREFIX}{tenor}", f)
        elif name == "IG_OAS" or (name == "HY_OAS" and "IG_OAS" not in by_name):
            add(fk.CREDIT_KEY, f)                           # one systematic credit factor
        elif name == "MOVE":
            add(fk.RATES_VOL_KEY, f)
        elif name == "VIX":
            add(fk.EQUITY_VOL_KEY, f)
        elif name == "USDTWD":
            add(f"{fk.FX_PREFIX}TWD", f)
        elif name == "USDJPY":
            add(f"{fk.FX_PREFIX}JPY", f)
        elif name in _RATES_DECOMP:
            add(_RATES_DECOMP[name], f)
        # else: unmapped (XCCY_BASIS, equity legs, …) -> dropped; see `unmapped_fields`
    return drivers


def unmapped_fields(fields: Iterable[MacroShockField]) -> list[str]:
    """Names of ``fields`` with no canonical mapping, sorted and de-duplicated."""
    return sorted({f.name for f in fields} - MAPPED_FIELDS)
