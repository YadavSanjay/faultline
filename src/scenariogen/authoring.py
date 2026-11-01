"""Scenario-set authoring (dual-RAG P4a).

The dual-RAG runtime reads a named scenario from *three* config files — `scenarios.json`
(authored baseline: drivers/overlays/narrative), `macro.json` (the MacroRAG synthesis request),
and `arag.json` (required services + ARag company Risk-Factor queries). Authoring a fresh set of
hypothetical scenarios by hand-editing three files in lockstep is error-prone, so this module
lets you author a whole set in **one** doc and expand it into the three governed configs. Once
applied, `python -m src.scenariogen.runner --dual-rag` picks the set up with no further changes.

Authoring doc shape (one entry per scenario)::

    {
      "description": "...",                      # optional, free text
      "scenarios": {
        "<name>": {
          "narrative": "...", "trigger": "...",  # -> scenarios.json
          "horizon_days": 10,
          "drivers":  {"eq:XLK": {"move_pct": -20}},   # authored systematic baseline
          "overlays": {"eqvol": {"move_pts": 8}},
          "required_services": ["macro", "arag"],       # -> arag.json (fail-loud)
          "macro": {                                    # -> macro.json (omit if macro not used)
            "synthesis_question": "...",
            "filters": {"factor_tags": [...], "entity_tags": [...]},
            "expect_fields": ["UST_10Y", "IG_OAS", ...]
          },
          "companies": [                                # -> arag.json
            {"ticker": "NVDA", "seed_move": -0.25, "narrative_query": "...",
             "fiscal_years": [2024, 2025], "calibration": {...}}
          ]
        }
      }
    }

``apply`` upserts (add/update named scenarios, keep the rest); ``--replace-set`` swaps the whole
set (the four starters out, your set in) for a clean regeneration. ``export`` round-trips the
current three configs back into one authoring doc.

CLI::

    python -m src.scenariogen.authoring apply configs/scenariogen/authoring/<set>.json
    python -m src.scenariogen.authoring apply <set>.json --replace-set
    python -m src.scenariogen.authoring export configs/scenariogen/authoring/current.json
    python -m src.scenariogen.authoring list
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scenariogen import factor_keys as fk
from src.scenariogen.config import (
    config_dir,
    load_arag_config,
    load_macro_config,
    load_scenario_config,
)

_VALID_SERVICES = {"macro", "arag"}
_SCALAR_VOL_FACTORS = {
    fk.EQUITY_VOL_KEY, fk.EQUITY_VOL_TERM_KEY, fk.EQUITY_VOL_SKEW_KEY,
    fk.RATES_VOL_KEY, fk.RATES_VOL_TERM_KEY, fk.CREDIT_KEY,
}
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Optional scenarios.json tags the runtime reads (severity anchoring, F8 geopolitical, F9 throughput,
# historical replay): carried through apply and export unchanged, so a round trip doesn't drop them.
_PASSTHROUGH_KEYS = ("severity", "geopolitical", "throughput", "replay")


class AuthoringError(ValueError):
    """One or more scenarios in an authoring doc failed validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("authoring validation failed:\n  - " + "\n  - ".join(errors))


def _valid_factor(key: str) -> bool:
    return (fk.is_ir(key) or fk.is_equity(key) or fk.is_fx(key) or fk.is_fx_rate(key)
            or fk.is_style(key) or fk.is_basis(key) or fk.is_rates_decomp(key)
            or fk.is_issuer_credit(key) or key in _SCALAR_VOL_FACTORS)


