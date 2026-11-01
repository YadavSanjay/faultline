"""Materialized scenario = a per-factor shock vector + its bump transforms (S1).

This is the design's "generalize `Scenario` to a materialized per-factor vector" (§8.1),
implemented as a scenariogen type rather than mutating the pricer's scalar `Scenario`.

A `MaterializedScenario` carries a complete vector over the canonical factor keys
(per-tenor IR, per-name equity, per-name/systematic vol, per-pair FX, credit) in native
units, and lowers it to the granular bump transforms the P2 engine already applies
(`shift_ir_curve`, `scale_equity_named`, `shift_equity_vol`, `scale_rates_vol`,
`scale_fx_named`, `shift_credit`). The book is then fully re-priced under the vector by
the existing `ScenarioEngine` — no pricer change.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from src.pricer.risk import bumps

from . import factor_keys as fk


def vector_to_transforms(vector: Mapping[str, float]) -> list[bumps.Transform]:
    """Lower a canonical factor vector to the ordered list of bump transforms.

    Equity uses the systematic-proxy (XLK) move as the default for any underlier not
    named in the vector, so option underliers without own spot history still move.
    """
    ir = {fk.ir_tenor(k): v for k, v in vector.items() if fk.is_ir(k)}
    equity = {fk.equity_name(k): v for k, v in vector.items() if fk.is_equity(k)}
    fx = {fk.fx_ccy(k): v for k, v in vector.items() if fk.is_fx(k)}
    fxrate = {fk.fx_rate_ccy(k): v for k, v in vector.items() if fk.is_fx_rate(k)}

    tfs: list[bumps.Transform] = []
    if ir:
        # np.interp inside shift_ir_curve needs control tenors sorted ascending in years.
        nodes = sorted(ir, key=lambda t: fk.IR_TENOR_YEARS[t])
        years = tuple(fk.IR_TENOR_YEARS[t] for t in nodes)
        shifts = tuple(ir[t] for t in nodes)
        tfs.append(bumps.shift_ir_curve(years, shifts))
    if equity:
        systematic = equity.get(fk.SYSTEMATIC_EQUITY, 0.0)
        tfs.append(bumps.scale_equity_named(equity, default=systematic))
    if fk.EQUITY_VOL_KEY in vector:
        tfs.append(bumps.shift_equity_vol(vector[fk.EQUITY_VOL_KEY]))
    if fk.EQUITY_VOL_TERM_KEY in vector:
        tfs.append(bumps.shift_equity_vol_term(vector[fk.EQUITY_VOL_TERM_KEY]))
    if fk.EQUITY_VOL_SKEW_KEY in vector:
        tfs.append(bumps.shift_equity_vol_skew(vector[fk.EQUITY_VOL_SKEW_KEY]))
    if fk.RATES_VOL_KEY in vector:
        tfs.append(bumps.scale_rates_vol(vector[fk.RATES_VOL_KEY]))
    if fk.RATES_VOL_TERM_KEY in vector:
        tfs.append(bumps.shift_rates_vol_term(vector[fk.RATES_VOL_TERM_KEY]))
    if fx:
        tfs.append(bumps.scale_fx_named(fx, default=0.0))
    if fxrate:
        tfs.append(bumps.shift_fx_rate_named(fxrate, default=0.0))
    issuers = {fk.issuer_credit_name(k): v for k, v in vector.items() if fk.is_issuer_credit(k)}
    if issuers:
        # An issuer key overrides the common credit move for that issuer's bond and CDS curves.
        tfs.append(bumps.shift_credit_issuers(issuers, vector.get(fk.CREDIT_KEY, 0.0)))
    elif fk.CREDIT_KEY in vector:
        tfs.append(bumps.shift_credit(vector[fk.CREDIT_KEY]))
    # Basis factors (F3). The equity-dispersion basis (basis:SMH_SEMI) is driver-only — it has no
    # transform and reprices only through the equity moves it propagates to. The two SPREAD bases
    # reprice directly via one-sided curve/spread shifts (they have no daily history for Σ, so they
    # are authored as overlays rather than covariance drivers).
    if fk.BASIS_SWAP_SPREAD_KEY in vector:
        tfs.append(bumps.shift_swap_spread(vector[fk.BASIS_SWAP_SPREAD_KEY]))
    if fk.BASIS_CDS_BOND_KEY in vector:
        tfs.append(bumps.shift_cds_bond_basis(vector[fk.BASIS_CDS_BOND_KEY]))
    return tfs


@dataclass(frozen=True)
class MaterializedScenario:
    """A scenario reduced to a concrete, fully-correlated factor vector ready to price."""

    name: str
    vector: dict[str, float]                 # canonical factor key -> native-unit move
    drivers: tuple[str, ...] = ()            # which keys were narrative-set (vs derived)
    severity: str = ""                       # mild | adverse | severe (label only)
    provenance: dict[str, Any] = field(default_factory=dict)

    def transforms(self) -> list[bumps.Transform]:
        return vector_to_transforms(self.vector)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "severity": self.severity,
            "drivers": list(self.drivers),
            "vector": dict(self.vector),
            "provenance": dict(self.provenance),
        }
