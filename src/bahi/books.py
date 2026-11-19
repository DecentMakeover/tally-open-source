"""Core double-entry bookkeeping: company setup, masters and vouchers.

Functions take a ``sqlite3.Connection`` for one company and plain Python
values. Amounts coming in may be strings/Decimals in rupees; internally
everything is integer paise (see :mod:`bahi.money`). Validation problems
raise :class:`BahiError` with a message fit to show a user.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import gst
from .money import (
    amount_in_words,
    div_round,
    fmt_money,
    fmt_qty,
    fmt_rate,
    line_amount,
    tax_on,
    to_bp,
    to_milli,
    to_paise,
)


class BahiError(ValueError):
    """A validation error with a user-facing message."""


class NotFound(BahiError):
    pass


ACCOUNTING_TYPES = ("Contra", "Payment", "Receipt", "Journal")
INVOICE_TYPES = ("Sales", "Purchase", "Credit Note", "Debit Note")
VTYPES = ACCOUNTING_TYPES + INVOICE_TYPES
INWARD_TYPES = ("Purchase", "Credit Note")  # stock comes in
OUTWARD_TYPES = ("Sales", "Debit Note")  # stock goes out
SALES_SIDE = ("Sales", "Credit Note")  # uses output tax + sales ledgers
TAX_TYPES = ("cgst", "sgst", "igst")

CASH_BANK_GROUPS = ("Cash-in-Hand", "Bank Accounts", "Bank OD A/c")
PARTY_GROUPS = ("Sundry Debtors", "Sundry Creditors") + CASH_BANK_GROUPS

# (name, parent, nature, affects_gross_profit). Primary groups have no parent.
DEFAULT_GROUPS: list[tuple[str, str | None, str | None, int]] = [
    ("Capital Account", None, "liability", 0),
    ("Loans (Liability)", None, "liability", 0),
    ("Current Liabilities", None, "liability", 0),
    ("Suspense A/c", None, "liability", 0),
    ("Fixed Assets", None, "asset", 0),
    ("Investments", None, "asset", 0),
    ("Current Assets", None, "asset", 0),
    ("Misc. Expenses (Asset)", None, "asset", 0),
    ("Sales Accounts", None, "income", 1),
    ("Direct Incomes", None, "income", 1),
    ("Indirect Incomes", None, "income", 0),
    ("Purchase Accounts", None, "expense", 1),
    ("Direct Expenses", None, "expense", 1),
    ("Indirect Expenses", None, "expense", 0),
    ("Reserves & Surplus", "Capital Account", None, 0),
    ("Secured Loans", "Loans (Liability)", None, 0),
    ("Unsecured Loans", "Loans (Liability)", None, 0),
    ("Bank OD A/c", "Loans (Liability)", None, 0),
    ("Duties & Taxes", "Current Liabilities", None, 0),
    ("Provisions", "Current Liabilities", None, 0),
    ("Sundry Creditors", "Current Liabilities", None, 0),
    ("Bank Accounts", "Current Assets", None, 0),
    ("Cash-in-Hand", "Current Assets", None, 0),
    ("Deposits (Asset)", "Current Assets", None, 0),
    ("Loans & Advances (Asset)", "Current Assets", None, 0),
    ("Sundry Debtors", "Current Assets", None, 0),
]

DEFAULT_UNITS = [("Nos", "Numbers"), ("Kg", "Kilograms"), ("Ltr", "Litres"), ("Box", "Boxes"), ("Hrs", "Hours")]


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def parse_date(value: Any, what: str = "date") -> str:
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise BahiError(f"{what} must be a date like 2026-04-01") from exc


def _clean(text: Any) -> str:
    return (text or "").strip() if isinstance(text, str) or text is None else str(text).strip()


# --------------------------------------------------------------------------
# Company
# --------------------------------------------------------------------------

COMPANY_FIELDS = ("name", "address", "state_code", "gstin", "books_from")


def setup_company(conn: sqlite3.Connection, info: dict[str, Any], seed: bool = True) -> None:
    """Write company details and (optionally) the default chart of accounts."""
    with transaction(conn):
        _write_company(conn, info)
        if seed:
            _seed(conn)


def _write_company(conn: sqlite3.Connection, info: dict[str, Any]) -> None:
    name = _clean(info.get("name"))
    if not name:
        raise BahiError("Company name is required")
    state = _clean(info.get("state_code"))
    gstin = _clean(info.get("gstin")).upper()
    if gstin:
        if err := gst.validate_gstin(gstin):
            raise BahiError(err)
        state = state or gstin[:2]
        if gstin[:2] != state:
            raise BahiError("GSTIN state code does not match the company state")
    if state not in gst.STATES:
        raise BahiError("Choose the company's state")
    values = {
        "name": name,
        "address": _clean(info.get("address")),
        "state_code": state,
        "gstin": gstin,
        "books_from": parse_date(info.get("books_from"), "Books beginning date"),
    }
    conn.executemany(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        list(values.items()),
    )


def _seed(conn: sqlite3.Connection) -> None:
    ids: dict[str, int] = {}
    for name, parent, nature, gp in DEFAULT_GROUPS:
        cur = conn.execute(
            "INSERT INTO account_groups(name, parent_id, nature, affects_gross_profit, reserved) VALUES (?,?,?,?,1)",
            (name, ids.get(parent) if parent else None, nature, gp),
        )
        ids[name] = cur.lastrowid
    ledgers = [
        ("Cash", "Cash-in-Hand", None, None),
        ("Sales", "Sales Accounts", None, None),
        ("Purchase", "Purchase Accounts", None, None),
        ("Round Off", "Indirect Expenses", None, None),
    ]
    for direction in ("output", "input"):
        for tax in TAX_TYPES:
            ledgers.append((f"{direction.title()} {tax.upper()}", "Duties & Taxes", tax, direction))
    for name, group, tax, direction in ledgers:
        cur = conn.execute(
            "INSERT INTO ledgers(name, group_id, tax_type, tax_direction) VALUES (?,?,?,?)",
            (name, ids[group], tax, direction),
        )
        if name == "Round Off":
            conn.execute("INSERT INTO meta(key, value) VALUES ('round_off_ledger_id', ?)", (str(cur.lastrowid),))
    conn.executemany("INSERT INTO units(symbol, name) VALUES (?, ?)", DEFAULT_UNITS)


def company(conn: sqlite3.Connection) -> dict[str, Any]:
    meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    info = {k: meta.get(k, "") for k in COMPANY_FIELDS}
    info["state_name"] = gst.STATES.get(info["state_code"], "")
    info["gst_registered"] = bool(info["gstin"])
    return info


def update_company(conn: sqlite3.Connection, info: dict[str, Any]) -> None:
    current = company(conn)
    merged = {k: info.get(k, current[k]) for k in COMPANY_FIELDS}
    first = conn.execute("SELECT MIN(date) AS d FROM vouchers").fetchone()["d"]
    if first and parse_date(merged["books_from"]) > first:
        raise BahiError(f"Books cannot begin after the first voucher ({first})")
    with transaction(conn):
        _write_company(conn, merged)


def _meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


# --------------------------------------------------------------------------
# Chart of accounts
# --------------------------------------------------------------------------


class Chart:
    """In-memory view of the group tree, loaded once per operation."""

    def __init__(self, conn: sqlite3.Connection):
        self.groups = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM account_groups ORDER BY id")}
        self.by_name = {g["name"].lower(): gid for gid, g in self.groups.items()}

    def get(self, gid: int) -> dict[str, Any]:
        if gid not in self.groups:
            raise NotFound(f"Group {gid} does not exist")
        return self.groups[gid]

    def ancestors(self, gid: int) -> list[int]:
        chain, seen = [], set()
        while gid is not None and gid not in seen:
            seen.add(gid)
            chain.append(gid)
            gid = self.groups[gid]["parent_id"]
        return chain

    def primary(self, gid: int) -> dict[str, Any]:
        return self.groups[self.ancestors(gid)[-1]]

    def is_under(self, gid: int, *names: str) -> bool:
        targets = {self.by_name.get(n.lower()) for n in names}
        return any(a in targets for a in self.ancestors(gid))

    def children(self, gid: int | None) -> list[dict[str, Any]]:
        return [g for g in self.groups.values() if g["parent_id"] == gid]


def list_groups(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    chart = Chart(conn)
    out = []
    for gid, g in chart.groups.items():
        p = chart.primary(gid)
        parent = chart.groups.get(g["parent_id"]) if g["parent_id"] else None
        out.append(
            {
                "id": gid,
                "name": g["name"],
                "parent_id": g["parent_id"],
                "parent_name": parent["name"] if parent else None,
                "primary_name": p["name"],
                "nature": p["nature"],
                "affects_gross_profit": bool(p["affects_gross_profit"]),
                "reserved": bool(g["reserved"]),
            }
        )
    return sorted(out, key=lambda g: g["name"].lower())


def create_group(conn: sqlite3.Connection, data: dict[str, Any]) -> int:
    name, parent_id = _clean(data.get("name")), data.get("parent_id")
    if not name:
        raise BahiError("Group name is required")
    if parent_id is None:
        raise BahiError("Choose the group this group falls under")
    Chart(conn).get(int(parent_id))
    try:
        with transaction(conn):
            return conn.execute(
                "INSERT INTO account_groups(name, parent_id) VALUES (?, ?)", (name, int(parent_id))
            ).lastrowid
    except sqlite3.IntegrityError as exc:
        raise BahiError(f"A group named {name!r} already exists") from exc


def update_group(conn: sqlite3.Connection, gid: int, data: dict[str, Any]) -> None:
    chart = Chart(conn)
    g = chart.get(gid)
    if g["reserved"]:
        raise BahiError("Built-in groups cannot be changed")
    name = _clean(data.get("name", g["name"]))
    parent_id = int(data.get("parent_id", g["parent_id"]))
    chart.get(parent_id)
    if gid in chart.ancestors(parent_id):
        raise BahiError("A group cannot be placed under itself")
    try:
        with transaction(conn):
            conn.execute("UPDATE account_groups SET name = ?, parent_id = ? WHERE id = ?", (name, parent_id, gid))
    except sqlite3.IntegrityError as exc:
        raise BahiError(f"A group named {name!r} already exists") from exc


def delete_group(conn: sqlite3.Connection, gid: int) -> None:
    g = Chart(conn).get(gid)
    if g["reserved"]:
        raise BahiError("Built-in groups cannot be deleted")
    if conn.execute("SELECT 1 FROM account_groups WHERE parent_id = ?", (gid,)).fetchone():
        raise BahiError("Move or delete the groups under this group first")
    if conn.execute("SELECT 1 FROM ledgers WHERE group_id = ?", (gid,)).fetchone():
        raise BahiError("Move or delete the ledgers in this group first")
    with transaction(conn):
        conn.execute("DELETE FROM account_groups WHERE id = ?", (gid,))


# --------------------------------------------------------------------------
# Ledgers
# --------------------------------------------------------------------------


def _ledger_values(conn: sqlite3.Connection, data: dict[str, Any], chart: Chart) -> dict[str, Any]:
    name = _clean(data.get("name"))
    if not name:
        raise BahiError("Ledger name is required")
    if data.get("group_id") is None:
        raise BahiError("Choose the group this ledger falls under")
    group_id = int(data["group_id"])
    chart.get(group_id)
    opening = to_paise(data.get("opening_balance") or 0)
    if opening < 0:
        raise BahiError("Opening balance cannot be negative; pick Dr or Cr instead")
    if data.get("opening_side", "dr") == "cr":
        opening = -opening
    gstin = _clean(data.get("gstin")).upper()
    state = _clean(data.get("state_code"))
    if gstin:
        if err := gst.validate_gstin(gstin):
            raise BahiError(err)
        state = state or gstin[:2]
        if state != gstin[:2]:
            raise BahiError("GSTIN state code does not match the ledger state")
    if state and state not in gst.STATES:
        raise BahiError(f"Unknown state code {state}")
    tax_type = data.get("tax_type") or None
    tax_direction = data.get("tax_direction") or None
    if tax_type or tax_direction:
        if tax_type not in TAX_TYPES or tax_direction not in ("input", "output"):
            raise BahiError("A tax ledger needs a tax type (CGST/SGST/IGST) and a direction (input/output)")
        if not chart.is_under(group_id, "Duties & Taxes"):
            raise BahiError("Tax ledgers must be under Duties & Taxes")
    return {
        "name": name,
        "group_id": group_id,
        "opening_balance": opening,
        "gstin": gstin,
        "state_code": state,
        "address": _clean(data.get("address")),
        "tax_type": tax_type,
        "tax_direction": tax_direction,
    }


def create_ledger(conn: sqlite3.Connection, data: dict[str, Any]) -> int:
    values = _ledger_values(conn, data, Chart(conn))
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    try:
        with transaction(conn):
            return conn.execute(f"INSERT INTO ledgers({cols}) VALUES ({marks})", list(values.values())).lastrowid
    except sqlite3.IntegrityError as exc:
        raise BahiError(f"A ledger named {values['name']!r} already exists") from exc


def update_ledger(conn: sqlite3.Connection, lid: int, data: dict[str, Any]) -> None:
    merged = {**get_ledger(conn, lid), **data}
    values = _ledger_values(conn, merged, Chart(conn))
    sets = ", ".join(f"{k} = ?" for k in values)
    try:
        with transaction(conn):
            conn.execute(f"UPDATE ledgers SET {sets} WHERE id = ?", [*values.values(), lid])
    except sqlite3.IntegrityError as exc:
        raise BahiError(f"A ledger named {values['name']!r} already exists") from exc


def delete_ledger(conn: sqlite3.Connection, lid: int) -> None:
    get_ledger(conn, lid)
    used = conn.execute(
        "SELECT 1 FROM entries WHERE ledger_id = ? UNION SELECT 1 FROM vouchers WHERE party_ledger_id = ? OR main_ledger_id = ?",
        (lid, lid, lid),
    ).fetchone()
    if used:
        raise BahiError("This ledger is used in vouchers and cannot be deleted")
    with transaction(conn):
        conn.execute("DELETE FROM ledgers WHERE id = ?", (lid,))


def _ledger_out(row: sqlite3.Row, chart: Chart, closing: int | None = None) -> dict[str, Any]:
    ob = row["opening_balance"]
    out = {
        "id": row["id"],
        "name": row["name"],
        "group_id": row["group_id"],
        "group_name": chart.groups[row["group_id"]]["name"],
        "primary_name": chart.primary(row["group_id"])["name"],
        "opening_balance": fmt_money(abs(ob)),
        "opening_side": "cr" if ob < 0 else "dr",
        "gstin": row["gstin"],
        "state_code": row["state_code"],
        "address": row["address"],
        "tax_type": row["tax_type"],
        "tax_direction": row["tax_direction"],
        "is_cash_bank": chart.is_under(row["group_id"], *CASH_BANK_GROUPS),
        "is_party": chart.is_under(row["group_id"], *PARTY_GROUPS),
        "is_debtor": chart.is_under(row["group_id"], "Sundry Debtors"),
        "is_creditor": chart.is_under(row["group_id"], "Sundry Creditors"),
        "is_sales": chart.is_under(row["group_id"], "Sales Accounts"),
        "is_purchase": chart.is_under(row["group_id"], "Purchase Accounts"),
    }
    if closing is not None:
        out["closing_balance"] = fmt_money(abs(closing))
        out["closing_side"] = "cr" if closing < 0 else "dr"
    return out


def get_ledger(conn: sqlite3.Connection, lid: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM ledgers WHERE id = ?", (lid,)).fetchone()
    if not row:
        raise NotFound(f"Ledger {lid} does not exist")
    return _ledger_out(row, Chart(conn))


def list_ledgers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    chart = Chart(conn)
    rows = conn.execute(
        """SELECT l.*, l.opening_balance + COALESCE(SUM(e.amount), 0) AS closing
           FROM ledgers l LEFT JOIN entries e ON e.ledger_id = l.id
           GROUP BY l.id ORDER BY l.name COLLATE NOCASE"""
    ).fetchall()
    return [_ledger_out(r, chart, r["closing"]) for r in rows]


# --------------------------------------------------------------------------
# Inventory masters
# --------------------------------------------------------------------------


def list_units(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM units ORDER BY symbol COLLATE NOCASE")]


def create_unit(conn: sqlite3.Connection, data: dict[str, Any]) -> int:
    symbol = _clean(data.get("symbol"))
    if not symbol:
        raise BahiError("Unit symbol is required")
    try:
        with transaction(conn):
            return conn.execute(
                "INSERT INTO units(symbol, name) VALUES (?, ?)", (symbol, _clean(data.get("name")) or symbol)
            ).lastrowid
    except sqlite3.IntegrityError as exc:
        raise BahiError(f"Unit {symbol!r} already exists") from exc


def _item_values(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    name = _clean(data.get("name"))
    if not name:
        raise BahiError("Stock item name is required")
    unit_id = data.get("unit_id")
    if unit_id is None or not conn.execute("SELECT 1 FROM units WHERE id = ?", (int(unit_id),)).fetchone():
        raise BahiError("Choose a unit of measure")
    rate = to_bp(data.get("gst_rate") or 0)
    if not 0 <= rate <= 100 * 100:
        raise BahiError("GST rate must be between 0 and 100%")
    qty = to_milli(data.get("opening_qty") or 0)
    value = to_paise(data.get("opening_value") or 0)
    if qty < 0 or value < 0:
        raise BahiError("Opening quantity and value cannot be negative")
    if value and not qty:
        raise BahiError("An opening value needs an opening quantity")
    return {
        "name": name,
        "unit_id": int(unit_id),
        "hsn": _clean(data.get("hsn")),
        "gst_rate": rate,
        "opening_qty": qty,
        "opening_value": value,
    }


def create_item(conn: sqlite3.Connection, data: dict[str, Any]) -> int:
    values = _item_values(conn, data)
    try:
        with transaction(conn):
            return conn.execute(
                f"INSERT INTO stock_items({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                list(values.values()),
            ).lastrowid
    except sqlite3.IntegrityError as exc:
        raise BahiError(f"A stock item named {values['name']!r} already exists") from exc


def update_item(conn: sqlite3.Connection, iid: int, data: dict[str, Any]) -> None:
    values = _item_values(conn, {**get_item(conn, iid), **data})
    try:
        with transaction(conn):
            conn.execute(
                f"UPDATE stock_items SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?",
                [*values.values(), iid],
            )
    except sqlite3.IntegrityError as exc:
        raise BahiError(f"A stock item named {values['name']!r} already exists") from exc


def delete_item(conn: sqlite3.Connection, iid: int) -> None:
    get_item(conn, iid)
    if conn.execute("SELECT 1 FROM invoice_lines WHERE stock_item_id = ?", (iid,)).fetchone():
        raise BahiError("This item is used in vouchers and cannot be deleted")
    with transaction(conn):
        conn.execute("DELETE FROM stock_items WHERE id = ?", (iid,))


def _item_out(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "unit_id": row["unit_id"],
        "unit": row["unit"],
        "hsn": row["hsn"],
        "gst_rate": fmt_rate(row["gst_rate"]),
        "opening_qty": fmt_qty(row["opening_qty"]),
        "opening_value": fmt_money(row["opening_value"]),
    }


_ITEM_SQL = "SELECT s.*, u.symbol AS unit FROM stock_items s JOIN units u ON u.id = s.unit_id"


def get_item(conn: sqlite3.Connection, iid: int) -> dict[str, Any]:
    row = conn.execute(f"{_ITEM_SQL} WHERE s.id = ?", (iid,)).fetchone()
    if not row:
        raise NotFound(f"Stock item {iid} does not exist")
    return _item_out(row)


def list_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [_item_out(r) for r in conn.execute(f"{_ITEM_SQL} ORDER BY s.name COLLATE NOCASE")]


# --------------------------------------------------------------------------
# Vouchers
# --------------------------------------------------------------------------


@dataclass
class Posting:
    """A fully computed voucher, ready to store or preview."""

    vtype: str
    date: str
    number: str
    narration: str = ""
    reference: str = ""
    party_ledger_id: int | None = None
    main_ledger_id: int | None = None
    place_of_supply: str = ""
    round_off: int = 0
    entries: list[tuple[int, int]] = field(default_factory=list)  # (ledger_id, signed paise)
    lines: list[dict[str, Any]] = field(default_factory=list)


def _ledger_rows(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    return {r["id"]: r for r in conn.execute("SELECT * FROM ledgers")}


def build_voucher(conn: sqlite3.Connection, data: dict[str, Any]) -> Posting:
    vtype = data.get("vtype")
    if vtype not in VTYPES:
        raise BahiError(f"Voucher type must be one of: {', '.join(VTYPES)}")
    info = company(conn)
    vdate = parse_date(data.get("date"), "Voucher date")
    if vdate < info["books_from"]:
        raise BahiError(f"Voucher date is before the books begin ({info['books_from']})")
    posting = Posting(
        vtype=vtype,
        date=vdate,
        number=_clean(data.get("number")),
        narration=_clean(data.get("narration")),
        reference=_clean(data.get("reference")),
    )
    chart, ledgers = Chart(conn), _ledger_rows(conn)
    if vtype in INVOICE_TYPES:
        _build_invoice(conn, posting, data, chart, ledgers, info)
    else:
        _build_accounting(posting, data, chart, ledgers)
    return posting


def _need_ledger(ledgers: dict[int, sqlite3.Row], lid: Any, what: str) -> sqlite3.Row:
    if lid is None or int(lid) not in ledgers:
        raise BahiError(f"Choose a valid {what}")
    return ledgers[int(lid)]


def _build_accounting(posting: Posting, data: dict[str, Any], chart: Chart, ledgers: dict[int, sqlite3.Row]) -> None:
    entries: list[tuple[int, int]] = []
    for i, e in enumerate(data.get("entries") or [], start=1):
        ledger = _need_ledger(ledgers, e.get("ledger_id"), f"ledger on line {i}")
        amount = to_paise(e.get("amount") or 0)
        if amount <= 0:
            raise BahiError(f"Line {i}: amount must be greater than zero")
        side = e.get("side")
        if side not in ("dr", "cr"):
            raise BahiError(f"Line {i}: choose Dr or Cr")
        entries.append((ledger["id"], amount if side == "dr" else -amount))
    if len(entries) < 2:
        raise BahiError("A voucher needs at least one debit and one credit line")
    debit = sum(a for _, a in entries if a > 0)
    credit = -sum(a for _, a in entries if a < 0)
    if not debit or not credit:
        raise BahiError("A voucher needs at least one debit and one credit line")
    if debit != credit:
        raise BahiError(f"Debits ({fmt_money(debit)}) and credits ({fmt_money(credit)}) must be equal")

    def cash_bank(lid: int) -> bool:
        return chart.is_under(ledgers[lid]["group_id"], *CASH_BANK_GROUPS)

    vtype = posting.vtype
    if vtype == "Contra" and not all(cash_bank(lid) for lid, _ in entries):
        raise BahiError("Contra vouchers move money between cash and bank ledgers only")
    if vtype == "Payment" and not any(cash_bank(lid) and a < 0 for lid, a in entries):
        raise BahiError("A payment must credit a cash or bank ledger")
    if vtype == "Receipt" and not any(cash_bank(lid) and a > 0 for lid, a in entries):
        raise BahiError("A receipt must debit a cash or bank ledger")
    posting.entries = entries


def _build_invoice(
    conn: sqlite3.Connection,
    posting: Posting,
    data: dict[str, Any],
    chart: Chart,
    ledgers: dict[int, sqlite3.Row],
    info: dict[str, Any],
) -> None:
    vtype = posting.vtype
    sales_side = vtype in SALES_SIDE
    party = _need_ledger(ledgers, data.get("party_ledger_id"), "party ledger")
    if not chart.is_under(party["group_id"], *PARTY_GROUPS):
        raise BahiError("The party must be a debtor, creditor, cash or bank ledger")
    main_kind = "Sales Accounts" if sales_side else "Purchase Accounts"
    main = _need_ledger(ledgers, data.get("main_ledger_id"), "sales ledger" if sales_side else "purchase ledger")
    if not chart.is_under(main["group_id"], main_kind):
        raise BahiError(f"The {'sales' if sales_side else 'purchase'} ledger must be under {main_kind}")

    pos = _clean(data.get("place_of_supply")) or party["state_code"] or info["state_code"]
    if pos not in gst.STATES:
        raise BahiError(f"Unknown place of supply {pos}")
    registered = info["gst_registered"]
    interstate = pos != info["state_code"]

    items = {r["id"]: r for r in conn.execute("SELECT * FROM stock_items")}
    lines = []
    for i, raw in enumerate(data.get("lines") or [], start=1):
        item_id = raw.get("stock_item_id")
        if item_id is not None:
            item = items.get(int(item_id))
            if item is None:
                raise BahiError(f"Line {i}: unknown stock item")
            qty = to_milli(raw.get("qty") or 0)
            if qty <= 0:
                raise BahiError(f"Line {i}: quantity must be greater than zero")
            if raw.get("rate") not in (None, ""):
                rate = to_paise(raw["rate"])
                taxable = line_amount(qty, rate)
            elif raw.get("amount") not in (None, ""):
                taxable = to_paise(raw["amount"])
                rate = div_round(taxable * 1000, qty)
            else:
                raise BahiError(f"Line {i}: enter a rate or an amount")
            description = _clean(raw.get("description")) or item["name"]
            hsn = _clean(raw.get("hsn")) or item["hsn"]
            default_rate = item["gst_rate"]
        else:
            qty, rate = 0, 0
            taxable = to_paise(raw.get("amount") or 0)
            description = _clean(raw.get("description"))
            if not description:
                raise BahiError(f"Line {i}: choose a stock item or type a description")
            hsn = _clean(raw.get("hsn"))
            default_rate = 0
        if taxable <= 0:
            raise BahiError(f"Line {i}: amount must be greater than zero")
        gst_rate = to_bp(raw["gst_rate"]) if raw.get("gst_rate") not in (None, "") else default_rate
        if not registered:
            gst_rate = 0
        if not 0 <= gst_rate <= 10000:
            raise BahiError(f"Line {i}: GST rate must be between 0 and 100%")
        cgst = sgst = igst = 0
        if interstate:
            igst = tax_on(taxable, gst_rate)
        else:
            cgst = sgst = div_round(taxable * gst_rate, 2 * 10000)  # half the rate each
        lines.append(
            {
                "stock_item_id": int(item_id) if item_id is not None else None,
                "description": description,
                "hsn": hsn,
                "qty": qty,
                "rate": rate,
                "taxable": taxable,
                "gst_rate": gst_rate,
                "cgst": cgst,
                "sgst": sgst,
                "igst": igst,
            }
        )
    if not lines:
        raise BahiError("Add at least one line to the invoice")

    totals = {k: sum(line[k] for line in lines) for k in ("taxable", "cgst", "sgst", "igst")}
    gross = sum(totals.values())
    round_off = (div_round(gross, 100) * 100 - gross) if data.get("round_off", True) else 0
    grand = gross + round_off

    sign = 1 if vtype in ("Sales", "Debit Note") else -1  # +1 when the party is debited
    entries: dict[int, int] = {}

    def post(lid: int, amount: int) -> None:
        if amount:
            entries[lid] = entries.get(lid, 0) + amount

    if party["id"] == main["id"]:
        raise BahiError("Party and sales/purchase ledger must be different")
    post(party["id"], sign * grand)
    post(main["id"], -sign * totals["taxable"])
    direction = "output" if sales_side else "input"
    for tax in TAX_TYPES:
        if totals[tax]:
            match = [r for r in ledgers.values() if r["tax_type"] == tax and r["tax_direction"] == direction]
            if not match:
                raise BahiError(
                    f"Create a ledger under Duties & Taxes with tax type {tax.upper()} ({direction}) to post this invoice"
                )
            post(min(match, key=lambda r: r["id"])["id"], -sign * totals[tax])
    if round_off:
        ro_id = _meta(conn, "round_off_ledger_id")
        if not ro_id or int(ro_id) not in ledgers:
            raise BahiError("The Round Off ledger is missing; turn off rounding or recreate it")
        post(int(ro_id), -sign * round_off)
    posting.party_ledger_id = party["id"]
    posting.main_ledger_id = main["id"]
    posting.place_of_supply = pos
    posting.round_off = round_off
    posting.entries = list(entries.items())
    posting.lines = lines


def _next_number(conn: sqlite3.Connection, vtype: str) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(number AS INTEGER)) AS n FROM vouchers WHERE vtype = ?", (vtype,)
    ).fetchone()
    return str((row["n"] or 0) + 1)


def save_voucher(conn: sqlite3.Connection, data: dict[str, Any], voucher_id: int | None = None) -> int:
    posting = build_voucher(conn, data)
    with transaction(conn):
        if voucher_id is not None:
            existing = conn.execute("SELECT vtype, number FROM vouchers WHERE id = ?", (voucher_id,)).fetchone()
            if not existing:
                raise NotFound(f"Voucher {voucher_id} does not exist")
            if existing["vtype"] != posting.vtype:
                raise BahiError("A voucher's type cannot be changed; delete it and enter a new one")
            number = posting.number or existing["number"]
        else:
            number = posting.number or _next_number(conn, posting.vtype)
        clash = conn.execute(
            "SELECT id FROM vouchers WHERE vtype = ? AND number = ? AND id IS NOT ?",
            (posting.vtype, number, voucher_id),
        ).fetchone()
        if clash:
            raise BahiError(f"{posting.vtype} voucher number {number} already exists")
        header = (
            posting.vtype,
            number,
            posting.date,
            posting.narration,
            posting.reference,
            posting.party_ledger_id,
            posting.main_ledger_id,
            posting.place_of_supply,
            posting.round_off,
        )
        if voucher_id is None:
            voucher_id = conn.execute(
                """INSERT INTO vouchers(vtype, number, date, narration, reference, party_ledger_id,
                   main_ledger_id, place_of_supply, round_off) VALUES (?,?,?,?,?,?,?,?,?)""",
                header,
            ).lastrowid
        else:
            conn.execute(
                """UPDATE vouchers SET vtype=?, number=?, date=?, narration=?, reference=?, party_ledger_id=?,
                   main_ledger_id=?, place_of_supply=?, round_off=? WHERE id = ?""",
                (*header, voucher_id),
            )
            conn.execute("DELETE FROM entries WHERE voucher_id = ?", (voucher_id,))
            conn.execute("DELETE FROM invoice_lines WHERE voucher_id = ?", (voucher_id,))
        conn.executemany(
            "INSERT INTO entries(voucher_id, line_no, ledger_id, amount) VALUES (?,?,?,?)",
            [(voucher_id, i, lid, amt) for i, (lid, amt) in enumerate(posting.entries, start=1)],
        )
        conn.executemany(
            """INSERT INTO invoice_lines(voucher_id, line_no, stock_item_id, description, hsn, qty, rate,
               taxable, gst_rate, cgst, sgst, igst) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (voucher_id, i, ln["stock_item_id"], ln["description"], ln["hsn"], ln["qty"], ln["rate"],
                 ln["taxable"], ln["gst_rate"], ln["cgst"], ln["sgst"], ln["igst"])
                for i, ln in enumerate(posting.lines, start=1)
            ],
        )
    return voucher_id


