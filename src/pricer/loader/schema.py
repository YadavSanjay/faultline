"""Data contracts (P0c): required-column checks for market-data input files.

Validated once per file at the read chokepoint (`MarketDataSnapshot.read_csv`). A
renamed or dropped column now fails immediately with a clear `DataContractError`
naming the file and the missing column(s) — instead of an opaque KeyError deep inside
an adapter or QuantLib. Columns listed are the ones the adapters index by name; derived
columns (e.g. credit `sp`, chain `mid`) are not required of the raw file.

Keyed by posix relpath under `data/marketdata/`. Option-chain files share one contract
(matched by the `options/chains/` prefix) since they are one schema per underlier.
"""

from __future__ import annotations

import pandas as pd

from ..input.common.quality import DataContractError

_CHAINS_REQUIRED = {
    "snapshot_date", "expiry_date", "days_to_expiry", "put_call", "strike", "mid", "iv",
}

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "rates/pensford_sofr_forward.csv": {"as_of_date", "settlement_date", "forward_sofr"},
    "bonds/treasury_par_yields.csv": {
        "date", "t6mo", "t1yr", "t2yr", "t3yr", "t5yr", "t7yr", "t10yr", "t20yr", "t30yr",
    },
    "equity/prices_daily.csv": {"date", "ticker", "close"},
    "equity/dividends.csv": {"date", "ticker", "amount"},
    "credit/dtcc_single_name_cds.csv": {
        "date", "Underlying Asset Name", "UPI Underlier Name", "Expiration Date", "Spread-Leg 1",
    },
    "credit/dtcc_index_cds.csv": {
        "date", "Underlying Asset Name", "UPI Underlier Name", "Expiration Date", "Spread-Leg 1",
    },
}


def required_columns(relpath: str) -> set[str] | None:
    if relpath in REQUIRED_COLUMNS:
        return REQUIRED_COLUMNS[relpath]
    if relpath.startswith("options/chains/") and relpath.endswith(".csv"):
        return _CHAINS_REQUIRED
    return None


def validate_columns(relpath: str, df: pd.DataFrame) -> None:
    """Raise `DataContractError` if `df` is missing any required column for `relpath`.

    Files without a registered contract (e.g. the best-effort MOVE anchor, FX files
    whose value column is config-driven) are skipped.
    """
    req = required_columns(relpath)
    if not req:
        return
    missing = req - set(df.columns)
    if missing:
        raise DataContractError(
            f"{relpath} is missing required column(s) {sorted(missing)}; "
            f"present columns: {sorted(map(str, df.columns))}"
        )
