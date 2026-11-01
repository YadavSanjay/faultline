"""Robust JSON extraction from LLM output (P2).

Models sometimes wrap JSON in prose or a ```json code fence. `extract_json_object` strips a
leading fence and pulls the outermost ``{...}`` so structured parsing tolerates that.
"""

from __future__ import annotations

import json
from typing import Any, cast


def extract_json_object(text: str) -> dict[str, Any]:
    body = text.strip()
    if body.startswith("```"):
        body = body[3:]
        if body[:4].lower() == "json":
            body = body[4:]
        if "```" in body:
            body = body[: body.rindex("```")]
        body = body.strip()
    start = body.find("{")
    if start == -1:
        raise ValueError("no JSON object found in model output")
    # raw_decode parses the first complete JSON value and ignores any trailing prose the model
    # may append (the naive first-{ to last-} slice chokes on that with "Extra data").
    try:
        obj, _ = json.JSONDecoder().raw_decode(body[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"could not parse JSON object from model output: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("model output is JSON but not an object")
    return cast("dict[str, Any]", obj)
