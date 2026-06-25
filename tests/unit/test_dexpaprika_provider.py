"""Unit tests for the DexPaprika provider."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

from metrics.defi import Defi, DefiMetricType
from metrics.overview import Overview, OverviewMetricType
from providers.dexpaprika import DexPaprika

_TODAY = datetime.date.today().isoformat()

_NETWORKS_RAW = [
    {"id": "ethereum", "volume_usd_24h": 1_000_000.0, "txns_24h": 50_000},
    {"id": "solana", "volume_usd_24h": 5_000_000_000.0, "txns_24h": 28_000_000},
]

_DEXES_RAW = {
    "dexes": [
        {"dex_id": "manifest", "volume_usd_24h": 214_000_000.0},
        {"dex_id": "orca", "volume_usd_24h": 90_000_000.0},
        {"dex_id": "dead-dex", "volume_usd_24h": 0},
    ],
    "page_info": {"limit": 100, "page": 1},
}

_SOL_TOKEN_RAW = {
    "id": "So11111111111111111111111111111111111111112",
    "name": "Wrapped SOL",
    "summary": {"price_usd": 68.42, "liquidity_usd": 1_200_000_000.0},
}


def _make_mock_resp(payload):
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def test_get_dex_volume_returns_defi_metric() -> None:
    provider = DexPaprika()
    sentinel_metric = object()

    with (
        patch.object(
            provider._session, "get", return_value=_make_mock_resp(_NETWORKS_RAW)
        ),
        patch.object(
            Defi, "from_metric_type", return_value=sentinel_metric
        ) as mock_factory,
    ):
        result = provider.get_metric("defi_dex_volume", _TODAY, "solana")

    assert result is sentinel_metric
    mock_factory.assert_called_once()
    assert mock_factory.call_args.kwargs["metric_type"] == DefiMetricType.DEX_VOLUME
    assert mock_factory.call_args.kwargs["value"] == 5_000_000_000.0


def test_fetch_rows_dex_transactions_picks_solana() -> None:
    provider = DexPaprika()
    with patch.object(
        provider._session, "get", return_value=_make_mock_resp(_NETWORKS_RAW)
    ):
        rows = provider.fetch_rows("defi_dex_transactions", _TODAY, _TODAY)

    assert rows == [{"date": _TODAY, "value": 28_000_000.0}]


def test_fetch_rows_dex_count_counts_only_active() -> None:
    provider = DexPaprika()
    with patch.object(
        provider._session, "get", return_value=_make_mock_resp(_DEXES_RAW)
    ):
        rows = provider.fetch_rows("defi_dex_count", _TODAY, _TODAY)

    # 2 of 3 DEXes have non-zero 24h volume
    assert rows == [{"date": _TODAY, "value": 2.0}]


def test_get_sol_price_returns_overview_metric() -> None:
    provider = DexPaprika()
    sentinel_metric = object()

    with (
        patch.object(
            provider._session, "get", return_value=_make_mock_resp(_SOL_TOKEN_RAW)
        ),
        patch.object(
            Overview, "from_metric_type", return_value=sentinel_metric
        ) as mock_factory,
    ):
        result = provider.get_metric("overview_sol_price", _TODAY, "solana")

    assert result is sentinel_metric
    mock_factory.assert_called_once()
    assert mock_factory.call_args.kwargs["metric_type"] == OverviewMetricType.SOL_PRICE
    assert mock_factory.call_args.kwargs["value"] == 68.42


def test_fetch_rows_returns_empty_when_today_out_of_range() -> None:
    provider = DexPaprika()
    # No HTTP call should be needed; range is entirely in the past.
    rows = provider.fetch_rows("defi_dex_volume", "2024-01-01", "2024-01-02")
    assert rows == []


def test_fetch_rows_raises_on_unknown_metric() -> None:
    provider = DexPaprika()
    try:
        provider.fetch_rows("nonexistent_metric", _TODAY, _TODAY)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "nonexistent_metric" in str(exc)


def test_get_metric_returns_none_when_no_rows() -> None:
    provider = DexPaprika()
    # Past range -> fetch_rows returns [] -> get_metric returns None.
    assert provider.get_metric("defi_dex_volume", "2024-01-01", "solana") is None
