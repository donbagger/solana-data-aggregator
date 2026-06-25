"""Integration tests for the DexPaprika provider."""

from __future__ import annotations

import datetime

import pytest

from metrics.defi import Defi, DefiMetricType
from providers.dexpaprika import DexPaprika

_TODAY = datetime.date.today().isoformat()


@pytest.mark.integration
def test_get_dex_volume_live_api() -> None:
    """Calls the DexPaprika public API directly and validates response mapping."""
    provider = DexPaprika()
    metric = provider.get_metric(
        metric="defi_dex_volume",
        date=_TODAY,
        chain="solana",
    )

    assert metric is not None
    assert isinstance(metric, Defi)
    assert metric.metric_type == DefiMetricType.DEX_VOLUME
    assert metric.value > 0


@pytest.mark.integration
def test_get_dex_count_live_api() -> None:
    """DEX count for Solana should be a positive whole number."""
    provider = DexPaprika()
    metric = provider.get_metric(
        metric="defi_dex_count",
        date=_TODAY,
        chain="solana",
    )

    assert metric is not None
    assert isinstance(metric, Defi)
    assert metric.metric_type == DefiMetricType.DEX_COUNT
    assert metric.value >= 1
    assert metric.value == int(metric.value)
