"""A small fictional trading company to explore Bahi with."""

from __future__ import annotations

import sqlite3

from . import books
from .gst import make_gstin


def _id(conn: sqlite3.Connection, table: str, name: str) -> int:
    col = "symbol" if table == "units" else "name"
    return conn.execute(f"SELECT id FROM {table} WHERE {col} = ?", (name,)).fetchone()["id"]


def populate(conn: sqlite3.Connection) -> None:
    books.setup_company(
        conn,
        {
            "name": "Demo Traders",
            "address": "12 Market Road, Mangaluru 575001",
            "state_code": "29",
            "gstin": make_gstin("29", "AAAPD1234K"),
            "books_from": "2026-04-01",
        },
    )
    g = lambda name: _id(conn, "account_groups", name)
    ledger = lambda **kw: books.create_ledger(conn, kw)

    books.update_ledger(conn, _id(conn, "ledgers", "Cash"), {"opening_balance": "50000", "opening_side": "dr"})
    bank = ledger(name="Current Account", group_id=g("Bank Accounts"), opening_balance="300000")
    ledger(name="Proprietor's Capital", group_id=g("Capital Account"), opening_balance="500000", opening_side="cr")
    coastal = ledger(name="Coastal Retail Pvt Ltd", group_id=g("Sundry Debtors"),
                     gstin=make_gstin("29", "AABCC5678L"), address="Hampankatta, Mangaluru")
    malabar = ledger(name="Malabar Stores", group_id=g("Sundry Debtors"),
                     gstin=make_gstin("32", "AAFFM4321Q"), address="MG Road, Kochi")
    ledger(name="Walk-in Customers", group_id=g("Sundry Debtors"), state_code="29")
    western = ledger(name="Western Wholesale Co", group_id=g("Sundry Creditors"),
                     gstin=make_gstin("27", "AAAFW9876R"), address="Masjid Bunder, Mumbai")
    packaging = ledger(name="Kudla Packaging", group_id=g("Sundry Creditors"),
                       gstin=make_gstin("29", "AAKFK2468P"), address="Baikampady, Mangaluru")
    rent = ledger(name="Shop Rent", group_id=g("Indirect Expenses"))
    salary = ledger(name="Salaries", group_id=g("Indirect Expenses"))
    power = ledger(name="Electricity", group_id=g("Indirect Expenses"))
    freight = ledger(name="Freight Inward", group_id=g("Direct Expenses"))
    interest = ledger(name="Bank Interest", group_id=g("Indirect Incomes"))

    kg, nos = _id(conn, "units", "Kg"), _id(conn, "units", "Nos")
    cashew = books.create_item(conn, {"name": "Cashew W240", "unit_id": kg, "hsn": "0801", "gst_rate": "5",
                                      "opening_qty": "100", "opening_value": "90000"})
    coffee = books.create_item(conn, {"name": "Filter Coffee Powder", "unit_id": kg, "hsn": "0901", "gst_rate": "5",
                                      "opening_qty": "80", "opening_value": "40000"})
    tiffin = books.create_item(conn, {"name": "Steel Tiffin Box", "unit_id": nos, "hsn": "7323", "gst_rate": "18",
                                      "opening_qty": "50", "opening_value": "20000"})
    mat = books.create_item(conn, {"name": "Coir Doormat", "unit_id": nos, "hsn": "5702", "gst_rate": "5"})

    cash = _id(conn, "ledgers", "Cash")
    sales, purchase = _id(conn, "ledgers", "Sales"), _id(conn, "ledgers", "Purchase")

    def acct(vtype: str, date: str, narration: str, *entries: tuple[int, str, str]) -> None:
        books.save_voucher(conn, {"vtype": vtype, "date": date, "narration": narration,
                                  "entries": [{"ledger_id": l, "side": s, "amount": a} for l, s, a in entries]})

    def inv(vtype: str, date: str, party: int, lines: list[dict], narration: str = "", ref: str = "") -> None:
        main = sales if vtype in books.SALES_SIDE else purchase
        books.save_voucher(conn, {"vtype": vtype, "date": date, "party_ledger_id": party, "main_ledger_id": main,
                                  "lines": lines, "narration": narration, "reference": ref})

    inv("Purchase", "2026-04-04", western, [
        {"stock_item_id": cashew, "qty": "200", "rate": "880"},
        {"stock_item_id": coffee, "qty": "150", "rate": "520"},
    ], "Quarterly stock-up", "WW/26/0412")
    acct("Payment", "2026-04-04", "Lorry freight for Mumbai consignment", (freight, "dr", "6500"), (cash, "cr", "6500"))
    inv("Purchase", "2026-04-10", packaging, [
        {"stock_item_id": tiffin, "qty": "120", "rate": "390"},
        {"stock_item_id": mat, "qty": "60", "rate": "210"},
    ], ref="KP-1187")
    acct("Payment", "2026-04-05", "April rent", (rent, "dr", "25000"), (bank, "cr", "25000"))
    inv("Sales", "2026-04-15", coastal, [
        {"stock_item_id": cashew, "qty": "120", "rate": "1150"},
        {"stock_item_id": tiffin, "qty": "40", "rate": "560"},
    ], "Credit 30 days")
    inv("Sales", "2026-04-22", malabar, [
        {"stock_item_id": coffee, "qty": "90", "rate": "720"},
        {"stock_item_id": mat, "qty": "25", "rate": "340"},
    ])
    inv("Sales", "2026-04-28", cash, [
        {"stock_item_id": cashew, "qty": "18.5", "rate": "1200"},
        {"stock_item_id": mat, "qty": "6", "rate": "350"},
    ], "Counter sales")
    acct("Receipt", "2026-05-12", "Against invoice 1", (bank, "dr", "150000"), (coastal, "cr", "150000"))
    books.save_voucher(conn, {"vtype": "Credit Note", "date": "2026-05-14", "party_ledger_id": malabar,
                              "main_ledger_id": sales, "narration": "5 kg returned, damaged packs",
                              "lines": [{"stock_item_id": coffee, "qty": "5", "rate": "720"}]})
    acct("Payment", "2026-05-20", "Part payment", (western, "dr", "200000"), (bank, "cr", "200000"))
    acct("Contra", "2026-05-25", "Cash deposited", (bank, "dr", "30000"), (cash, "cr", "30000"))
    acct("Payment", "2026-05-31", "May salaries", (salary, "dr", "42000"), (bank, "cr", "42000"))
    acct("Payment", "2026-05-31", "Electricity bill", (power, "dr", "3850"), (cash, "cr", "3850"))
    acct("Journal", "2026-06-30", "Quarterly interest credited", (bank, "dr", "1240"), (interest, "cr", "1240"))
    inv("Sales", "2026-07-08", coastal, [{"stock_item_id": coffee, "qty": "40", "rate": "730"}])