def validate(doc: dict[str, Any]) -> None:
    scenarios = doc.get("scenarios")
    if not isinstance(scenarios, dict) or not scenarios:
        raise AuthoringError(["'scenarios' must be a non-empty object"])
    errors: list[str] = []
    for name, spec in scenarios.items():
        p = f"{name}: "
        if not _NAME_RE.match(name):
            errors.append(p + "name must be snake_case (^[a-z][a-z0-9_]*$)")
        if not str(spec.get("narrative", "")).strip():
            errors.append(p + "missing narrative")
        req = [str(s).lower() for s in spec.get("required_services", [])]
        errors.extend(p + f"unknown required service {s!r} (allowed: macro, arag)"
                      for s in req if s not in _VALID_SERVICES)
        drivers = spec.get("drivers", {}) or {}
        overlays = spec.get("overlays", {}) or {}
        companies = spec.get("companies", []) or []
        replay = spec.get("replay")
        if not drivers and not companies and not replay:
            errors.append(p + "needs at least one driver, company leg or replay window")
        if replay is not None:
            if drivers:
                errors.append(p + "a replay takes its drivers from the episode; drop 'drivers'")
            if not (isinstance(replay, dict) and replay.get("start") and replay.get("end")):
                errors.append(p + "replay needs 'start' and 'end' (YYYY-MM-DD)")
            else:
                errors.extend(p + f"invalid idiosyncratic factor key {k!r}"
                              for k in (replay.get("idiosyncratic") or {}) if not _valid_factor(k))
        errors.extend(p + f"invalid factor key {k!r} (see factor_keys.py)"
                      for k in list(drivers) + list(overlays) if not _valid_factor(k))
        if "macro" in req and not str((spec.get("macro") or {}).get("synthesis_question", "")).strip():
            errors.append(p + "required_services includes 'macro' but no macro.synthesis_question")
        if "arag" in req and not companies:
            errors.append(p + "required_services includes 'arag' but no companies")
        for c in companies:
            tkr = str(c.get("ticker", "")).strip()
            if not tkr:
                errors.append(p + "a company entry is missing 'ticker'")
                continue
            # A replay seeds a company leg from the episode's observed move of that name.
            if c.get("seed_move") is None and f"eq:{tkr.upper()}" not in drivers and replay is None:
                errors.append(p + f"company {tkr} needs a seed_move or an eq:{tkr.upper()} driver")
    if errors:
        raise AuthoringError(errors)


def _scenarios_entry(spec: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "narrative": spec.get("narrative", ""),
        "trigger": spec.get("trigger", ""),
        "method": spec.get("method", "conditional_covariance"),
        "horizon_days": int(spec.get("horizon_days", 10)),
        "drivers": spec.get("drivers", {}) or {},
        "overlays": spec.get("overlays", {}) or {},
    }
    for key in _PASSTHROUGH_KEYS:
        if key in spec:
            entry[key] = spec[key]
    return entry


def _macro_entry(spec: dict[str, Any]) -> dict[str, Any]:
    m = spec["macro"]
    entry: dict[str, Any] = {"synthesis_question": m["synthesis_question"],
                             "filters": m.get("filters", {}) or {}}
    if m.get("expect_fields"):
        entry["expect_fields"] = list(m["expect_fields"])
    if m.get("directions"):                  # validated when MacroScenarioProvider is built
        entry["directions"] = dict(m["directions"])
    return entry


def _arag_entry(spec: dict[str, Any], req: list[str]) -> dict[str, Any]:
    companies = spec.get("companies", []) or []
    tickers = spec.get("expect_tickers") or [str(c.get("ticker", "")).upper() for c in companies]
    return {
        "required_services": req or ["macro"],
        "companies": companies,
        "expect_tickers": [t for t in tickers if t],
    }


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def apply_set(doc: dict[str, Any], root: Path | None = None, *,
              replace_set: bool = False) -> dict[str, Any]:
    """Expand an authoring doc into scenarios.json + macro.json + arag.json.

    ``replace_set`` swaps the entire scenario set in all three configs (clean regeneration);
    otherwise each authored scenario is upserted and its three-config presence made consistent
    (a scenario with no ``macro`` block has any stale macro entry removed). Top-level config
    blocks (severity_tiers, connection, gate, units, defaults, description) are preserved, with their
    ``${VAR:-default}`` placeholders unexpanded.
    """
    validate(doc)
    cdir = config_dir(root)
    # Raw JSON, not the env-expanded loaders: writing an expanded base_url back would freeze it.
    scen_cfg, macro_cfg, arag_cfg = (json.loads((cdir / f).read_text(encoding="utf-8"))
                                     for f in ("scenarios.json", "macro.json", "arag.json"))
    for cfg in (scen_cfg, macro_cfg, arag_cfg):
        cfg.setdefault("scenarios", {})
    if replace_set:
        scen_cfg["scenarios"], macro_cfg["scenarios"], arag_cfg["scenarios"] = {}, {}, {}

    written: list[str] = []
    for name, spec in doc["scenarios"].items():
        req = [str(s).lower() for s in spec.get("required_services", [])]
        scen_cfg["scenarios"][name] = _scenarios_entry(spec)
        arag_cfg["scenarios"][name] = _arag_entry(spec, req)
        if spec.get("macro"):
            macro_cfg["scenarios"][name] = _macro_entry(spec)
        else:
            macro_cfg["scenarios"].pop(name, None)   # keep the three configs consistent
        written.append(name)

    _write_json(cdir / "scenarios.json", scen_cfg)
    _write_json(cdir / "macro.json", macro_cfg)
    _write_json(cdir / "arag.json", arag_cfg)
    return {
        "written": written, "replace_set": replace_set,
        "n_scenarios_now": len(scen_cfg["scenarios"]),
        "scenarios_now": sorted(scen_cfg["scenarios"]),
    }


