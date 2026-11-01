"""The ``needs_market_data`` skip: on when there's no market data, off when it must be there."""

from __future__ import annotations

from pathlib import Path

import pytest

import market_data_marker as mdm


class _Item:
    """The two ``pytest.Item`` calls the skip uses."""

    def __init__(self, *markers: str) -> None:
        self.markers = list(markers)
        self.skips: list[str] = []

    def get_closest_marker(self, name: str) -> object | None:
        return name if name in self.markers else None

    def add_marker(self, marker: pytest.MarkDecorator) -> None:
        self.skips.append(marker.kwargs["reason"])


@pytest.fixture
def data_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty data tree, with the require switch off."""
    monkeypatch.setenv("PRICER_DATA_URI", str(tmp_path))
    monkeypatch.delenv(mdm.REQUIRE_ENV, raising=False)
    return tmp_path


def test_no_market_data_skips_only_the_marked_tests(data_tree: Path) -> None:
    marked, plain = _Item(mdm.MARKER), _Item("other")
    mdm.skip_without_market_data([marked, plain])
    assert marked.skips == [mdm.SKIP_REASON]
    assert plain.skips == []


def _ingest(data_tree: Path, feeds: tuple[str, ...]) -> None:
    for feed in feeds:
        path = data_tree / "marketdata" / feed
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("date\n", encoding="utf-8")


ALL_CORE_FEEDS = (*mdm.CORE_FILES, f"{mdm.OPTION_CHAINS}/SPY.csv")


def test_core_feeds_present_runs_the_marked_tests(data_tree: Path) -> None:
    _ingest(data_tree, ALL_CORE_FEEDS)
    marked = _Item(mdm.MARKER)
    mdm.skip_without_market_data([marked])
    assert marked.skips == []


@pytest.mark.parametrize("lost", ALL_CORE_FEEDS)
def test_a_missing_core_feed_still_skips(data_tree: Path, lost: str) -> None:
    _ingest(data_tree, tuple(feed for feed in ALL_CORE_FEEDS if feed != lost))
    marked = _Item(mdm.MARKER)
    mdm.skip_without_market_data([marked])
    assert marked.skips == [mdm.SKIP_REASON]


def test_a_single_feed_still_skips(data_tree: Path) -> None:
    _ingest(data_tree, ("bonds/treasury_par_yields.csv",))
    marked = _Item(mdm.MARKER)
    mdm.skip_without_market_data([marked])
    assert marked.skips == [mdm.SKIP_REASON]


def test_an_empty_marketdata_folder_still_skips(data_tree: Path) -> None:
    (data_tree / "marketdata").mkdir()
    marked = _Item(mdm.MARKER)
    mdm.skip_without_market_data([marked])
    assert marked.skips == [mdm.SKIP_REASON]


def test_require_switch_runs_them_without_data(data_tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(mdm.REQUIRE_ENV, "1")
    marked = _Item(mdm.MARKER)
    mdm.skip_without_market_data([marked])
    assert marked.skips == []


def test_skip_reason_points_at_a_guide_that_exists() -> None:
    root = Path(__file__).resolve().parents[2]
    guide = mdm.SKIP_REASON.split(": see ")[1]
    assert (root / guide).is_file()
