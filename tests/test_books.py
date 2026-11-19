import pytest

from bahi import books
from bahi.books import BahiError
from bahi.gst import make_gstin

from .conftest import gid, lid, unit


def acct(conn, vtype, *entries, date="2026-04-10", **extra):
    return books.save_voucher(
        conn,
        {"vtype": vtype, "date": date, "entries": [{"ledger_id": l, "side": s, "amount": a} for l, s, a in entries], **extra},
    )


@pytest.fixture
def bank(conn):
    return books.create_ledger(conn, {"name": "Bank", "group_id": gid(conn, "Bank Accounts")})


@pytest.fixture
def rent(conn):
    return books.create_ledger(conn, {"name": "Rent", "group_id": gid(conn, "Indirect Expenses")})


def test_default_chart_is_seeded(conn):
    names = {g["name"] for g in books.list_groups(conn)}
    assert {"Capital Account", "Sundry Debtors", "Duties & Taxes", "Sales Accounts"} <= names
    tax = [l for l in books.list_ledgers(conn) if l["tax_type"]]
    assert len(tax) == 6


def test_company_validation(store):
    c = store.create("x")
    with pytest.raises(BahiError, match="checksum"):
        books.setup_company(c, {"name": "X", "state_code": "29", "gstin": "29AAAPD1234K1Z0", "books_from": "2026-04-01"})
    with pytest.raises(BahiError, match="state"):
        books.setup_company(c, {"name": "X", "state_code": "", "books_from": "2026-04-01"})


def test_payment_receipt_and_voucher_numbering(conn, bank, rent):
    cash = lid(conn, "Cash")
    v1 = acct(conn, "Payment", (rent, "dr", "1000"), (cash, "cr", "1000"))
    v2 = acct(conn, "Payment", (rent, "dr", "500.50"), (bank, "cr", "500.50"))
    assert books.get_voucher(conn, v1)["number"] == "1"
    assert books.get_voucher(conn, v2)["number"] == "2"
    assert books.get_voucher(conn, v2)["amount"] == "500.50"


@pytest.mark.parametrize(
    "vtype,entries,message",
    [
        ("Journal", [("rent", "dr", "100"), ("cash", "cr", "90")], "must be equal"),
        ("Journal", [("rent", "dr", "100")], "at least one debit"),
        ("Journal", [("rent", "dr", "0"), ("cash", "cr", "0")], "greater than zero"),
        ("Payment", [("cash", "dr", "100"), ("rent", "cr", "100")], "credit a cash or bank"),
        ("Receipt", [("rent", "dr", "100"), ("cash", "cr", "100")], "debit a cash or bank"),
        ("Contra", [("rent", "dr", "100"), ("cash", "cr", "100")], "cash and bank"),
    ],
)
def test_accounting_voucher_rules(conn, rent, vtype, entries, message):
    ids = {"rent": rent, "cash": lid(conn, "Cash")}
    with pytest.raises(BahiError, match=message):
        acct(conn, vtype, *[(ids[n], s, a) for n, s, a in entries])


def test_voucher_before_books_begin_is_rejected(conn, rent):
    with pytest.raises(BahiError, match="before the books begin"):
        acct(conn, "Journal", (rent, "dr", "1"), (lid(conn, "Cash"), "cr", "1"), date="2026-03-31")


def test_duplicate_number_rejected(conn, rent):
    cash = lid(conn, "Cash")
    acct(conn, "Journal", (rent, "dr", "1"), (cash, "cr", "1"), number="J-1")
    with pytest.raises(BahiError, match="already exists"):
        acct(conn, "Journal", (rent, "dr", "1"), (cash, "cr", "1"), number="J-1")


def make_party(conn, name, state, pan, group="Sundry Debtors"):
    return books.create_ledger(conn, {"name": name, "group_id": gid(conn, group), "gstin": make_gstin(state, pan)})


def test_intrastate_sale_splits_cgst_sgst_and_moves_stock(conn):
    party = make_party(conn, "Local Co", "29", "AABCL1234M")
    item = books.create_item(conn, {"name": "Widget", "unit_id": unit(conn), "gst_rate": "18", "hsn": "8471"})
    vid = books.save_voucher(conn, {
        "vtype": "Sales", "date": "2026-04-12", "party_ledger_id": party, "main_ledger_id": lid(conn, "Sales"),
        "lines": [{"stock_item_id": item, "qty": "3", "rate": "333.33"}],
    })
    v = books.get_voucher(conn, vid)
    assert v["place_of_supply"] == "29"
    assert v["totals"]["taxable"] == "999.99"
    assert v["totals"]["cgst"] == v["totals"]["sgst"] == "90.00"
    assert v["totals"]["igst"] == "0.00"
    assert v["totals"]["round_off"] == "0.01"  # 1179.99 -> 1180
    assert v["amount"] == "1180.00"
    by_ledger = {e["ledger_name"]: (e["side"], e["amount"]) for e in v["entries"]}
    assert by_ledger["Local Co"] == ("dr", "1180.00")
    assert by_ledger["Sales"] == ("cr", "999.99")
    assert by_ledger["Output CGST"] == ("cr", "90.00")
    assert by_ledger["Round Off"] == ("cr", "0.01")
    assert v["lines"][0]["hsn"] == "8471"


