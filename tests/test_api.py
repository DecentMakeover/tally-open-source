import pytest
from fastapi.testclient import TestClient

from bahi.api import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path))


def test_company_lifecycle(client):
    assert client.get("/api/companies").json() == []
    r = client.post("/api/companies", json={"name": "Shop", "state_code": "29", "books_from": "2026-04-01"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert cid == "shop"
    assert client.get(f"/api/c/{cid}/company").json()["name"] == "Shop"
    r = client.put(f"/api/c/{cid}/company", json={"address": "Main Road"})
    assert r.json()["address"] == "Main Road" and r.json()["name"] == "Shop"
    assert client.post("/api/companies", json={"name": "Shop", "state_code": "29", "books_from": "2026-04-01"}).json()["id"] == "shop-2"
    assert client.delete(f"/api/companies/{cid}").status_code == 204
    assert client.get(f"/api/c/{cid}/company").status_code == 404


def test_bad_company_input_leaves_no_file(client):
    r = client.post("/api/companies", json={"name": "Bad", "state_code": "29", "gstin": "nope", "books_from": "2026-04-01"})
    assert r.status_code == 400 and "GSTIN" in r.json()["detail"]
    assert client.get("/api/companies").json() == []


def test_path_traversal_rejected(client):
    assert client.get("/api/c/..%2F..%2Fetc/company").status_code == 404


def test_voucher_flow_and_reports(client):
    cid = client.post("/api/companies/demo").json()["id"]
    ledgers = {l["name"]: l for l in client.get(f"/api/c/{cid}/ledgers").json()}
    items = {i["name"]: i for i in client.get(f"/api/c/{cid}/items").json()}
    body = {
        "vtype": "Sales", "date": "2026-08-01", "party_ledger_id": ledgers["Malabar Stores"]["id"],
        "main_ledger_id": ledgers["Sales"]["id"],
        "lines": [{"stock_item_id": items["Coir Doormat"]["id"], "qty": "2", "rate": "350"}],
    }
    preview = client.post(f"/api/c/{cid}/vouchers/preview", json=body).json()
    assert preview["totals"]["igst"] == "35.00" and preview["amount"] == "735.00"
    saved = client.post(f"/api/c/{cid}/vouchers", json=body)
    assert saved.status_code == 201
    vid = saved.json()["id"]
    assert saved.json()["party"]["state_name"] == "Kerala"
    body["lines"][0]["qty"] = "3"
    assert client.put(f"/api/c/{cid}/vouchers/{vid}", json=body).json()["amount"] == "1103.00"  # 1102.50 rounded
    for path in ("day-book", "trial-balance", "profit-loss", "balance-sheet", "stock-summary", "gst"):
        assert client.get(f"/api/c/{cid}/reports/{path}?frm=2026-04-01&to=2027-03-31").status_code == 200, path
    assert client.get(f"/api/c/{cid}/reports/balance-sheet?to=2027-03-31").json()["balanced"]
    st = client.get(f"/api/c/{cid}/reports/ledger/{ledgers['Malabar Stores']['id']}").json()
    assert st["rows"]
    assert client.delete(f"/api/c/{cid}/vouchers/{vid}").status_code == 204
    assert client.get(f"/api/c/{cid}/vouchers/{vid}").status_code == 404


def test_validation_errors_are_readable(client):
    cid = client.post("/api/companies/demo").json()["id"]
    r = client.post(f"/api/c/{cid}/vouchers", json={"vtype": "Journal", "date": "2026-05-01", "entries": [
        {"ledger_id": 1, "side": "dr", "amount": "10"}, {"ledger_id": 2, "side": "cr", "amount": "9"}]})
    assert r.status_code == 400 and "must be equal" in r.json()["detail"]
    r = client.post(f"/api/c/{cid}/ledgers", json={"name": "Cash", "group_id": 1})
    assert r.status_code == 400 and "already exists" in r.json()["detail"]


def test_backup_restore(client):
    cid = client.post("/api/companies/demo").json()["id"]
    backup = client.get(f"/api/c/{cid}/export")
    assert "attachment" in backup.headers["content-disposition"]
    new = client.post("/api/companies/import", json=backup.json()).json()["id"]
    assert new != cid
    a = client.get(f"/api/c/{cid}/reports/trial-balance?to=2027-03-31").json()
    b = client.get(f"/api/c/{new}/reports/trial-balance?to=2027-03-31").json()
    assert a == b


def test_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "app.js" in r.text
    assert client.get("/static/app.js").status_code == 200
