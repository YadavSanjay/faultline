"""BFF sizing rebalance blotter + book-variant reads (`/sizing/rebalance/{p}`, `/sizing/{p}/variant`)."""

from __future__ import annotations

from pathlib import Path

from bff_helpers import write_active_portfolio, write_book_variant, write_sizing_report
from ui.bff.app import sizing

DATE = "2026-05-01"


def _size(tid: str, cur: float, tgt: float, *, hedge: bool = False, rc: float = 0.0, rs: float = 0.0) -> dict:
    return {
        "trade_id": tid, "asset_class": "equity", "description": f"pos {tid}",
        "is_hedge": hedge, "multiplier": (tgt / cur) if cur else 1.0,
        "current_notional": cur, "target_notional": tgt,
        "mu_annual": 0.0, "risk_contribution": rc, "risk_share": rs, "standalone_var99": 1.0,
    }


def _report() -> dict:
    return {
        "run_id": "run-sz-1", "capital_base": 1_000_000.0,
        "n_trades": 3, "n_factors": 5, "n_days": 250,
        "profiles": {
            "balanced": {
                "profile": "balanced", "label": "Balanced",
                "binding_constraint": "var_budget", "var_budget": 10_000.0,
                "achieved_var99": 8_000.0, "gross_leverage": 1.5,
                "achieved_es975": 9_000.0, "expected_annual_return": 0.0,
                "expected_return_pct": 0.0, "return_on_var": 0.0, "net_notional": 0.0,
                "portfolio_vol_annual": 0.0,
                "sizes": [
                    _size("T1", 100.0, 200.0, rs=0.5),   # +100 increase
                    _size("T2", 100.0, 50.0, rs=0.3),    # -50 decrease
                    _size("T3", 100.0, 100.0, rs=0.2),   # hold
                ],
            },
        },
    }


def test_rebalance_deltas_directions_and_summary(bff_root: Path) -> None:
    write_sizing_report(bff_root, DATE, _report())

    r = sizing.rebalance("balanced", None)
    assert r["available"] is True
    assert r["label"] == "Balanced"
    assert r["n_increase"] == 1
    assert r["n_decrease"] == 1
    assert r["n_hold"] == 1
    assert r["gross_turnover"] == 150.0  # |+100| + |-50| + 0

    # sorted by |delta| desc: T1 (100), T2 (50), T3 (0)
    ids = [t["trade_id"] for t in r["trades"]]
    assert ids == ["T1", "T2", "T3"]

    t1 = r["trades"][0]
    assert t1["direction"] == "increase"
    assert t1["delta_notional"] == 100.0
    assert t1["pct_change"] == 1.0
    assert r["trades"][1]["direction"] == "decrease"
    assert r["trades"][2]["direction"] == "hold"


def test_rebalance_unavailable_without_report(bff_root: Path) -> None:
    out = sizing.rebalance("balanced", None)
    assert out["available"] is False


def test_rebalance_unknown_profile_lists_available(bff_root: Path) -> None:
    write_sizing_report(bff_root, DATE, _report())
    out = sizing.rebalance("risky", None)
    assert out["available"] is False
    assert out["available_profiles"] == ["balanced"]


def test_book_variant_reads_file(bff_root: Path) -> None:
    write_book_variant(bff_root, "aggressive", {
        "name": "AI book (aggressive)",
        "sizing_basis": {"profile": "aggressive", "capital_base": 1_000_000.0},
        "trades": [{"trade_id": "T1"}, {"trade_id": "T2"}],
    })

    v = sizing.book_variant("aggressive")
    assert v["available"] is True
    assert v["filename"] == "portfolio_aggressive.json"
    assert v["n_trades"] == 2
    assert v["sizing_basis"]["profile"] == "aggressive"
    assert v["book"]["trades"][0]["trade_id"] == "T1"


def test_book_variant_missing(bff_root: Path) -> None:
    out = sizing.book_variant("nope")
    assert out["available"] is False


# --- active book + activate variant (gated write) -------------------------- #
def test_active_book_reads_identity(bff_root: Path) -> None:
    write_active_portfolio(bff_root, {
        "name": "Aggressive book", "sizing_basis": {"profile": "aggressive", "label": "Aggressive Growth"},
        "trades": [{"trade_id": "T1"}, {"trade_id": "T2"}],
    })
    a = sizing.active_book()
    assert a["available"] is True
    assert a["profile"] == "aggressive"
    assert a["n_trades"] == 2


def test_activate_variant_promotes_to_active(bff_root: Path) -> None:
    write_active_portfolio(bff_root, {"name": "BASE", "trades": [{"trade_id": "T1"}]})
    write_book_variant(bff_root, "risky", {
        "name": "Risky book", "sizing_basis": {"profile": "risky"},
        "trades": [{"trade_id": "T1"}, {"trade_id": "T2"}, {"trade_id": "T3"}],
    })

    out = sizing.activate_variant("risky")
    assert out["ok"] is True
    assert out["previous_profile"] is None      # base book had no sizing_basis
    assert out["n_trades"] == 3
    # the active book now reflects the promotion
    assert sizing.active_book()["profile"] == "risky"


def test_activate_variant_missing(bff_root: Path) -> None:
    assert sizing.activate_variant("nope")["ok"] is False
