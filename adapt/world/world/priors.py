"""Platform priors fitted from the Global Ads E-commerce rows (spec §1, §10: truth is drawn from priors).

Each channel gets a pool of observed (CTR, CVR, CPM) triples; a campaign's truth draws one row jointly
(bootstrap), which keeps the observed correlation between the three rates. Channels with too few rows fall back
to the parametric defaults in benchmarks.yaml, and the pool records which source it used.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import duckdb
import numpy as np
import yaml

BENCHMARKS_PATH = Path(__file__).with_name("benchmarks.yaml")

# channel -> (Global Ads platform, campaign types used; None = all types)
CHANNEL_SOURCES: dict[str, tuple[str, tuple[str, ...] | None]] = {
    "google_search": ("Google Ads", ("Search",)),
    "google_video": ("Google Ads", ("Video",)),
    "meta": ("Meta Ads", None),
}


@lru_cache
def benchmarks() -> dict:
    return yaml.safe_load(BENCHMARKS_PATH.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class RatePool:
    channel: str
    ctr: np.ndarray
    cvr: np.ndarray
    cpm_usd: np.ndarray
    source: str  # "global_ads:E-commerce (n=..)" or "benchmarks fallback"

    def __len__(self) -> int:
        return len(self.ctr)


def load_priors(global_ads_csv: str | Path | None) -> dict[str, RatePool]:
    bench = benchmarks()
    rows: dict[str, list[tuple[float, float, float]]] = {c: [] for c in CHANNEL_SOURCES}
    if global_ads_csv is not None and Path(global_ads_csv).exists():
        path = Path(global_ads_csv).as_posix().replace("'", "''")
        data = duckdb.sql(f"""
            SELECT platform, campaign_type,
                   clicks / impressions AS ctr, conversions / clicks AS cvr, 1000 * ad_spend / impressions AS cpm
            FROM read_csv_auto('{path}', header = true)
            WHERE industry = 'E-commerce' AND impressions > 0 AND clicks > 0 AND conversions > 0 AND ad_spend > 0
            ORDER BY date, platform, campaign_type, country
        """).fetchall()
        for platform, ctype, ctr, cvr, cpm in data:
            for channel, (p, types) in CHANNEL_SOURCES.items():
                if platform == p and (types is None or ctype in types):
                    rows[channel].append((ctr, cvr, cpm))
    pools = {}
    for channel, triples in rows.items():
        if len(triples) >= bench["min_prior_rows"]:
            arr = np.asarray(triples, dtype=float)
            pools[channel] = RatePool(channel, arr[:, 0], arr[:, 1], arr[:, 2],
                                      f"global_ads:E-commerce (n={len(triples)})")
        else:
            fb = bench["fallback_rates"][channel]
            pools[channel] = RatePool(channel, np.array([fb["ctr"]]), np.array([fb["cvr"]]),
                                      np.array([fb["cpm_usd"]]), "benchmarks fallback")
    return pools
