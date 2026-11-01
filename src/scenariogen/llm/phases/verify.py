"""Verify phase (P7) — faithfulness gate on calibrated driver legs.

A cheap single JSON call (gpt-5-nano) that checks each calibrated driver leg against its cited
evidence and drops the ones the verifier marks UNSUPPORTED before the scenario is priced —
mirroring ARag's faithfulness verifier. Lenient by design: a parse/verify failure never kills
the scenario, and the guard never drops *every* leg (materialize needs at least one driver).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ..config import LLMSettings
from ..gateway import BudgetExceededError, InstrumentedChatClient
from ..prompts import PromptManager
from ..schema import CalibratedScenario
from .json_schemas import VERIFY_SCHEMA, response_format_for
from .parsing import extract_json_object

logger = logging.getLogger("scenariogen.llm.phases.verify")


@dataclass
class VerifyResult:
    calibrated: CalibratedScenario  # possibly with UNSUPPORTED legs removed
    verdicts: list[dict[str, Any]] = field(default_factory=list)
    n_dropped: int = 0


def verify_scenario(
    *,
    gateway: InstrumentedChatClient,
    prompts: PromptManager,
    settings: LLMSettings,
    calibrated: CalibratedScenario,
    evidence: str,
) -> VerifyResult:
    if not calibrated.legs:
        return VerifyResult(calibrated)
    statements = json.dumps(
        [
            {
                "factor": leg.factor, "move": leg.move, "origin": leg.origin,
                "rationale": leg.rationale,
                "citations": [c.model_dump() for c in leg.citations],
            }
            for leg in calibrated.legs
        ],
        indent=2,
    )
    system = prompts.render("verify_system")
    user = prompts.render("verify_user", statements=statements, evidence=evidence)
    try:
        result = gateway.complete(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=settings.model_for("verify"),
            stage="verify",
            max_completion_tokens=settings.max_tokens_for("verify"),
            response_format=response_format_for(settings, "verify", "leg_verdicts", VERIFY_SCHEMA),
        )
    except BudgetExceededError:
        raise  # the cost breaker is meant to hard-stop the run
    except Exception as exc:
        # Verify is a non-fatal safety pass: an API/backend error must never kill the scenario.
        logger.warning("verify: LLM call failed (%s); keeping all legs", exc)
        return VerifyResult(calibrated)
    try:
        obj = extract_json_object(result.content)
    except ValueError:
        logger.warning("verify: could not parse verdicts; keeping all legs")
        return VerifyResult(calibrated)

    verdicts = [v for v in (obj.get("verdicts") or []) if isinstance(v, dict)]
    unsupported = {
        str(v.get("factor")) for v in verdicts if str(v.get("verdict", "")).upper() == "UNSUPPORTED"
    }
    kept = [leg for leg in calibrated.legs if leg.factor not in unsupported]
    if not kept or len(kept) == len(calibrated.legs):
        # never drop everything (materialize needs a driver); nothing to drop -> unchanged
        return VerifyResult(calibrated.model_copy(update={"verification": {"n_dropped": 0, "verdicts": verdicts}}),
                            verdicts, 0)
    n_dropped = len(calibrated.legs) - len(kept)
    logger.info("verify: dropped %d unsupported leg(s) from %r", n_dropped, calibrated.name)
    verified = calibrated.model_copy(
        update={"legs": kept, "verification": {"n_dropped": n_dropped, "verdicts": verdicts}}
    )
    return VerifyResult(verified, verdicts, n_dropped)
