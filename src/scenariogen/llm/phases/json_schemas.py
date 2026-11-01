"""JSON schemas for the structured-output stages (propose, verify) — design D7.

A model whose registry `json_mode` is `json_schema` (the Claude models) gets its output constrained
to these schemas; a `json_object` model (OpenAI, DeepSeek) gets plain JSON mode and the prompt's
example shape, as before. The schemas mirror the shapes the prompts ask for and that
`extract_json_object` + the phase parsers read. Structured outputs need every object closed
(`additionalProperties: false`) with every property required.
"""

from __future__ import annotations

from typing import Any

from ..config import LLMSettings


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}

PROPOSE_SCHEMA: dict[str, Any] = _obj({
    "scenarios": {"type": "array", "items": _obj({
        "name": _STR, "theme": _STR, "narrative": _STR, "trigger": _STR, "method": _STR,
        "horizon_days": {"type": "integer"}, "driver_factors": _STRS, "exposed_trades": _STRS,
        "rationale": _STR,
    })},
})

VERIFY_SCHEMA: dict[str, Any] = _obj({
    "verdicts": {"type": "array", "items": _obj({
        "factor": _STR, "verdict": {"type": "string", "enum": ["SUPPORTED", "UNSUPPORTED"]}, "reason": _STR,
    })},
})


def response_format_for(settings: LLMSettings, stage: str, name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """The `response_format` for a structured stage, by its model's registry `json_mode`.

    Args:
        settings: The loaded LLM config.
        stage: The flow stage (`propose` or `verify`).
        name: The schema's name, as OpenAI-style `json_schema` formats carry one.
        schema: The stage's JSON schema.

    Returns:
        ``{"type": "json_schema", "json_schema": {"name", "schema", "strict": True}}`` for a
        `json_schema` model, else ``{"type": "json_object"}``.
    """
    if settings.json_mode_for(stage) == "json_schema":
        return {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}
    return {"type": "json_object"}
