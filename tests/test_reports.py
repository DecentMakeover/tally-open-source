from decimal import Decimal

import pytest

from bahi import books, demo, reports, transfer
from bahi.gst import make_gstin

from .conftest import gid, lid, unit


def D(s):
    return Decimal(s)


@pytest.fixture
def traded(conn):
    """Opening capital + stock, one purchase, one sale, one expense."""
    cash = lid(conn, "Cash")
    books.update_ledger(conn, cash, {"opening_balance": "10000", "opening_side": "dr"})
    books.create_ledger(conn, {"name": "Capital", "group_id": gid(conn, "Capital Account"),
                               "opening_balance": "12000", "opening_side": "cr"})
    item = books.create_item(conn, {"name": "Rice", "unit_id": unit(conn, "Kg"), "gst_rate": "5",
                                    "opening_qty": "10", "opening_value": "2000"})  # 200/kg
    supplier = books.create_ledger(conn, {"name": "Supplier", "group_id": gid(conn, "Sundry Creditors"),
                                          "gstin": make_gstin("29", "AABCS1111A")})
    customer = books.create_ledger(conn, {"name": "Customer", "group_id": gid(conn, "Sundry Debtors"),
                                          "gstin": make_gstin("32", "AABCC2222B")})
    rent = books.create_ledger(conn, {"name": "Rent", "group_id": gid(conn, "Indirect Expenses")})
    books.save_voucher(conn, {"vtype": "Purchase", "date": "2026-04-05", "party_ledger_id": supplier,
                              "main_ledger_id": lid(conn, "Purchase"),
                              "lines": [{"stock_item_id": item, "qty": "10", "rate": "220"}]})  # 2200 + 110 GST
    books.save_voucher(conn, {"vtype": "Sales", "date": "2026-04-20", "party_ledger_id": customer,
                              "main_ledger_id": lid(conn, "Sales"),
                              "lines": [{"stock_item_id": item, "qty": "15", "rate": "300"}]})  # 4500 + 225 IGST
    books.save_voucher(conn, {"vtype": "Payment", "date": "2026-04-30",
                              "entries": [{"ledger_id": rent, "side": "dr", "amount": "800"},
                                          {"ledger_id": cash, "side": "cr", "amount": "800"}]})
    return {"item": item, "customer": customer, "supplier": supplier, "cash": cash, "rent": rent}


def test_trial_balance_shows_opening_difference(traded, conn):
    tb = reports.trial_balance(conn, "2026-04-30")
    # Ledgers: Dr 10000 cash vs Cr 12000 capital -> 2000 is the opening stock, not in ledgers.
    assert D(tb["opening_difference"]) == D("-2000")
    assert D(tb["credit_total"]) - D(tb["debit_total"]) == D("2000")


def test_stock_valued_at_weighted_average(traded, conn):
    pos = reports.stock_position(conn, "2026-04-30")[traded["item"]]
    # avg = (2000 + 2200) / 20 = 210; 5 kg left
    assert pos["qty"] == 5000 and pos["value"] == 105000
    s = reports.stock_summary(conn, "2026-04-01", "2026-04-30")["items"][0]
    assert (s["opening_qty"], s["in_qty"], s["out_qty"], s["closing_value"]) == ("10.000", "10.000", "15.000", "1050.00")


def test_profit_and_loss(traded, conn):
    pl = reports.profit_and_loss(conn, "2026-04-01", "2026-04-30")
    # Sales 4500 + closing 1050 - opening 2000 - purchases 2200 = 1350 gross; less rent 800
    assert D(pl["gross_profit"]) == D("1350")
    assert D(pl["net_profit"]) == D("550")
    assert pl["trading_total"] == "5550.00"


def test_balance_sheet_tallies(traded, conn):
    bs = reports.balance_sheet(conn, "2026-04-30")
    assert bs["balanced"], bs
    names = [x["name"] for x in bs["liabilities"]]
    assert "Profit & Loss A/c" in names
    assert "Difference in opening balances" not in names + [x["name"] for x in bs["assets"]]


def test_balance_sheet_reports_unbalanced_openings(conn):
    books.update_ledger(conn, lid(conn, "Cash"), {"opening_balance": "500", "opening_side": "dr"})
    bs = reports.balance_sheet(conn, "2026-04-30")
    assert bs["balanced"]
    diff = next(x for x in bs["liabilities"] if x["name"] == "Difference in opening balances")
    assert diff["amount"] == "500.00"


def test_ledger_statement_running_balance(traded, conn):
    st = reports.ledger_statement(conn, traded["cash"], "2026-04-01", "2026-04-30")
    assert st["opening"] == "10000.00"
    assert [r["particulars"] for r in st["rows"]] == ["Rent"]
    assert st["closing"] == "9200.00"
    later = reports.ledger_statement(conn, traded["cash"], "2026-05-01", "2026-05-31")
    assert later["opening"] == "9200.00" and later["rows"] == []


def test_day_book(traded, conn):
    db = reports.day_book(conn, "2026-04-01", "2026-04-30")
    assert [v["vtype"] for v in db["vouchers"]] == ["Purchase", "Sales", "Payment"]
    assert db["vouchers"][1]["particulars"] == "Customer"
    assert db["vouchers"][1]["amount"] == "4725.00"
    assert len(reports.day_book(conn, "2026-04-01", "2026-04-30", "Sales")["vouchers"]) == 1


def test_gst_summary(traded, conn):
    g = reports.gst_summary(conn, "2026-04-01", "2026-04-30")
    assert g["outward_total"]["igst"] == "225.00"
    assert g["inward_total"]["cgst"] == "55.00"
    assert g["net_tax"] == {"cgst": "-55.00", "sgst": "-55.00", "igst": "225.00", "total": "115.00"}
    assert len(g["b2b"]) == 1 and g["b2b"][0]["place_of_supply"] == "32"
    assert g["hsn"][0]["qty"] == "15.000"


def test_credit_note_returns_stock_and_reverses_tax(traded, conn):
    books.save_voucher(conn, {"vtype": "Credit Note", "date": "2026-04-25", "party_ledger_id": traded["customer"],
                              "main_ledger_id": lid(conn, "Sales"),
                              "lines": [{"stock_item_id": traded["item"], "qty": "1", "rate": "300"}]})
    pos = reports.stock_position(conn, "2026-04-30")[traded["item"]]
    assert pos["qty"] == 6000
    g = reports.gst_summary(conn, "2026-04-01", "2026-04-30")
    assert g["outward_total"]["igst"] == "210.00"
    assert reports.balance_sheet(conn, "2026-04-30")["balanced"]


def test_demo_company_tallies(store):
    c = store.create("demo")
    demo.populate(c)
    for as_of in ("2026-04-30", "2026-06-30", "2027-03-31"):
        assert reports.balance_sheet(c, as_of)["balanced"]
    tb = reports.trial_balance(c, "2027-03-31")
    assert tb["opening_difference"] == "-150000.00"  # equals opening stock


def test_export_import_roundtrip(traded, conn, store):
    data = transfer.export_company(conn)
    copy = store.create("copy")
    transfer.import_company(copy, data)
    assert reports.balance_sheet(copy, "2026-04-30") == reports.balance_sheet(conn, "2026-04-30")
    assert reports.gst_summary(copy) == reports.gst_summary(conn)
    with pytest.raises(books.BahiError, match="not a Bahi backup"):
        transfer.import_company(store.create("bad"), {"format": "other"})
