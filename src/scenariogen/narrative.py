"""Dual-RAG value-add narrative (P2) — LLM prose over the attribution, never numbers.

A short, grounded write-up of *what each service contributed* to a scenario's shocks, generated
from the deterministic `ScenarioAttribution`. The LLM only narrates the facts it is given — it
sets no shock values (per the dual-RAG decision: LLM for narrative only). Reuses the scenariogen
LLM gateway (instrumented, cost-tracked) so each narrative carries its own cost.

Fail-soft: if the active profile's ``attribution`` provider has no API key, its SDK is absent, or
the call errors, `generate` returns ``None`` and the run proceeds with the deterministic
attribution (headline_lines) intact — the narrative is descriptive, not load-bearing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.common.config.models import ConfigError

from .attribution import ScenarioAttribution
from .llm.config import load_llm_settings
from .llm.cost import CostTracker
from .llm.gateway import (
    ChatBackendError,
    InstrumentedChatClient,
)
from .llm.profiles import build_provider_backend

logger = logging.getLogger("scenariogen.narrative")

_STAGE = "attribution"

_SYSTEM = (
    "You are a risk-methodology writer briefing a risk committee. In exactly 2 short paragraphs "
    "of flowing prose, explain what each data service contributed to a stress scenario's shocks. "
    "Paragraph 1 = MacroRAG (macro legs: rates/credit/vol/FX, grounded in Fed/Treasury/BIS "
    "primary sources). Paragraph 2 = ARag (per-name company legs, grounded in the companies' own "
    "SEC 10-K/Q Risk Factors, and — where shown — sized by a disclosed fact such as data-center "
    "revenue share). Emphasise the EXTRA information each service added beyond the authored "
    "baseline: the direction/size change, the fact-based sizing, and that the legs are cited.\n"
    "STYLE RULES (important): write for a human reader, not a machine. Do NOT echo raw JSON field "
    "names (e.g. 'final_move', 'gate_passed', 'n_citations'), do NOT print chunk ids / UUIDs, and "
    "do NOT quote full-precision decimals — round every number the way an analyst would speak it "
    "(rates in basis points, equity moves as whole or one-decimal percentages, e.g. '-43%'). "
    "Say 'cited to N SEC filings' or 'N primary-source citations', never list the ids. "
    "Use ONLY the numbers and facts in the provided JSON — never invent values or add legs. "
    "If a service did not apply (e.g. the freshness/citation gate failed), say so in one plain "
    "sentence and note the authored legs were used. No preamble, no headings, no bullet lists."
)


class AttributionNarrator:
    def __init__(self, client: InstrumentedChatClient, tracker: CostTracker, *, model: str,
                 max_tokens: int | None):
        self._client = client
        self.tracker = tracker
        self._model = model
        self._max_tokens = max_tokens

    def generate(self, attribution: ScenarioAttribution) -> dict[str, Any] | None:
        payload = json.dumps(self._facts(attribution), default=str)
        messages = [{"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": payload}]
        try:
            res = self._client.complete(messages=messages, model=self._model, stage=_STAGE,
                                        max_completion_tokens=self._max_tokens)
        except ChatBackendError as exc:  # fail-soft: keep the deterministic attribution
            logger.warning("attribution narrative failed for %s: %s", attribution.scenario, exc)
            return None
        text = res.content.strip()
        if not text:
            logger.warning("attribution narrative empty for %s", attribution.scenario)
            return None
        return {"text": text, "model": res.model, "cost_usd": res.cost_usd,
                "prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens,
                "latency_ms": round(res.latency_ms, 1)}

    @staticmethod
    def _facts(attr: ScenarioAttribution) -> dict[str, Any]:
        """The compact, model-facing view — the attribution minus any prior narrative."""
        d = attr.to_dict()
        d.pop("narrative", None)
        return d


def build_narrator(root: Path | None = None) -> AttributionNarrator | None:
    """Construct the narrator, or ``None`` if its provider isn't available (fail-soft).

    The model and provider come from the active profile's ``attribution`` stage.
    """
    settings = load_llm_settings(root)
    provider = settings.provider_for(_STAGE)
    try:
        backend = build_provider_backend(settings, provider)
    except ConfigError as exc:  # no key for the provider, or its backend isn't available yet
        logger.info("  narrative: %s -> deterministic attribution only", exc)
        return None
    except Exception as exc:  # provider SDK missing / construction error
        logger.warning("  narrative: %s backend unavailable (%s) -> attribution only", provider, exc)
        return None
    tracker = CostTracker(settings.pricing)
    client = InstrumentedChatClient(
        backend, tracker, max_attempts=settings.retry.max_attempts,
        min_wait_s=settings.retry.min_wait_s, max_wait_s=settings.retry.max_wait_s)
    return AttributionNarrator(client, tracker, model=settings.model_for(_STAGE),
                               max_tokens=settings.max_tokens_for(_STAGE))
