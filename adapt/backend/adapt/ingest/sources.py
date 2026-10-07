"""Public source datasets (spec §1) and the columns each must provide.

Every slug and column list here is checked by the M0 profiling gate (`adapt.ingest.profile`).
A required column may name a documented fallback; otherwise its absence fails the gate.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TableSpec:
    file: str
    required: tuple[str, ...]
    optional: dict[str, str] = field(default_factory=dict)  # column -> documented fallback


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    slug: str
    label: str
    tables: tuple[TableSpec, ...]


THELOOK = DatasetSpec(
    key="thelook",
    slug="mustafakeser4/looker-ecommerce-bigquery-dataset",
    label="PUBLIC-SAMPLE",
    tables=(
        TableSpec(
            "products.csv",
            ("id", "cost", "category", "name", "brand", "retail_price", "department", "sku",
             "distribution_center_id"),
        ),
        TableSpec("orders.csv", ("order_id", "user_id", "status", "created_at", "num_of_item")),
        TableSpec(
            "order_items.csv",
            ("id", "order_id", "user_id", "product_id", "inventory_item_id", "status", "created_at"),
            optional={"sale_price": "products.retail_price x discount factor", "returned_at": "no returns"},
        ),
        TableSpec("inventory_items.csv", ("id", "product_id", "created_at", "sold_at", "cost")),
        TableSpec(
            "events.csv",
            ("id", "user_id", "session_id", "created_at", "traffic_source", "uri", "event_type"),
            optional={"city": "no geo drill-down", "state": "no geo drill-down", "browser": "no browser drill-down"},
        ),
        TableSpec("users.csv", ("id", "country", "created_at", "traffic_source")),
    ),
)

GLOBAL_ADS = DatasetSpec(
    key="global_ads",
    slug="nudratabbas/global-ads-performance-google-meta-tiktok",
    label="CALIBRATED",
    tables=(),  # schema undocumented: columns are discovered by synonym matching (see profile.py)
)

KAG_FACEBOOK = DatasetSpec(
    key="kag_facebook",
    slug="loveall/clicks-conversion-tracking",
    label="CALIBRATED",
    tables=(
        TableSpec(
            "KAG_conversion_data.csv",
            ("ad_id", "age", "gender", "interest", "Impressions", "Clicks", "Spent", "Total_Conversion",
             "Approved_Conversion"),
        ),
    ),
)

ALL_DATASETS = (THELOOK, GLOBAL_ADS, KAG_FACEBOOK)

# Synonyms used to discover the Global Ads columns we need for calibration.
GLOBAL_ADS_FIELDS: dict[str, tuple[str, ...]] = {
    "platform": ("platform", "ad_platform", "channel", "network", "source"),
    "spend": ("spend", "cost", "ad_spend", "amount_spent", "spent"),
    "impressions": ("impressions", "impr", "views"),
    "clicks": ("clicks", "link_clicks"),
    "conversions": ("conversions", "purchases", "orders", "results"),
    "revenue": ("revenue", "conversion_value", "sales", "purchase_value"),
    "date": ("date", "day", "report_date", "start_date"),
}
