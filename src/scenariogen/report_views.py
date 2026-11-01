# Pure views over stress, ablation and measurement (coverage, decision impact, event replay) run
# reports for the BFF and UI.
#
# Picks a report from a date's run reports, builds the per-scenario ablation waterfall (authored ->
# + MacroRAG -> + XbrlRag -> + key events -> + market intelligence -> final, with the grouped Shapley
# value beside each step), the signal-health view, and the drivers and horizon to show for a scenario
# tier (resolving replays from the report or the version store); and the coverage, decision-impact and
# event-replay views of the Measurement page.
#
# The module imports only the standard library and ``src.scenariogen.ablation`` so the BFF image stays
# QuantLib-free.

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from src.scenariogen.ablation import LAYERS

# Waterfall steps (id, table layers) in table order: the section 7.1 sources table.
WATERFALL_STEPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("macrorag", ("macrorag",)),
    ("xbrlrag", ("xbrlrag_narrative", "xbrlrag_facts")),
    ("events", ("news",)),
    ("market_intelligence", ("crowding", "state")),
)

# Unit keys of a driver / overlay spec, read in this order (first present wins): (key, factor, divide).
# The rule of ``scenario_gen.to_canonical``, restated because that module pulls in QuantLib.
_UNIT_KEYS: tuple[tuple[str, float, bool], ...] = (
    ("move_bp", 1e-4, False),
    ("move_pct", 100.0, True),
    ("move_pts", 100.0, True),
    ("move_rel", 1.0, False),
    ("move", 1.0, False),
)

# Books shown first, in this order, by the decision view; any other book follows by name.
STANDARD_BOOKS: tuple[str, ...] = ("balanced", "core", "aggressive", "risky")

# Keys of one coverage direction kept by the coverage view (``variance``, ``book_exposure`` are left out).
_DIRECTION_KEYS: tuple[str, ...] = ("index", "variance_share", "spanned_share", "uncovered", "top_loadings")


def select_report(reports: Sequence[Mapping[str, Any]], *, run_id: str | None = None,
                  require: str | None = None) -> Mapping[str, Any] | None:
    """Picks one report from a list of decoded run reports.

    Args:
        reports: Decoded stress or ablation report dicts.
        run_id: When given, the ``run_id`` of the report to return.
        require: When given (and ``run_id`` is not), only reports containing this key qualify.

    Returns:
        The report with ``run_id`` (or ``None`` when absent); otherwise the qualifying report with
        the latest ``generated_at`` (undated reports rank lowest, ties to the greatest ``run_id``),
        or ``None`` when none qualifies.

    Raises:
        ValueError: Two reports share a non-empty ``run_id``, or a ``generated_at`` is not a valid
            ISO string.
    """
    seen: set[str] = set()
    for r in reports:
        rid = r.get("run_id")
        if rid:
            if rid in seen:
                raise ValueError(f"duplicate run_id {rid!r} among the reports")
            seen.add(rid)
    if run_id is not None:
        return next((r for r in reports if r.get("run_id") == run_id), None)

    best: Mapping[str, Any] | None = None
    best_key: tuple[datetime | None, str] | None = None
    for r in reports:
        if require is not None and require not in r:
            continue
        stamp = r.get("generated_at")
        parsed: datetime | None = None
        if stamp is not None:
            try:
                parsed = datetime.fromisoformat(stamp)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"report {r.get('run_id')!r} has an invalid generated_at {stamp!r}") from exc
        key = (parsed, r.get("run_id") or "")
        if best_key is None or _rank_gt(key, best_key):
            best, best_key = r, key
    return best


