"""CSV upload wizard (Stage 3, spec §1): synonym + rapidfuzz mapping, the server-side data contract, idempotent
staging, and confirm into a NEW workspace file per upload."""

import duckdb
import pytest

from adapt.ingest import csv_upload as cu


def test_mapper_uses_exact_synonym_and_fuzzy_matches_once_each():
    m = cu.suggest_mapping("ads", ["Day", "Campaign Budget ID", "Network", "Cost (INR)", "Impr.", "Link Clicks", "x"])
    assert {f: v["header"] for f, v in m.items()} == {
        "date": "Day", "budget_id": "Campaign Budget ID", "platform": "Network", "spend": "Cost (INR)",
        "impressions": "Impr.", "clicks": "Link Clicks", "conversion_value": None}
    assert m["date"]["method"] == "synonym" and all(v["score"] >= 80 for f, v in m.items() if f != "conversion_value")
    none = cu.suggest_mapping("margins", ["foo", "bar"])
    assert all(v["header"] is None for v in none.values())
    with pytest.raises(cu.UploadError):
        cu.suggest_mapping("refunds", ["a"])


ADS = [{"date": "2026-10-01", "budget_id": "b1", "platform": "Meta", "spend": 1200.5, "impressions": 1000,
        "clicks": 30},
       {"date": "2026-10-01", "budget_id": "b2", "platform": "Google", "spend": 900, "impressions": 800, "clicks": 20}]


@pytest.mark.parametrize("bad, msg", [
    ({**ADS[0], "clicks": 2000}, "clicks exceed impressions"),
    ({**ADS[0], "platform": "TikTok"}, "platform must be Meta or Google"),
    ({**ADS[0], "date": "2026-02-30"}, "valid YYYY-MM-DD"),
    ({**ADS[0], "spend": -1}, "nonnegative"),
    ({**ADS[0], "impressions": 10.5}, "whole number"),
    ({**ADS[0], "spend": "abc"}, "must be a number"),
])
def test_server_side_contract_rejects_bad_rows(bad, msg):
    with pytest.raises(cu.UploadError) as e:
        cu.validate("ads", [bad])
    assert any(msg in x for x in e.value.errors)


def test_duplicates_and_inventory_rules():
    with pytest.raises(cu.UploadError) as e:
        cu.validate("ads", [ADS[0], ADS[0]])
    assert any("duplicate business key" in x for x in e.value.errors)
    with pytest.raises(cu.UploadError) as e:
        cu.validate("inventory", [{"sku": "S1", "on_hand": 5, "reserved": 9, "safety_stock": 1}])
    assert any("reserved units exceed" in x for x in e.value.errors)


def test_stage_is_idempotent_and_confirm_creates_its_own_workspace(tmp_path):
    reg = cu.Registry(tmp_path)
    with pytest.raises(cu.UploadError):
        cu.stage(reg, "ads", ADS, "USD", "Asia/Kolkata")                            # brand currency only
    a = cu.stage(reg, "ads", ADS, "INR", "Asia/Kolkata")
    assert a["status"] == "STAGED" and a["row_count"] == 2
    assert cu.stage(reg, "ads", ADS, "INR", "Asia/Kolkata")["import_id"] == a["import_id"]
    mapping = {f: f for f in cu.FIELDS["ads"]}
    with pytest.raises(cu.UploadError):
        cu.confirm(reg, a["import_id"], {"date": "date"}, "maria")                  # incomplete mapping
    c = cu.confirm(reg, a["import_id"], mapping, "maria")
    assert c["status"] == "IMPORTED" and c["row_count"] == 2
    ws = cu.upload_workspaces(reg)
    assert len(ws) == 1 and ws[0]["id"].startswith("upload-")
    con = duckdb.connect(str(tmp_path / f"{ws[0]['id']}.duckdb"), read_only=True)
    try:
        rows = con.execute("SELECT budget_id, spend, _provenance FROM stg.upload_ads ORDER BY 1").fetchall()
        actor = con.execute("SELECT actor FROM ops.upload_manifest").fetchone()[0]
    finally:
        con.close()
    assert rows == [("b1", 1200.5, "UPLOADED"), ("b2", 900.0, "UPLOADED")] and actor == "maria"
    assert cu.confirm(reg, a["import_id"], mapping, "maria")["message"].startswith("already imported")
