"""
S&P DJI CDX.NA.IG Index Annex downloader — semi-annual composition reference.

Downloads the current on-the-run CDX.NA.IG Index Annex PDF (125 reference
entities + RED codes + sub-index classification + ~0.8% equal weightings):

  https://www.spglobal.com/spdji/en/documents/index-news-and-announcements/
      CDX.NA.IG.{series}-V1%20Index%20Annex.pdf

The series number increments at each roll (Mar 20 / Sep 20); set `current_series`
in config. NOT required for QL pricing of trade #30 — the index is priced off the
DTCC index-implied flat hazard curve (see dtcc_index source). The annex is used
only for (1) verifying ORCL/META overlap with the index and (2) sub-index sector
tilt in AI-capex stress decomposition.

S&P Global's CDN (Akamai bot protection) returns HTTP 403 to automated requests
even with a real browser User-Agent, so this source is disabled by default and
the download is handled gracefully: a block logs a clear manual-download
instruction rather than failing the pipeline. The PDF is stored raw (not parsed —
the 125-name table layout is brittle and the data is reference-only).

Output: data/marketdata/credit/cdx_annex/CDX.NA.IG.{series}_Index_Annex.pdf
"""

import logging

from src.common import storage

from ..common.data_store import get_output_path
from ..common.http_client import HttpClient

logger = logging.getLogger(__name__)


class CDXAnnexDownloader:
    """Download the on-the-run CDX.NA.IG Index Annex PDF from S&P DJI."""

    def __init__(self, config: dict, credentials: dict[str, str]):
        src_cfg = config["sources"]["cdx_annex"]
        self._cfg = src_cfg
        self._asset_class = config["asset_class"]
        self._client = HttpClient(src_cfg, credentials)
        self._series = src_cfg["current_series"]
        self._tpl = src_cfg["endpoint_template"]
        self._out_dir = src_cfg["output_dir"]
        self._file_tpl = src_cfg["output_file_template"]

    def run(self) -> None:
        endpoint = self._tpl.format(series=self._series)
        out_path = get_output_path(
            self._asset_class,
            f"{self._out_dir}/{self._file_tpl.format(series=self._series)}",
        )
        manual_url = f"{self._cfg['base_url']}{endpoint}".replace("%20", " ")

        try:
            resp = self._client.get(endpoint)
        except Exception as exc:
            logger.warning(
                "CDX annex S.%s: download blocked (%s). S&P's CDN blocks automation — "
                "download manually in a browser:\n  %s\n  -> save to %s",
                self._series, exc, manual_url, out_path,
            )
            return

        content = resp.content
        if content[:4] != b"%PDF":
            logger.warning(
                "CDX annex S.%s: response is not a PDF (%d bytes, likely a CDN block page). "
                "Download manually:\n  %s\n  -> save to %s",
                self._series, len(content), manual_url, out_path,
            )
            return

        storage.write_bytes_atomic(str(out_path), content)
        logger.info("CDX annex S.%s: %d bytes -> %s", self._series, len(content), out_path)