def test_interstate_purchase_uses_input_igst(conn):
    supplier = make_party(conn, "Mumbai Co", "27", "AABCM1234M", "Sundry Creditors")
    vid = books.save_voucher(conn, {
        "vtype": "Purchase", "date": "2026-04-12", "party_ledger_id": supplier, "main_ledger_id": lid(conn, "Purchase"),
        "lines": [{"description": "Consulting", "amount": "1000", "gst_rate": "18"}], "round_off": False,
    })
    v = books.get_voucher(conn, vid)
    by_ledger = {e["ledger_name"]: (e["side"], e["amount"]) for e in v["entries"]}
    assert by_ledger == {
        "Mumbai Co": ("cr", "1180.00"),
        "Purchase": ("dr", "1000.00"),
        "Input IGST": ("dr", "180.00"),
    }


def test_unregistered_company_charges_no_gst(store):
    c = store.create("small")
    books.setup_company(c, {"name": "Small", "state_code": "29", "books_from": "2026-04-01"})
    vid = books.save_voucher(c, {
        "vtype": "Sales", "date": "2026-04-02", "party_ledger_id": lid(c, "Cash"), "main_ledger_id": lid(c, "Sales"),
        "lines": [{"description": "Repair", "amount": "500", "gst_rate": "18"}],
    })
    v = books.get_voucher(c, vid)
    assert v["amount"] == "500.00" and v["totals"]["cgst"] == "0.00"


def test_invoice_validation(conn, rent):
    party = make_party(conn, "Local Co", "29", "AABCL1234M")
    base = {"vtype": "Sales", "date": "2026-04-12", "party_ledger_id": party, "main_ledger_id": lid(conn, "Sales")}
    with pytest.raises(BahiError, match="at least one line"):
        books.save_voucher(conn, {**base, "lines": []})
    with pytest.raises(BahiError, match="Sales Accounts"):
        books.save_voucher(conn, {**base, "main_ledger_id": rent, "lines": [{"description": "x", "amount": "1"}]})
    with pytest.raises(BahiError, match="debtor, creditor"):
        books.save_voucher(conn, {**base, "party_ledger_id": rent, "lines": [{"description": "x", "amount": "1"}]})
    item = books.create_item(conn, {"name": "Widget", "unit_id": unit(conn)})
    with pytest.raises(BahiError, match="quantity"):
        books.save_voucher(conn, {**base, "lines": [{"stock_item_id": item, "rate": "1"}]})


def test_edit_and_delete_voucher(conn, rent):
    cash = lid(conn, "Cash")
    vid = acct(conn, "Payment", (rent, "dr", "100"), (cash, "cr", "100"), narration="first")
    books.save_voucher(conn, {"vtype": "Payment", "date": "2026-04-11", "narration": "fixed",
                              "entries": [{"ledger_id": rent, "side": "dr", "amount": "250"},
                                          {"ledger_id": cash, "side": "cr", "amount": "250"}]}, vid)
    v = books.get_voucher(conn, vid)
    assert (v["number"], v["amount"], v["narration"], v["date"]) == ("1", "250.00", "fixed", "2026-04-11")
    with pytest.raises(BahiError, match="type cannot be changed"):
        books.save_voucher(conn, {"vtype": "Journal", "date": "2026-04-11",
                                  "entries": [{"ledger_id": rent, "side": "dr", "amount": "1"},
                                              {"ledger_id": cash, "side": "cr", "amount": "1"}]}, vid)
    with pytest.raises(BahiError, match="used in vouchers"):
        books.delete_ledger(conn, rent)
    books.delete_voucher(conn, vid)
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
    books.delete_ledger(conn, rent)


def test_masters_validation(conn):
    with pytest.raises(BahiError, match="already exists"):
        books.create_ledger(conn, {"name": "cash", "group_id": gid(conn, "Cash-in-Hand")})
    with pytest.raises(BahiError, match="Duties & Taxes"):
        books.create_ledger(conn, {"name": "Bad tax", "group_id": gid(conn, "Indirect Expenses"),
                                   "tax_type": "cgst", "tax_direction": "output"})
    with pytest.raises(BahiError, match="Built-in"):
        books.delete_group(conn, gid(conn, "Sundry Debtors"))
    sub = books.create_group(conn, {"name": "Retail Customers", "parent_id": gid(conn, "Sundry Debtors")})
    led = books.create_ledger(conn, {"name": "Shop A", "group_id": sub})
    assert books.get_ledger(conn, led)["is_debtor"] is True
    with pytest.raises(BahiError, match="ledgers in this group"):
        books.delete_group(conn, sub)
    with pytest.raises(BahiError, match="under itself"):
        child = books.create_group(conn, {"name": "Child", "parent_id": sub})
        books.update_group(conn, sub, {"parent_id": child})


def test_update_ledger_keeps_unspecified_fields(conn):
    led = books.create_ledger(conn, {"name": "Cap", "group_id": gid(conn, "Capital Account"),
                                     "opening_balance": "1000", "opening_side": "cr"})
    books.update_ledger(conn, led, {"name": "Capital"})
    got = books.get_ledger(conn, led)
    assert (got["name"], got["opening_balance"], got["opening_side"]) == ("Capital", "1000.00", "cr")
