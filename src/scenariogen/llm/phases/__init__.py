"""LLM scenario-generation phases (P2+).

Each phase is a small function: evidence (agentic gather), propose (structured synthesis),
calibrate (agentic driver-shock calibration), narrate (free-text narrative). The flow wires them.
"""

from __future__ import annotations

from .calibrate import calibrate_scenario, fetch_shock_schema
from .discover import DiscoveryResult, discover_factor_universe
from .evidence import EvidenceBundle, gather_evidence
from .fact_check import CoverageView, FactCheckConfig, FactCheckResult, fact_check
from .narrate import narrate_scenario
from .parsing import extract_json_object
from .propose import propose_scenario, propose_scenarios
from .verify import VerifyResult, verify_scenario

__all__ = [
    "CoverageView",
    "DiscoveryResult",
    "EvidenceBundle",
    "FactCheckConfig",
    "FactCheckResult",
    "VerifyResult",
    "calibrate_scenario",
    "discover_factor_universe",
    "extract_json_object",
    "fact_check",
    "fetch_shock_schema",
    "gather_evidence",
    "narrate_scenario",
    "propose_scenario",
    "propose_scenarios",
    "verify_scenario",
]
