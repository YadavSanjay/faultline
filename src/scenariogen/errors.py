"""scenariogen error types shared across the dual-RAG flow."""

from __future__ import annotations


class ScenarioGenerationError(RuntimeError):
    """A single named scenario could not be generated and must be **stopped**.

    Raised (fail-loud, dual-RAG decision 2) when a scenario a run *requires* to be grounded
    cannot be: a required RAG call timed out past the 10-min ceiling, a required company leg
    has no seed magnitude, or the ARag evidence GATE failed. The runner catches it *per
    scenario*, logs + documents the stop, and continues with the other scenarios — it does not
    abort the whole run (that is ``preflight.ServiceUnavailableError``, raised up front).
    """

    def __init__(self, scenario: str, reason: str, detail: str = ""):
        self.scenario = scenario
        self.reason = reason
        self.detail = detail
        msg = f"scenario '{scenario}' stopped: {reason}"
        if detail:
            msg += f" ({detail})"
        super().__init__(msg)