def waterfall(record: Mapping[str, Any],
              cost_rows: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Builds the waterfall of one ablation scenario tier.

    Args:
        record: One entry of an ablation report's ``"scenarios"``.
        cost_rows: The report's ``layer_cost["rows"]``, or ``None`` when the report has none.

    Returns:
        ``{"scenario", "severity", "composition_rule", "authored_pnl", "final_pnl",
        "authored_floor_hits", "steps"}`` with one step per waterfall group (ladder bars, grouped
        Shapley, floor hits and calls / latency / cost per step).

    Raises:
        ValueError: An empty ladder or one not starting at ``"authored"``, a ladder that does not
            follow ``order``, an unknown or repeated layer in ``order``, or a step whose present
            layers are not consecutive in ``order``.
    """
    ladder = record["ladder"]
    order = list(record["order"])
    if not ladder or ladder[0].get("layer") != "authored":
        raise ValueError(f"ladder of {record.get('scenario')!r} must start at the 'authored' entry")
    seen: set[str] = set()
    for layer in order:
        if layer not in LAYERS:
            raise ValueError(f"unknown layer id {layer!r} in order; expected ids from {list(LAYERS)}")
        if layer in seen:
            raise ValueError(f"layer id {layer!r} repeated in order")
        seen.add(layer)
    if [e.get("layer") for e in ladder[1:]] != order:
        raise ValueError(f"ladder layers of {record.get('scenario')!r} do not follow order {order}")

    totals = [float(e["total_pnl"]) for e in ladder]
    authored_pnl, final_pnl = totals[0], totals[-1]
    shapley: Mapping[str, float] = record.get("shapley") or {}
    subsets: Sequence[Mapping[str, Any]] | None = record.get("subsets")

    def floor_hits_for(prefix: list[str]) -> list[str]:
        if subsets is None:
            return []
        target = sorted(prefix)
        for s in subsets:
            if sorted(s.get("layers", [])) == target:
                return sorted(s.get("floor_hits", []))
        return []

    present_steps: list[tuple[int, dict[str, Any]]] = []
    empty_steps: list[dict[str, Any]] = []
    for step_id, table_layers in WATERFALL_STEPS:
        positions = [i for i, layer in enumerate(order) if layer in table_layers]
        if positions and positions[-1] - positions[0] != len(positions) - 1:
            raise ValueError(f"step {step_id!r} layers are not consecutive in order {order}")
        if positions:
            first, last = positions[0], positions[-1]
            # ladder[k + 1] is the entry after order[k], so ladder[first] is the one before order[first].
            before, after = totals[first], totals[last + 1]
            hits = floor_hits_for(order[: last + 1])
        else:
            before = after = final_pnl
            hits = []
        step_shapley = float(sum(float(shapley.get(layer, 0.0)) for layer in table_layers))
        step: dict[str, Any] = {"step": step_id, "layers": [order[i] for i in positions], "pnl_before": before,
                                "pnl_after": after, "delta": after - before, "shapley": step_shapley,
                                "floor_hits": hits}
        step.update(_step_cost(record, table_layers, step_shapley, cost_rows))
        if positions:
            present_steps.append((positions[0], step))
        else:
            empty_steps.append(step)
    present_steps.sort(key=lambda p: p[0])

    return {"scenario": record.get("scenario"), "severity": record.get("severity"),
            "composition_rule": record.get("composition_rule"), "authored_pnl": authored_pnl,
            "final_pnl": final_pnl, "authored_floor_hits": floor_hits_for([]),
            "steps": [s for _, s in present_steps] + empty_steps}


def ablation_view(report: Mapping[str, Any]) -> dict[str, Any]:
    """Builds the view of one ablation report.

    Args:
        report: A decoded ablation report.

    Returns:
        ``{"run_id", "valuation_date", "generated_at", "severity", "note", "composition_rules",
        "mixed_rules", "scenarios", "stopped", "overhead"}`` with one waterfall per scenario tier.

    Raises:
        ValueError: Two scenarios share ``(scenario, severity)``, or a scenario's waterfall is invalid.
    """
    layer_cost = report.get("layer_cost")
    cost_rows = layer_cost["rows"] if layer_cost is not None else None
    seen: set[tuple[Any, Any]] = set()
    scenarios: list[dict[str, Any]] = []
    for rec in report.get("scenarios", []):
        key = (rec.get("scenario"), rec.get("severity"))
        if key in seen:
            raise ValueError(f"duplicate scenario tier {key} in the ablation report")
        seen.add(key)
        scenarios.append(waterfall(rec, cost_rows))
    rules = sorted({str(w["composition_rule"]) for w in scenarios if w["composition_rule"] is not None})
    return {"run_id": report.get("run_id"), "valuation_date": report.get("valuation_date"),
            "generated_at": report.get("generated_at"), "severity": report.get("severity"),
            "note": report.get("note"), "composition_rules": rules, "mixed_rules": len(rules) >= 2,
            "scenarios": scenarios, "stopped": copy.deepcopy(list(report.get("stopped", []))),
            "overhead": copy.deepcopy(layer_cost.get("overhead")) if layer_cost is not None else None}


def signal_health_view(report: Mapping[str, Any]) -> dict[str, Any]:
    """Builds the signal-health view of one stress report.

    Args:
        report: A decoded stress report.

    Returns:
        ``{"available", "run_id", "valuation_date", "generated_at", "overall", "counts", "sources",
        "held_out", "stopped", "attention"}``.
    """
    health = report.get("signal_health")
    sources: list[dict[str, Any]] = []
    if health is not None:
        for s in health.get("sources", []):
            src = copy.deepcopy(dict(s))
            src["n_holds"] = int(sum((s.get("holds") or {}).values()))
            sources.append(src)
    held_out: list[dict[str, Any]] = []
    for e in report.get("held_out", []):
        item = copy.deepcopy(dict(e))
        findings = e.get("findings") or []
        item["n_findings"] = len(findings)
        item["n_fail"] = sum(1 for f in findings if f.get("severity") == "fail")
        item["n_flag"] = sum(1 for f in findings if f.get("severity") == "flag")
        held_out.append(item)
    stopped = copy.deepcopy(list(report.get("stopped", [])))
    attention = sum(1 for s in sources if s.get("status") in ("down", "degraded")) + len(held_out) + len(stopped)
    return {"available": health is not None, "run_id": report.get("run_id"),
            "valuation_date": report.get("valuation_date"), "generated_at": report.get("generated_at"),
            "overall": copy.deepcopy(health.get("overall")) if health is not None else None,
            "counts": copy.deepcopy(health.get("counts")) if health is not None else None,
            "sources": sources, "held_out": held_out, "stopped": stopped, "attention": attention}


def definition_view(raw_spec: Mapping[str, Any], *, entry: Mapping[str, Any] | None = None,
                    stored_spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Returns the drivers and horizon to show for a scenario tier.

    Args:
        raw_spec: The tier's ``scenarios.json`` entry.
        entry: The stress-report entry for the tier, or ``None``.
        stored_spec: The version-store spec the runner registered, or ``None``.

    Returns:
        ``{"source", "drivers", "overlays", "horizon_days", "severity_scale", "scaled", "replay"}``
        where ``source`` is ``"report"``, ``"version"``, ``"config"`` or ``"unresolved"``.

    Raises:
        ValueError: A driver or overlay mapping has no recognized unit key.
    """
    provenance = ((entry or {}).get("materialized") or {}).get("provenance") or {}
    if provenance.get("drivers"):
        replay = provenance.get("replay")
        return {"source": "report", "drivers": copy.deepcopy(dict(provenance["drivers"])),
                "overlays": copy.deepcopy(dict(provenance.get("overlays") or {})),
                "horizon_days": provenance.get("horizon_days"),
                "severity_scale": provenance.get("severity_scale"), "scaled": True,
                "replay": copy.deepcopy(dict(replay)) if replay is not None else None}
    spec: Mapping[str, Any]
    if stored_spec is not None and stored_spec.get("drivers"):
        source, spec, horizon = "version", stored_spec, stored_spec.get("horizon_days")
    elif "replay" not in raw_spec:
        source, spec, horizon = "config", raw_spec, raw_spec.get("horizon_days")
    else:
        source, spec, horizon = "unresolved", raw_spec, None
    drivers = {} if source == "unresolved" else _canonical_moves(spec.get("drivers") or {})
    window = spec.get("replay")
    return {"source": source, "drivers": drivers, "overlays": _canonical_moves(spec.get("overlays") or {}),
            "horizon_days": horizon, "severity_scale": None, "scaled": False,
            "replay": {"start": str(window["start"]), "end": str(window["end"])} if window is not None else None}


def _rank_gt(a: tuple[datetime | None, str], b: tuple[datetime | None, str]) -> bool:
    """True when rank key ``a`` beats ``b``: dated over undated, then the later stamp, then the greater run_id."""
    if (a[0] is None) != (b[0] is None):
        return a[0] is not None
    if a[0] is not None and b[0] is not None and a[0] != b[0]:
        return a[0] > b[0]
    return a[1] > b[1]


def _step_cost(record: Mapping[str, Any], table_layers: tuple[str, ...], step_shapley: float,
               cost_rows: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """Calls, latency, cost and contribution ratios of one waterfall step from the layer-cost rows."""
    if cost_rows is None:
        return {"n_calls": None, "latency_s": None, "cost_usd": None, "contribution_per_s": None,
                "contribution_per_usd": None}
    rows = [r for r in cost_rows if r.get("scenario") == record.get("scenario")
            and r.get("severity") == record.get("severity") and r.get("layer") in table_layers]
    n_calls = sum(int(r.get("n_calls") or 0) for r in rows)
    latency_s = float(sum(float(r.get("latency_s") or 0.0) for r in rows))
    costs = [float(r["cost_usd"]) for r in rows if r.get("cost_usd") is not None]
    cost_usd = float(sum(costs)) if costs else None
    contribution = abs(step_shapley)
    return {"n_calls": n_calls, "latency_s": latency_s, "cost_usd": cost_usd,
            "contribution_per_s": contribution / latency_s if latency_s > 0 else None,
            "contribution_per_usd": contribution / cost_usd if cost_usd is not None and cost_usd > 0 else None}


def _canonical_moves(spec: Mapping[str, Any]) -> dict[str, float]:
    """Converts a drivers / overlays mapping to canonical decimal moves (``to_canonical``'s unit rule)."""
    out: dict[str, float] = {}
    for key, value in spec.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = float(value)
            continue
        for unit, factor, divide in _UNIT_KEYS:
            if unit in value:
                move = float(value[unit])
                out[key] = move / factor if divide else move * factor
                break
        else:
            raise ValueError(f"driver/overlay for {key!r} has no recognized unit key: {dict(value)}")
    return out


def coverage_view(report: Mapping[str, Any]) -> dict[str, Any]:
    """Builds the view of one scenario-set coverage report.

    Args:
        report: A decoded coverage report (``coverage_runner``).

    Returns:
        ``{"coverage", "n_directions", "uncovered_share", "directions", "diversity", "losses",
        "merge_pairs", "missing"}``: directions in ``index`` order, ``uncovered_share`` the summed
        ``variance_share`` of the uncovered directions, and the scenario losses ordered by loss
        descending, then by scenario name.

    Raises:
        ValueError: ``coverage`` or ``directions`` is missing, or two directions share an ``index``.
    """
    for key in ("coverage", "directions"):
        if key not in report:
            raise ValueError(f"coverage report has no {key!r}")
    seen: set[Any] = set()
    directions: list[dict[str, Any]] = []
    for d in report["directions"]:
        idx = d.get("index")
        if idx in seen:
            raise ValueError(f"duplicate direction index {idx!r} in the coverage report")
        seen.add(idx)
        directions.append({k: copy.deepcopy(d.get(k)) for k in _DIRECTION_KEYS})
    directions.sort(key=lambda d: d["index"])
    uncovered_share = float(sum(float(d["variance_share"]) for d in directions if d["uncovered"]))
    diversity = report.get("diversity") or {}
    losses = [{"scenario": name, "loss": loss} for name, loss in (diversity.get("losses") or {}).items()]
    losses.sort(key=lambda e: (-e["loss"], e["scenario"]))
    return {"coverage": report["coverage"], "n_directions": report.get("n_directions"),
            "uncovered_share": uncovered_share, "directions": directions,
            "diversity": {k: copy.deepcopy(diversity.get(k)) for k in ("participation_ratio", "target", "in_target")},
            "losses": losses, "merge_pairs": copy.deepcopy(list(report.get("merge_pairs", []))),
            "missing": copy.deepcopy(list(report.get("missing", [])))}


def decision_view(report: Mapping[str, Any]) -> dict[str, Any]:
    """Builds the view of one decision-impact report.

    Args:
        report: A decoded decision-impact report (``decision_impact_runner``).

    Returns:
        ``{"tier", "books", "without"}``: one row per book of the stress set (standard book order,
        then by name) and one row per source of ``without_each_source`` with the binding change and
        the mean size ratio per book.

    Raises:
        ValueError: The report has more than one set with ``kind == "stress"``.
    """
    stress_sets = [s for s in report.get("sets", []) if s.get("kind") == "stress"]
    if len(stress_sets) > 1:
        raise ValueError(f"decision report has {len(stress_sets)} stress sets; expected at most one")
    books: list[dict[str, Any]] = []
    if stress_sets:
        profiles = stress_sets[0].get("profiles") or {}
        for name in _book_order(profiles):
            p = profiles[name]
            unconstrained = p.get("unconstrained") or {}
            change = p.get("change") or {}
            binding = change.get("binding") or {}
            books.append({"book": name, "stress_budget": p.get("stress_budget"),
                          "worst_scenario": unconstrained.get("worst_scenario"),
                          "worst_loss": unconstrained.get("worst_loss"),
                          "binding_from": binding.get("from"), "binding_to": binding.get("to"),
                          "n_over_budget": len(unconstrained.get("over_budget") or []),
                          "n_trades_changed": change.get("n_trades_changed")})
    without: list[dict[str, Any]] = []
    for source, by_book in (report.get("without_each_source") or {}).items():
        rows: list[dict[str, Any]] = []
        for name in _book_order(by_book):
            cmp = by_book[name]
            binding = cmp.get("binding") or {}
            ratios = [float(m["ratio"]) for m in (cmp.get("multipliers") or {}).values()
                      if m.get("ratio") is not None]
            rows.append({"book": name, "binding_from": binding.get("from"), "binding_to": binding.get("to"),
                         "binding_changed": binding.get("from") != binding.get("to"),
                         "size_ratio": sum(ratios) / len(ratios) if ratios else 1.0})
        without.append({"source": source, "books": rows})
    return {"tier": report.get("tier"), "books": books, "without": without}


def replay_view(report: Mapping[str, Any]) -> dict[str, Any]:
    """Builds the view of one event-replay report.

    Args:
        report: A decoded event-replay report (``event_replay_runner``).

    Returns:
        ``{"limitations", "episodes"}`` with one row per episode in report order: the authored and
        enriched nearest scenarios with their cosines, the gain between them, availability, dropped
        scenarios and the point-in-time notes.
    """
    episodes: list[dict[str, Any]] = []
    for ep in report.get("episodes", []):
        versions = ep.get("versions") or {}
        authored = _nearest(versions.get("authored") or {})
        enriched = _nearest(versions.get("enriched") or {})
        a_cos, e_cos = authored["cosine"], enriched["cosine"]
        pit = ep.get("pit") or {}
        episodes.append({
            "name": ep.get("name"), "as_of": ep.get("as_of"), "horizon_days": ep.get("horizon_days"),
            "authored": authored, "enriched": enriched,
            "gain": float(e_cos) - float(a_cos) if a_cos is not None and e_cos is not None else None,
            "available": copy.deepcopy(ep.get("available")),
            "dropped": [d.get("scenario") for d in ep.get("dropped") or []],
            "regime_asof": pit.get("regime_asof"),
            "dropped_issuer_factors": copy.deepcopy(list(pit.get("dropped_issuer_factors") or []))})
    return {"limitations": copy.deepcopy(list(report.get("limitations", []))), "episodes": episodes}


def _book_order(books: Mapping[str, Any]) -> list[str]:
    """Book names in the standard order (balanced, core, aggressive, risky), then the others by name."""
    standard = [b for b in STANDARD_BOOKS if b in books]
    return standard + sorted(b for b in books if b not in STANDARD_BOOKS)


def _nearest(block: Mapping[str, Any]) -> dict[str, Any]:
    """``{"nearest", "cosine"}`` of one replay version block (cosine ``None`` when unscored)."""
    nearest = block.get("nearest")
    score = (block.get("scores") or {}).get(nearest) if nearest is not None else None
    cosine = score.get("cosine") if score is not None else None
    return {"nearest": nearest, "cosine": cosine}
