"""DexPaprika data provider."""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from metrics.defi import Defi, DefiMetricType
from metrics.overview import Overview, OverviewMetricType
from providers.base import BaseProvider


class DexPaprika(BaseProvider):
    """Fetch Solana DeFi (DEX) metrics from the DexPaprika public API.

    Endpoints
    ---------
    - Networks:      /networks               (per-chain 24h aggregates: volume, transactions)
    - Network DEXes: /networks/solana/dexes  (per-DEX 24h aggregates; used to count active DEXes)
    - Token latest:  /networks/solana/tokens/<mint>  (per-token summary; used for SOL price)

    All exposed metrics are current snapshots rather than historical series,
    so ``fetch_rows`` returns a single row dated today when today falls within the
    requested range (consistent with the aggregator's other snapshot providers).

    The session retries idempotent GETs with capped exponential backoff + jitter
    and honors ``Retry-After`` on 429/5xx, because the public API is rate-limited.

    No API key required (public REST API).
    """

    _CHAIN = "solana"
    BASE_URL = "https://api.dexpaprika.com"
    # Wrapped SOL mint — DexPaprika prices native SOL via the wSOL token.
    _WSOL_MINT = "So11111111111111111111111111111111111111112"

    # (connect, read) timeouts; requests has no default and would otherwise hang.
    _TIMEOUT = (5, 30)
    _DEX_PAGE_LIMIT = 100

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
        "overview_sol_price": {
            "endpoint": f"/networks/solana/tokens/{_WSOL_MINT}",
            "summary_field": "price_usd",
            "methodology": "Spot USD price of SOL (wrapped SOL), volume-weighted across Solana DEX pools.",
            "methodology_url": "https://docs.dexpaprika.com/api-reference/tokens/get-a-tokens-latest-data-on-a-network",
        },
    }

    _DEFI_METRIC_TYPE_MAP: Dict[str, DefiMetricType] = {
        "defi_dex_volume": DefiMetricType.DEX_VOLUME,
        "defi_dex_transactions": DefiMetricType.DEX_TRANSACTIONS,
        "defi_dex_count": DefiMetricType.DEX_COUNT,
    }

    _OVERVIEW_METRIC_TYPE_MAP: Dict[str, OverviewMetricType] = {
        "overview_sol_price": OverviewMetricType.SOL_PRICE,
    }

    def __init__(self) -> None:
        super().__init__(
            name="DexPaprika",
            base_url=self.BASE_URL,
            api_key="",
        )
        self._session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            backoff_jitter=0.5,
            status_forcelist=(408, 429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    # -- private helpers ----------------------------------------------------

    def _get(self, endpoint: str, *, params: Optional[Dict[str, Any]] = None) -> Any:
        resp = self._session.get(
            f"{self.base_url}{endpoint}", params=params or {}, timeout=self._TIMEOUT
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

    def _token_summary_field(self, endpoint: str, field: str) -> Optional[float]:
        """Return a numeric field from a token's ``summary`` block."""
        payload = self._get(endpoint)
        summary = payload.get("summary") if isinstance(payload, dict) else None
        if not isinstance(summary, dict):
            return None
        value = summary.get(field)
        return None if value is None else float(value)

    def _active_dex_count(self, endpoint: str) -> int:
        """Count DEXes on the chain with non-zero 24h volume.

        The endpoint returns the full DEX list in one page (Solana has a small,
        fixed set of protocols), so a single request suffices.
        """
        payload = self._get(endpoint, params={"limit": self._DEX_PAGE_LIMIT})
        dexes = payload.get("dexes", []) if isinstance(payload, dict) else []
        return sum(1 for dex in dexes if (dex.get("volume_usd_24h") or 0) > 0)

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
        elif "summary_field" in config:
            value = self._token_summary_field(
                config["endpoint"], config["summary_field"]
            )
        else:
            value = self._network_field(config["network_field"])

        if value is None:
            return []
        return [{"date": today, "value": value}]

    def get_metric(self, metric: str, date: str, chain: str) -> Defi | Overview | None:
        """Fetch one metric for one date and return it as a typed metric model."""
        rows = self.fetch_rows(metric, date, date)
        if not rows:
            return None

        parsed_date = datetime.date.fromisoformat(date)
        value = rows[0]["value"]

        defi_type = self._DEFI_METRIC_TYPE_MAP.get(metric)
        if defi_type is not None:
            return Defi.from_metric_type(
                metric_type=defi_type, date=parsed_date, value=value
            )

        overview_type = self._OVERVIEW_METRIC_TYPE_MAP.get(metric)
        if overview_type is not None:
            return Overview.from_metric_type(
                metric_type=overview_type, date=parsed_date, value=value
            )

        return None
