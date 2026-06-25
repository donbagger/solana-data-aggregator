"""DexPaprika data provider."""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

import requests

from metrics.defi import Defi, DefiMetricType
from providers.base import BaseProvider


class DexPaprika(BaseProvider):
    """Fetch Solana DeFi (DEX) metrics from the DexPaprika public API.

    Endpoints
    ---------
    - Networks:      /networks               (per-chain 24h aggregates: volume, transactions)
    - Network DEXes: /networks/solana/dexes  (per-DEX 24h aggregates, paginated; used to count active DEXes)

    All exposed metrics are current 24h aggregates rather than historical series,
    so ``fetch_rows`` returns a single row dated today when today falls within the
    requested range (consistent with the aggregator's other snapshot providers).

    No API key required (public REST API).
    """

    _CHAIN = "solana"

    METRIC_MAP: Dict[str, Dict[str, Any]] = {
        "defi_dex_volume": {
            "endpoint": "/networks",
            "network_field": "volume_usd_24h",
            "methodology": "Aggregate 24h USD spot trading volume across all DEXes indexed on Solana.",
            "methodology_url": "https://docs.dexpaprika.com/api-reference/networks/get-a-list-of-available-blockchain-networks",
        },
        "defi_dex_transactions": {
            "endpoint": "/networks",
            "network_field": "txns_24h",
            "methodology": "Aggregate 24h DEX transaction count (swaps and liquidity events) across Solana.",
            "methodology_url": "https://docs.dexpaprika.com/api-reference/networks/get-a-list-of-available-blockchain-networks",
        },
        "defi_dex_count": {
            "endpoint": "/networks/solana/dexes",
            "count_active_dexes": True,
            "methodology": "Number of DEXes indexed on Solana with non-zero 24h trading volume.",
            "methodology_url": "https://docs.dexpaprika.com/api-reference/dexes/get-a-list-of-available-dexes-on-a-network",
        },
    }

    _DEFI_METRIC_TYPE_MAP: Dict[str, DefiMetricType] = {
        "defi_dex_volume": DefiMetricType.DEX_VOLUME,
        "defi_dex_transactions": DefiMetricType.DEX_TRANSACTIONS,
        "defi_dex_count": DefiMetricType.DEX_COUNT,
    }

    BASE_URL = "https://api.dexpaprika.com"
    _PAGE_LIMIT = 100
    _MAX_PAGES = 50  # safety bound for DEX pagination

    def __init__(self) -> None:
        super().__init__(
            name="DexPaprika",
            base_url=self.BASE_URL,
            api_key="",
        )
        self._session = requests.Session()

    # -- private helpers ----------------------------------------------------

    def _get(self, endpoint: str, *, params: Optional[Dict[str, Any]] = None) -> Any:
        resp = self._session.get(
            f"{self.base_url}{endpoint}", params=params or {}, timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    def _network_field(self, field: str) -> Optional[float]:
        """Return a 24h aggregate field for the target chain from /networks."""
        networks = self._get("/networks")
        for network in networks if isinstance(networks, list) else []:
            if network.get("id") == self._CHAIN:
                value = network.get(field)
                return None if value is None else float(value)
        return None

    def _active_dex_count(self, endpoint: str) -> int:
        """Count DEXes on the chain with non-zero 24h volume, across all pages."""
        count = 0
        for page in range(1, self._MAX_PAGES + 1):
            payload = self._get(
                endpoint, params={"page": page, "limit": self._PAGE_LIMIT}
            )
            dexes = payload.get("dexes", []) if isinstance(payload, dict) else []
            if not dexes:
                break
            count += sum(1 for dex in dexes if (dex.get("volume_usd_24h") or 0) > 0)
            if len(dexes) < self._PAGE_LIMIT:
                break
        return count

    # -- BaseProvider interface ---------------------------------------------

    def fetch_rows(
        self, metric: str, start_date: str, end_date: str, **kwargs: Any
    ) -> List[Dict[str, Any]]:
        """Return normalized {"date": str, "value": float} records for the given range (both dates inclusive).

        Note: DexPaprika exposes current 24h aggregates, not historical series, so
        this returns a single row dated today when today falls within the range.
        """
        config = self.METRIC_MAP.get(metric)
        if config is None:
            available = ", ".join(self.METRIC_MAP)
            raise ValueError(f"Unknown metric '{metric}'. Available: {available}")

        today = datetime.date.today().isoformat()
        if not (start_date <= today <= end_date):
            return []

        if config.get("count_active_dexes"):
            value: Optional[float] = float(self._active_dex_count(config["endpoint"]))
        else:
            value = self._network_field(config["network_field"])

        if value is None:
            return []
        return [{"date": today, "value": value}]

    def get_metric(self, metric: str, date: str, chain: str) -> Defi | None:
        """Fetch one metric for one date and return it as a typed Defi model."""
        rows = self.fetch_rows(metric, date, date)
        if not rows:
            return None

        metric_type = self._DEFI_METRIC_TYPE_MAP.get(metric)
        if metric_type is None:
            return None

        return Defi.from_metric_type(
            metric_type=metric_type,
            date=datetime.date.fromisoformat(date),
            value=rows[0]["value"],
        )
