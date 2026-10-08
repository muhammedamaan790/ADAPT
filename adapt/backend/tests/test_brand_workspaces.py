"""Brand workspaces: created empty (every number 0), filled only by uploads, independent of each other, and a
re-uploaded row replaces the earlier one instead of double counting."""

import pytest

from adapt.api.brands import BrandError, Brands, brand_overview, import_rows
from adapt.ingest import csv_upload as cu


def metrics(db, ws):
    return {m["key"]: m for m in brand_overview(db, ws)["metrics"]}


def test_new_workspace_is_all_zero_and_fills_from_uploads(tmp_path):
    brands = Brands(tmp_path)
    try:
        a = brands.create("Acme Apparel")
        b = brands.create("Beta Beauty")
        with pytest.raises(BrandError):
            brands.create("acme apparel")
        da, db_ = brands.db(a["id"]), brands.db(b["id"])

        start = metrics(da, a)
        assert all(m["value"] == 0 and m["change"] is None for m in start.values())
        assert brand_overview(da, a)["sources"] == [] and brand_overview(da, a)["attention"] == []

        ads = [{"date": "2026-09-30", "budget_id": "b1", "platform": "Meta", "spend": 1000.0, "impressions": 900,
                "clicks": 30}]
        import_rows(da, "ads", cu.validate("ads", ads), "imp-1", "maria")
        m = metrics(da, a)
        assert m["spend"]["value"] == 1000 and m["net_revenue"]["value"] == 0
        assert m["caa"]["value"] is None and m["caa"]["reason"] == "NEEDS_ORDERS_AND_MARGINS_UPLOAD"

        orders = [{"date": "2026-09-30", "order_id": "o1", "sku": "S1", "quantity": 2, "net_revenue": 5000.0}]
        import_rows(da, "orders", cu.validate("orders", orders), "imp-2", "maria")
        import_rows(da, "margins", cu.validate("margins", [{"sku": "S1", "price": 2500, "unit_cost": 1000}]),
                    "imp-3", "maria")
        m = metrics(da, a)
        assert m["net_revenue"]["value"] == 5000 and m["mer"]["value"] == 5.0
        assert m["caa"]["value"] == 5000 - 2000 - 1000 and m["poas"]["value"] == 3.0

        # a corrected file replaces the row with the same business key
        res = import_rows(da, "ads", cu.validate("ads", [{**ads[0], "spend": 1500.0}]), "imp-4", "maria")
        assert res["replaced"] == 1 and metrics(da, a)["spend"]["value"] == 1500

        # the other workspace is untouched
        assert all(x["value"] == 0 for x in metrics(db_, b).values())

        brands.activate(b["id"])
        assert brands.active()["id"] == b["id"]
        brands.activate(None)
        assert brands.active() is None
    finally:
        brands.close()


def test_orders_contract_and_targeted_staging(tmp_path):
    with pytest.raises(cu.UploadError):
        cu.validate("orders", [{"date": "2026-02-30", "order_id": "o1", "sku": "S1", "quantity": 1,
                                "net_revenue": 10}])
    reg = cu.Registry(tmp_path)
    rows = [{"sku": "S1", "price": 10, "unit_cost": 4}]
    one = cu.stage(reg, "margins", rows, "INR", "Asia/Kolkata", target="brand-a")
    two = cu.stage(reg, "margins", rows, "INR", "Asia/Kolkata", target="brand-b")
    assert one["import_id"] != two["import_id"]
    seen = []
    ack = cu.confirm(reg, one["import_id"], {"sku": "sku", "price": "price", "unit_cost": "unit_cost"}, "maria",
                     sink=lambda kind, r, iid, who: seen.append((kind, len(r))) or "ok")
    assert ack["status"] == "IMPORTED" and seen == [("margins", 1)]
    assert cu.upload_workspaces(reg) == []  # brand uploads are not listed as review workspaces
