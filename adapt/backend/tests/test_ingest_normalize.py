"""A2 'done when' (unit): native formats load, and micros / minor units / FX / tax / time zones normalise exactly."""

from datetime import date, datetime

import httpx
import pytest

from adapt.ingest.connectors.ads import normalize_google_ad_rows, normalize_meta_ad_rows, normalize_meta_campaign_rows
from adapt.ingest.connectors.base import available_at, fx_to_inr
from adapt.ingest.connectors.commerce import (
    normalize_ga4,
    normalize_orders,
    normalize_refunds,
    parse_local_ts,
    parse_utm,
)
from adapt.ingest.http import ConnectorError, SourceHttp

FX = fx_to_inr("USD")


def test_meta_usd_strings_to_inr():
    rows = [{"campaign_id": "238", "adset_id": "23801", "ad_id": "2380101", "date_start": "2026-09-30",
             "impressions": "12000", "clicks": "240", "spend": "150.25",
             "actions": [{"action_type": "link_click", "value": "240"},
                         {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "9"}],
             "action_values": [{"action_type": "offsite_conversion.fb_pixel_purchase", "value": "540.10"}]},
            {"campaign_id": "238", "adset_id": "23801", "ad_id": "2380102", "date_start": "2026-09-30",
             "impressions": "500", "clicks": "3", "spend": "4.00"}]  # no purchases -> no actions key at all
    df = normalize_meta_ad_rows(rows, "USD")
    r = df.iloc[0]
    assert (r.impressions, r.clicks, r.platform_conversions) == (12000, 240, 9)
    assert r.spend_native == 150.25 and r.spend_inr == pytest.approx(150.25 * FX)
    assert r.platform_conversion_value_inr == pytest.approx(540.10 * FX) and r.date == date(2026, 9, 30)
    assert df.iloc[1].platform_conversions == 0 and df.iloc[1].platform_conversion_value_inr == 0
    c = normalize_meta_campaign_rows([{"campaign_id": "238", "date_start": "2026-09-30", "impressions": "9000",
                                       "reach": "4100"}])
    assert (c.iloc[0].reach, c.iloc[0].impressions) == (4100, 9000)


def test_google_micros_and_int64_strings():
    res = [{"campaign": {"id": "2001"}, "adGroup": {"id": "200101"}, "adGroupAd": {"ad": {"id": "20010101"}},
            "segments": {"date": "2026-09-30"},
            "metrics": {"impressions": "30203", "clicks": "1208", "costMicros": "133716948515", "conversions": 49.0,
                        "conversionsValue": 251234.5}}]
    r = normalize_google_ad_rows(res).iloc[0]
    assert r.cost_inr == pytest.approx(133716.948515) and r.cost_micros == 133716948515
    assert (r.impressions, r.clicks, r.platform_conversions) == (30203, 1208, 49.0)


def order(**kw):
    o = {"id": 100000000001, "created_at": "2026-09-30T23:58:00+05:30", "currency": "INR", "taxes_included": False,
         "subtotal_price": "1000.00", "total_tax": "120.00", "total_price": "1120.00", "total_discounts": "0.00",
         "landing_site": "/products/m_jeans-p1?utm_source=google&utm_medium=cpc&utm_campaign=2001&utm_content=20010101",
         "customer": {"id": 77, "orders_count": 1},
         "line_items": [{"id": 1000000000011, "sku": "M_JEANS-P1", "quantity": 1, "price": "1000.00",
                         "total_discount": "0.00"}]}
    o.update(kw)
    return o


def test_orders_strip_gst_parse_utm_and_keep_local_dates():
    df = normalize_orders([order()])
    r = df.iloc[0]
    assert r.line_subtotal_ex_tax == 1000.0 and r.order_tax == 120.0 and r.order_total == 1120.0
    assert (r.utm_source, r.utm_medium, r.utm_campaign, r.utm_content) == ("google", "cpc", "2001", "20010101")
    assert r.date == date(2026, 9, 30)  # 23:58 IST stays on the 30th (no UTC shift)
    assert bool(r.is_new_customer) is True
    unpaid = normalize_orders([order(landing_site="/products/x", customer={"id": 5, "orders_count": 3})]).iloc[0]
    assert unpaid.utm_source is None and unpaid.landing_path == "/products/x" and not unpaid.is_new_customer


def test_tax_inclusive_prices_and_foreign_offsets_are_rejected():
    with pytest.raises(ConnectorError) as e:
        normalize_orders([order(taxes_included=True)])
    assert e.value.code == "TAX_INCLUDED_PRICES"
    with pytest.raises(ConnectorError) as e:
        parse_local_ts("2026-09-30T10:00:00+00:00")
    assert e.value.code == "UNEXPECTED_TIMEZONE"
    assert parse_local_ts("2026-09-30T10:00:00+05:30") == datetime(2026, 9, 30, 10, 0)


def test_refunds_are_separate_dated_records():
    df = normalize_refunds([{"id": 9, "order_id": 9, "created_at": "2026-10-05T10:00:00+05:30",
                             "refund_line_items": [{"line_item_id": 91, "quantity": 1, "subtotal": "1000.00",
                                                    "total_tax": "120.00"}]}])
    r = df.iloc[0]
    assert (r.date, r.amount_ex_tax, r.tax) == (date(2026, 10, 5), 1000.0, 120.0)


def test_ga4_report_rows():
    rep = {"dimensionHeaders": [{"name": n} for n in ("date", "sessionSource", "sessionMedium", "sessionCampaignId")],
           "metricHeaders": [{"name": "sessions"}, {"name": "ecommercePurchases"}, {"name": "purchaseRevenue"}],
           "rows": [{"dimensionValues": [{"value": "20260930"}, {"value": "google"}, {"value": "cpc"},
                                         {"value": "2001"}],
                     "metricValues": [{"value": "812"}, {"value": "33"}, {"value": "171234.50"}]}]}
    r = normalize_ga4(rep).iloc[0]
    assert (r.date, r.campaign_id, r.sessions, r.purchases) == (date(2026, 9, 30), "2001", 812, 33)


def test_available_at_is_logical_next_day_plus_report_lag():
    assert available_at("meta_ads", date(2026, 9, 30)) == datetime(2026, 10, 1, 6, 0)
    assert available_at("store", date(2026, 9, 30)) == datetime(2026, 10, 1, 0, 0)
    assert available_at("ga4", date(2026, 9, 30)) == datetime(2026, 10, 1, 12, 0)


def test_parse_utm_handles_missing_site():
    assert parse_utm(None)["utm_campaign"] is None


# ---- HTTP retry policy -------------------------------------------------------------------------------------
def _http(responses):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        status, body = responses[min(len(calls) - 1, len(responses) - 1)]
        return httpx.Response(status, json=body)

    return SourceHttp("http://world", httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None), calls


def test_reads_retry_transient_errors_then_succeed():
    http, calls = _http([(503, {}), (429, {}), (200, {"ok": True})])
    assert http.get("/x") == {"ok": True} and len(calls) == 3


def test_client_errors_are_not_retried():
    http, calls = _http([(404, {"detail": "no"})])
    with pytest.raises(ConnectorError) as e:
        http.get("/x")
    assert e.value.code == "HTTP_404" and len(calls) == 1


def test_persistent_rate_limit_gives_up_with_a_code():
    http, calls = _http([(429, {})])
    with pytest.raises(ConnectorError) as e:
        http.get("/x")
    assert e.value.code == "HTTP_429" and len(calls) == 4  # 1 + 3 retries
