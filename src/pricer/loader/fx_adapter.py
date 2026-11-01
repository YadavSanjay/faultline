"""FX market-data adapter.

Resolves the FX spot (foreign per USD) and the foreign discount rate for a
USD/foreign pair:

  * JPY -> USD/JPY spot from ECB cross (`ecb_fx_rates.csv`); foreign rate = BOJ
    TONAR (`boj_tonar.csv`).
  * TWD -> USD/TWD spot from Fed H.10 (`fed_h10_fx_rates.csv`); foreign rate =
    the CBC policy rate held as a config constant (no free TWD curve feed).

USD discounting comes from the SOFR curve. Pure pandas — no QuantLib.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.common import storage

from ..input.common.market_data import FxData
from ..input.common.quality import DataContractError
from ..input.common.valuation import ValuationContext
from .snapshot import MarketDataSnapshot, SnapshotReader
from .staleness import pick_snapshot


class FxMarketDataAdapter(SnapshotReader):
    def __init__(self, config: dict, project_root: Path,
                 snapshot: MarketDataSnapshot | None = None):
        self.cfg = config.get("fx", {})
        self.root = project_root
        self.data_dir = storage.join(storage.data_base(project_root), "marketdata", "fx")
        self._snapshot = snapshot

    def fx(self, foreign_ccy: str, ctx: ValuationContext) -> tuple[FxData, date, list[str]]:
        ccfg = self.cfg.get("currencies", {}).get(foreign_ccy)
        if ccfg is None:
            raise KeyError(f"No FX config for currency {foreign_ccy!r} (fx.currencies)")

        spot, as_of, warnings = self._latest(
            ccfg["spot_file"], ccfg["spot_col"], ctx,
            label=f"USD/{foreign_ccy} spot", kind="fx_spot")

        rate = self._foreign_rate(foreign_ccy, ccfg, ctx, warnings)
        basis_bp = float(ccfg.get("xccy_basis_bp", 0.0))   # C2: default 0 (no free basis feed)

        return (
            FxData(foreign_ccy=foreign_ccy, spot=spot, foreign_rate=rate,
                   as_of_date=as_of, source=ccfg.get("spot_file", "fx_pipeline"),
                   xccy_basis_bp=basis_bp),
            as_of, warnings,
        )

    def _foreign_rate(self, foreign_ccy: str, ccfg: dict, ctx: ValuationContext,
                      warnings: list[str]) -> float:
        if "rate_constant" in ccfg:
            return float(ccfg["rate_constant"])
        rate, _as_of, warn = self._latest(
            ccfg["rate_file"], ccfg["rate_col"], ctx,
            label=f"{foreign_ccy} rate", kind="fx_rate")
        warnings += warn
        if ccfg.get("rate_in_percent", True):
            rate /= 100.0
        return rate

    def _latest(self, filename: str, col: str, ctx: ValuationContext,
                label: str, kind: str | None = None) -> tuple[float, date, list[str]]:
        path = storage.join(self.data_dir, filename)
        if not storage.exists(path):
            raise FileNotFoundError(f"FX data not found: {path}")
        df = self._read_csv(path, parse_dates=["date"])
        if col not in df.columns:
            raise DataContractError(
                f"fx/{filename} is missing required column {col!r}; "
                f"present columns: {sorted(map(str, df.columns))}"
            )
        df = df.dropna(subset=[col])
        chosen, warnings = pick_snapshot(df["date"], ctx, label=label, kind=kind)
        row = df[df["date"] == chosen].iloc[-1]
        return float(row[col]), chosen.date(), warnings