def delete_voucher(conn: sqlite3.Connection, vid: int) -> None:
    with transaction(conn):
        if not conn.execute("DELETE FROM vouchers WHERE id = ?", (vid,)).rowcount:
            raise NotFound(f"Voucher {vid} does not exist")


def _entries_out(entries: list[tuple[int, int]], names: dict[int, str]) -> list[dict[str, Any]]:
    return [
        {
            "ledger_id": lid,
            "ledger_name": names.get(lid, "?"),
            "side": "dr" if amt > 0 else "cr",
            "amount": fmt_money(abs(amt)),
        }
        for lid, amt in entries
    ]


def _lines_out(lines: list[dict[str, Any]], items: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for ln in lines:
        item = items.get(ln["stock_item_id"]) if ln["stock_item_id"] else None
        out.append(
            {
                "stock_item_id": ln["stock_item_id"],
                "item_name": item["name"] if item else None,
                "unit": item["unit"] if item else "",
                "description": ln["description"],
                "hsn": ln["hsn"],
                "qty": fmt_qty(ln["qty"]) if ln["stock_item_id"] else None,
                "rate": fmt_money(ln["rate"]) if ln["stock_item_id"] else None,
                "taxable": fmt_money(ln["taxable"]),
                "gst_rate": fmt_rate(ln["gst_rate"]),
                "cgst": fmt_money(ln["cgst"]),
                "sgst": fmt_money(ln["sgst"]),
                "igst": fmt_money(ln["igst"]),
                "total": fmt_money(ln["taxable"] + ln["cgst"] + ln["sgst"] + ln["igst"]),
            }
        )
    return out


def posting_out(conn: sqlite3.Connection, posting: Posting) -> dict[str, Any]:
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM ledgers")}
    items = {i["id"]: i for i in list_items(conn)}
    lines = posting.lines
    totals = {k: sum(ln[k] for ln in lines) for k in ("taxable", "cgst", "sgst", "igst")}
    debit = sum(a for _, a in posting.entries if a > 0)
    grand = sum(totals.values()) + posting.round_off if lines else debit
    return {
        "vtype": posting.vtype,
        "number": posting.number,
        "date": posting.date,
        "narration": posting.narration,
        "reference": posting.reference,
        "party_ledger_id": posting.party_ledger_id,
        "party_name": names.get(posting.party_ledger_id) if posting.party_ledger_id else None,
        "main_ledger_id": posting.main_ledger_id,
        "main_ledger_name": names.get(posting.main_ledger_id) if posting.main_ledger_id else None,
        "place_of_supply": posting.place_of_supply,
        "place_of_supply_name": gst.STATES.get(posting.place_of_supply, ""),
        "entries": _entries_out(posting.entries, names),
        "lines": _lines_out(lines, items),
        "totals": {k: fmt_money(v) for k, v in totals.items()} | {"round_off": fmt_money(posting.round_off)},
        "amount": fmt_money(grand),
        "amount_in_words": amount_in_words(grand),
    }


def get_voucher(conn: sqlite3.Connection, vid: int) -> dict[str, Any]:
    v = conn.execute("SELECT * FROM vouchers WHERE id = ?", (vid,)).fetchone()
    if not v:
        raise NotFound(f"Voucher {vid} does not exist")
    posting = Posting(
        vtype=v["vtype"],
        date=v["date"],
        number=v["number"],
        narration=v["narration"],
        reference=v["reference"],
        party_ledger_id=v["party_ledger_id"],
        main_ledger_id=v["main_ledger_id"],
        place_of_supply=v["place_of_supply"],
        round_off=v["round_off"],
        entries=[
            (r["ledger_id"], r["amount"])
            for r in conn.execute("SELECT * FROM entries WHERE voucher_id = ? ORDER BY line_no", (vid,))
        ],
        lines=[dict(r) for r in conn.execute("SELECT * FROM invoice_lines WHERE voucher_id = ? ORDER BY line_no", (vid,))],
    )
    out = posting_out(conn, posting)
    out["id"] = vid
    if posting.party_ledger_id:
        party = conn.execute("SELECT * FROM ledgers WHERE id = ?", (posting.party_ledger_id,)).fetchone()
        out["party"] = {"name": party["name"], "gstin": party["gstin"], "address": party["address"],
                        "state_code": party["state_code"], "state_name": gst.STATES.get(party["state_code"], "")}
    out["company"] = company(conn)
    return out