def export_current(root: Path | None = None) -> dict[str, Any]:
    """Round-trip the current three configs into one authoring doc (editable single source)."""
    scen = load_scenario_config(root).get("scenarios", {})
    macro = load_macro_config(root).get("scenarios", {})
    arag = load_arag_config(root).get("scenarios", {})
    out: dict[str, Any] = {}
    for name, s in scen.items():
        entry: dict[str, Any] = {
            "narrative": s.get("narrative", ""), "trigger": s.get("trigger", ""),
            "method": s.get("method", "conditional_covariance"),
            "horizon_days": s.get("horizon_days", 10),
            "drivers": s.get("drivers", {}), "overlays": s.get("overlays", {}),
        }
        entry.update({k: s[k] for k in _PASSTHROUGH_KEYS if k in s})
        a = arag.get(name, {})
        entry["required_services"] = a.get("required_services", ["macro"])
        if a.get("companies"):
            entry["companies"] = a["companies"]
        if a.get("expect_tickers"):
            entry["expect_tickers"] = a["expect_tickers"]
        if name in macro:
            entry["macro"] = macro[name]
        out[name] = entry
    return {"description": "Exported dual-RAG scenario set (authoring single-source).",
            "scenarios": out}


def _main() -> None:
    ap = argparse.ArgumentParser(description="dual-RAG scenario-set authoring")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply", help="expand an authoring doc into the three configs")
    a.add_argument("file")
    a.add_argument("--replace-set", action="store_true",
                   help="replace the ENTIRE scenario set (clean regenerate) instead of upsert")
    e = sub.add_parser("export", help="round-trip the current configs into one authoring doc")
    e.add_argument("file")
    sub.add_parser("list", help="list the current scenario set + required services")
    args = ap.parse_args()

    if args.cmd == "apply":
        with open(args.file, encoding="utf-8") as fh:
            doc = json.load(fh)
        try:
            summary = apply_set(doc, replace_set=args.replace_set)
        except AuthoringError as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(2) from exc
        print(f"applied {len(summary['written'])} scenario(s): {', '.join(summary['written'])}")
        print(f"scenario set now ({summary['n_scenarios_now']}): {', '.join(summary['scenarios_now'])}")
    elif args.cmd == "export":
        doc = export_current()
        out = Path(args.file)
        out.parent.mkdir(parents=True, exist_ok=True)
        _write_json(out, doc)
        print(f"exported {len(doc['scenarios'])} scenario(s) -> {out}")
    elif args.cmd == "list":
        arag = load_arag_config().get("scenarios", {})
        for name in sorted(load_scenario_config().get("scenarios", {})):
            req = arag.get(name, {}).get("required_services", [])
            n_co = len(arag.get(name, {}).get("companies", []))
            print(f"  {name:24} services={','.join(req) or '-':11} companies={n_co}")


if __name__ == "__main__":
    _main()
