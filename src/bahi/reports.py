"""Financial reports computed from vouchers.

Signed amounts follow one rule everywhere: debit is positive, credit is
negative. Reports that present "natural" figures (income and liabilities
as positive numbers) flip the sign explicitly and say so.

Stock is valued at weighted average cost: (opening value + purchases) /
(opening quantity + purchased quantity), applied to the quantity in hand.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from . import gst
from .books import INWARD_TYPES, Chart, company, parse_date
from .money import div_round, fmt_money, fmt_qty, fmt_rate


def _day_before(d: str) -> str:
    return (date.fromisoformat(d) - timedelta(days=1)).isoformat()


def _period(conn: sqlite3.Connection, frm: Any, to: Any) -> tuple[str, str, str]:
    books_from = company(conn)["books_from"]
    to_s = parse_date(to, "To date") if to else date.today().isoformat()
    frm_s = parse_date(frm, "From date") if frm else books_from
    frm_s = max(frm_s, books_from)
    return frm_s, to_s, books_from


def movements(conn: sqlite3.Connection, frm: str, to: str) -> dict[int, int]:
    rows = conn.execute(
        """SELECT e.ledger_id, SUM(e.amount) AS amt FROM entries e JOIN vouchers v ON v.id = e.voucher_id
           WHERE v.date >= ? AND v.date <= ? GROUP BY e.ledger_id""",
        (frm, to),
    )
    return {r["ledger_id"]: r["amt"] for r in rows}


def closing_balances(conn: sqlite3.Connection, as_of: str) -> dict[int, int]:
    moved = movements(conn, "0000-01-01", as_of)
    return {r["id"]: r["opening_balance"] + moved.get(r["id"], 0) for r in conn.execute("SELECT id, opening_balance FROM ledgers")}


# --------------------------------------------------------------------------
# Group trees
# --------------------------------------------------------------------------


def _tree(chart: Chart, ledgers: list[sqlite3.Row], amounts: dict[int, int], gid: int, sign: int = 1) -> dict[str, Any]:
    g = chart.groups[gid]
    children = [_tree(chart, ledgers, amounts, c["id"], sign) for c in chart.children(gid)]
    children = [c for c in children if c["children"] or c["amount"]]
    for lg in ledgers:
        if lg["group_id"] == gid and amounts.get(lg["id"], 0):
            children.append({"kind": "ledger", "id": lg["id"], "name": lg["name"], "amount": sign * amounts[lg["id"]]})
    children.sort(key=lambda c: (c["kind"] != "group", c["name"].lower()))
    return {"kind": "group", "id": gid, "name": g["name"], "amount": sum(c["amount"] for c in children), "children": children}


def _fmt_tree(node: dict[str, Any]) -> dict[str, Any]:
    out = {**node, "amount": fmt_money(node["amount"])}
    if "children" in node:
        out["children"] = [_fmt_tree(c) for c in node["children"]]
    return out


def _primaries(chart: Chart, natures: tuple[str, ...], gp: bool | None = None) -> list[dict[str, Any]]:
    return [
        g
        for g in chart.children(None)
        if g["nature"] in natures and (gp is None or bool(g["affects_gross_profit"]) == gp)
    ]


# --------------------------------------------------------------------------
# Trial balance
# --------------------------------------------------------------------------


def trial_balance(conn: sqlite3.Connection, as_of: Any = None) -> dict[str, Any]:
    _, to, _ = _period(conn, None, as_of)
    chart = Chart(conn)
    ledgers = conn.execute("SELECT * FROM ledgers").fetchall()
    bal = closing_balances(conn, to)
    groups = [_tree(chart, ledgers, bal, g["id"]) for g in chart.children(None)]
    groups = [g for g in groups if g["children"]]
    debit = sum(a for a in bal.values() if a > 0)
    credit = -sum(a for a in bal.values() if a < 0)
    diff = debit - credit  # equals the sum of opening balances
    return {
        "as_of": to,
        "groups": [_fmt_tree(g) for g in groups],
        "debit_total": fmt_money(debit),
        "credit_total": fmt_money(credit),
        "opening_difference": fmt_money(diff),
        "grand_total": fmt_money(max(debit, credit)),
    }


# --------------------------------------------------------------------------
# Stock
# --------------------------------------------------------------------------


def stock_position(conn: sqlite3.Connection, as_of: str, frm: str | None = None) -> dict[int, dict[str, int]]:
    """Per item quantities and values as of a date (movements optionally from ``frm``)."""
    items = conn.execute("SELECT * FROM stock_items").fetchall()
    moved: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in conn.execute(
        """SELECT l.stock_item_id AS item, v.vtype, SUM(l.qty) AS qty, SUM(l.taxable) AS val
           FROM invoice_lines l JOIN vouchers v ON v.id = l.voucher_id
           WHERE l.stock_item_id IS NOT NULL AND v.date >= ? AND v.date <= ?
           GROUP BY l.stock_item_id, v.vtype""",
        (frm or "0000-01-01", as_of),
    ):
        moved[r["item"]][r["vtype"]] = [r["qty"], r["val"]]
    out = {}
    for it in items:
        m = moved[it["id"]]
        in_qty = sum(m[t][0] for t in INWARD_TYPES)
        out_qty = sum(m[t][0] for t in ("Sales", "Debit Note"))
        cost_qty = it["opening_qty"] + m["Purchase"][0]
        cost_val = it["opening_value"] + m["Purchase"][1]
        qty = it["opening_qty"] + in_qty - out_qty
        value = div_round(qty * cost_val, cost_qty) if cost_qty else 0
        out[it["id"]] = {
            "qty": qty,
            "value": value,
            "rate": div_round(cost_val * 1000, cost_qty) if cost_qty else 0,
            "in_qty": in_qty,
            "in_value": sum(m[t][1] for t in INWARD_TYPES),
            "out_qty": out_qty,
            "out_value": sum(m[t][1] for t in ("Sales", "Debit Note")),
        }
    return out


def stock_value(conn: sqlite3.Connection, as_of: str) -> int:
    return sum(p["value"] for p in stock_position(conn, as_of).values())


def stock_summary(conn: sqlite3.Connection, frm: Any = None, to: Any = None) -> dict[str, Any]:
    frm, to, _ = _period(conn, frm, to)
    opening = stock_position(conn, _day_before(frm))
    closing = stock_position(conn, to)
    period = stock_position(conn, to, frm)
    rows = []
    for it in conn.execute(
        "SELECT s.id, s.name, u.symbol FROM stock_items s JOIN units u ON u.id = s.unit_id ORDER BY s.name COLLATE NOCASE"
    ):
        o, c, p = opening[it["id"]], closing[it["id"]], period[it["id"]]
        rows.append(
            {
                "id": it["id"],
                "name": it["name"],
                "unit": it["symbol"],
                "opening_qty": fmt_qty(o["qty"]),
                "opening_value": fmt_money(o["value"]),
                "in_qty": fmt_qty(p["in_qty"]),
                "in_value": fmt_money(p["in_value"]),
                "out_qty": fmt_qty(p["out_qty"]),
                "out_value": fmt_money(p["out_value"]),
                "closing_qty": fmt_qty(c["qty"]),
                "closing_rate": fmt_money(c["rate"]),
                "closing_value": fmt_money(c["value"]),
            }
        )
    return {
        "from": frm,
        "to": to,
        "items": rows,
        "opening_total": fmt_money(sum(o["value"] for o in opening.values())),
        "closing_total": fmt_money(sum(c["value"] for c in closing.values())),
    }


# --------------------------------------------------------------------------
# Profit & loss and balance sheet
# --------------------------------------------------------------------------


def _profit_and_loss(conn: sqlite3.Connection, frm: str, to: str, books_from: str) -> dict[str, Any]:
    chart = Chart(conn)
    ledgers = conn.execute("SELECT * FROM ledgers").fetchall()
    amounts = movements(conn, frm, to)
    if frm <= books_from:  # opening balances of P&L ledgers belong to the first period
        for lg in ledgers:
            amounts[lg["id"]] = amounts.get(lg["id"], 0) + lg["opening_balance"]

    def side(natures: tuple[str, ...], gp: bool, sign: int) -> list[dict[str, Any]]:
        nodes = [_tree(chart, ledgers, amounts, g["id"], sign) for g in _primaries(chart, natures, gp)]
        return [n for n in nodes if n["children"] or n["amount"]]

    opening_stock = stock_value(conn, _day_before(frm))
    closing_stock = stock_value(conn, to)
    t_debit = side(("expense",), True, 1)
    t_credit = side(("income",), True, -1)
    gross = sum(g["amount"] for g in t_credit) + closing_stock - opening_stock - sum(g["amount"] for g in t_debit)
    p_debit = side(("expense",), False, 1)
    p_credit = side(("income",), False, -1)
    net = gross + sum(g["amount"] for g in p_credit) - sum(g["amount"] for g in p_debit)

    def line(name: str, amount: int) -> dict[str, Any]:
        return {"kind": "line", "name": name, "amount": amount}

    trading_debit = [line("Opening Stock", opening_stock), *t_debit]
    trading_credit = [*t_credit, line("Closing Stock", closing_stock)]
    if gross >= 0:
        trading_debit.append(line("Gross Profit c/o", gross))
    else:
        trading_credit.append(line("Gross Loss c/o", -gross))
    pnl_debit = ([line("Gross Loss b/f", -gross)] if gross < 0 else []) + p_debit
    pnl_credit = ([line("Gross Profit b/f", gross)] if gross >= 0 else []) + p_credit
    if net >= 0:
        pnl_debit.append(line("Net Profit", net))
    else:
        pnl_credit.append(line("Net Loss", -net))
    return {
        "from": frm,
        "to": to,
        "trading": {"debit": trading_debit, "credit": trading_credit},
        "pnl": {"debit": pnl_debit, "credit": pnl_credit},
        "trading_total": sum(x["amount"] for x in trading_debit),
        "pnl_total": sum(x["amount"] for x in pnl_debit),
        "gross_profit": gross,
        "net_profit": net,
        "opening_stock": opening_stock,
        "closing_stock": closing_stock,
    }


def profit_and_loss(conn: sqlite3.Connection, frm: Any = None, to: Any = None) -> dict[str, Any]:
    frm, to, books_from = _period(conn, frm, to)
    r = _profit_and_loss(conn, frm, to, books_from)
    return {
        "from": r["from"],
        "to": r["to"],
        "trading": {k: [_fmt_tree(x) for x in v] for k, v in r["trading"].items()},
        "pnl": {k: [_fmt_tree(x) for x in v] for k, v in r["pnl"].items()},
        "trading_total": fmt_money(r["trading_total"]),
        "pnl_total": fmt_money(r["pnl_total"]),
        "gross_profit": fmt_money(r["gross_profit"]),
        "net_profit": fmt_money(r["net_profit"]),
        "opening_stock": fmt_money(r["opening_stock"]),
        "closing_stock": fmt_money(r["closing_stock"]),
    }


def balance_sheet(conn: sqlite3.Connection, as_of: Any = None) -> dict[str, Any]:
    _, to, books_from = _period(conn, None, as_of)
    chart = Chart(conn)
    ledgers = conn.execute("SELECT * FROM ledgers").fetchall()
    bal = closing_balances(conn, to)
    pnl = _profit_and_loss(conn, books_from, to, books_from)

    liabilities = [_tree(chart, ledgers, bal, g["id"], -1) for g in _primaries(chart, ("liability",))]
    assets = [_tree(chart, ledgers, bal, g["id"], 1) for g in _primaries(chart, ("asset",))]
    stock = pnl["closing_stock"]
    if stock:
        current = next((a for a in assets if a["name"] == "Current Assets"), None)
        stock_line = {"kind": "line", "name": "Closing Stock", "amount": stock}
        if current is None:
            assets.append({"kind": "line", "name": "Closing Stock", "amount": stock})
        else:
            current["children"].insert(0, stock_line)
            current["amount"] += stock
    liabilities = [x for x in liabilities if x["children"]]
    assets = [x for x in assets if x.get("children") or x["amount"]]
    liabilities.append({"kind": "line", "name": "Profit & Loss A/c", "amount": pnl["net_profit"]})

    opening_stock = conn.execute("SELECT COALESCE(SUM(opening_value), 0) AS v FROM stock_items").fetchone()["v"]
    diff = sum(lg["opening_balance"] for lg in ledgers) + opening_stock
    if diff > 0:
        liabilities.append({"kind": "line", "name": "Difference in opening balances", "amount": diff})
    elif diff < 0:
        assets.append({"kind": "line", "name": "Difference in opening balances", "amount": -diff})
    total_l = sum(x["amount"] for x in liabilities)
    total_a = sum(x["amount"] for x in assets)
    return {
        "as_of": to,
        "liabilities": [_fmt_tree(x) for x in liabilities],
        "assets": [_fmt_tree(x) for x in assets],
        "liabilities_total": fmt_money(total_l),
        "assets_total": fmt_money(total_a),
        "balanced": total_l == total_a,
    }


# --------------------------------------------------------------------------
# Registers
# --------------------------------------------------------------------------


def _counterparts(conn: sqlite3.Connection, voucher_ids: list[int]) -> dict[int, list[sqlite3.Row]]:
    out: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for chunk_start in range(0, len(voucher_ids), 500):
        chunk = voucher_ids[chunk_start : chunk_start + 500]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"""SELECT e.voucher_id, e.ledger_id, e.amount, l.name FROM entries e JOIN ledgers l ON l.id = e.ledger_id
                WHERE e.voucher_id IN ({marks}) ORDER BY e.voucher_id, e.line_no""",
            chunk,
        ):
            out[r["voucher_id"]].append(r)
    return out


def ledger_statement(conn: sqlite3.Connection, ledger_id: int, frm: Any = None, to: Any = None) -> dict[str, Any]:
    frm, to, _ = _period(conn, frm, to)
    lg = conn.execute("SELECT * FROM ledgers WHERE id = ?", (ledger_id,)).fetchone()
    if not lg:
        from .books import NotFound

        raise NotFound(f"Ledger {ledger_id} does not exist")
    before = conn.execute(
        """SELECT COALESCE(SUM(e.amount), 0) AS s FROM entries e JOIN vouchers v ON v.id = e.voucher_id
           WHERE e.ledger_id = ? AND v.date < ?""",
        (ledger_id, frm),
    ).fetchone()["s"]
    opening = lg["opening_balance"] + before
    rows = conn.execute(
        """SELECT v.id, v.date, v.vtype, v.number, v.narration, SUM(e.amount) AS amount
           FROM entries e JOIN vouchers v ON v.id = e.voucher_id
           WHERE e.ledger_id = ? AND v.date >= ? AND v.date <= ?
           GROUP BY v.id ORDER BY v.date, v.id""",
        (ledger_id, frm, to),
    ).fetchall()
    others = _counterparts(conn, [r["id"] for r in rows])
    running, debit, credit, out = opening, 0, 0, []
    for r in rows:
        amt = r["amount"]
        opposite = [o for o in others[r["id"]] if o["ledger_id"] != ledger_id and (o["amount"] > 0) != (amt > 0)]
        particulars = max(opposite, key=lambda o: abs(o["amount"]))["name"] if opposite else "(this ledger)"
        running += amt
        debit += max(amt, 0)
        credit += max(-amt, 0)
        out.append(
            {
                "voucher_id": r["id"],
                "date": r["date"],
                "vtype": r["vtype"],
                "number": r["number"],
                "particulars": particulars,
                "narration": r["narration"],
                "debit": fmt_money(max(amt, 0)),
                "credit": fmt_money(max(-amt, 0)),
                "balance": fmt_money(running),
            }
        )
    return {
        "ledger": {"id": lg["id"], "name": lg["name"]},
        "from": frm,
        "to": to,
        "opening": fmt_money(opening),
        "rows": out,
        "debit_total": fmt_money(debit),
        "credit_total": fmt_money(credit),
        "closing": fmt_money(running),
    }


def day_book(conn: sqlite3.Connection, frm: Any = None, to: Any = None, vtype: str | None = None) -> dict[str, Any]:
    frm, to, _ = _period(conn, frm, to)
    sql = """SELECT v.*, p.name AS party_name FROM vouchers v LEFT JOIN ledgers p ON p.id = v.party_ledger_id
             WHERE v.date >= ? AND v.date <= ?"""
    params: list[Any] = [frm, to]
    if vtype:
        sql += " AND v.vtype = ?"
        params.append(vtype)
    rows = conn.execute(sql + " ORDER BY v.date, v.id", params).fetchall()
    entries = _counterparts(conn, [r["id"] for r in rows])
    out = []
    for r in rows:
        es = entries[r["id"]]
        amount = sum(e["amount"] for e in es if e["amount"] > 0)
        particulars = r["party_name"] or (es[0]["name"] if es else "")
        out.append(
            {
                "id": r["id"],
                "date": r["date"],
                "vtype": r["vtype"],
                "number": r["number"],
                "particulars": particulars,
                "narration": r["narration"],
                "amount": fmt_money(amount),
            }
        )
    return {"from": frm, "to": to, "vtype": vtype, "vouchers": out}


# --------------------------------------------------------------------------
# GST
# --------------------------------------------------------------------------

_TAX = ("taxable", "cgst", "sgst", "igst")


def _money_row(row: dict[str, int]) -> dict[str, str]:
    return {k: fmt_money(v) if k in _TAX + ("total",) else v for k, v in row.items()}


def gst_summary(conn: sqlite3.Connection, frm: Any = None, to: Any = None) -> dict[str, Any]:
    """Period summary of outward and inward supplies (an aid, not a return filer)."""
    frm, to, _ = _period(conn, frm, to)
    lines = conn.execute(
        """SELECT v.id, v.vtype, v.number, v.date, v.place_of_supply, p.name AS party, p.gstin,
                  l.hsn, l.qty, l.gst_rate, l.taxable, l.cgst, l.sgst, l.igst, l.stock_item_id
           FROM invoice_lines l JOIN vouchers v ON v.id = l.voucher_id
           LEFT JOIN ledgers p ON p.id = v.party_ledger_id
           WHERE v.date >= ? AND v.date <= ? ORDER BY v.date, v.id""",
        (frm, to),
    ).fetchall()

    def blank() -> dict[str, int]:
        return dict.fromkeys(_TAX, 0)

    rates = {"outward": defaultdict(blank), "inward": defaultdict(blank)}
    b2b: dict[int, dict[str, Any]] = {}
    b2c: dict[tuple[str, int], dict[str, int]] = defaultdict(blank)
    hsn: dict[tuple[str, int], dict[str, int]] = defaultdict(lambda: {"qty": 0, **blank()})
    for ln in lines:
        outward = ln["vtype"] in ("Sales", "Credit Note")
        sign = -1 if ln["vtype"] in ("Credit Note", "Debit Note") else 1
        bucket = rates["outward" if outward else "inward"][ln["gst_rate"]]
        for k in _TAX:
            bucket[k] += sign * ln[k]
        if not outward:
            continue
        h = hsn[(ln["hsn"] or "-", ln["gst_rate"])]
        h["qty"] += sign * ln["qty"]
        for k in _TAX:
            h[k] += sign * ln[k]
        if ln["vtype"] != "Sales":
            continue
        if ln["gstin"]:
            inv = b2b.setdefault(
                ln["id"],
                {"number": ln["number"], "date": ln["date"], "party": ln["party"], "gstin": ln["gstin"],
                 "place_of_supply": ln["place_of_supply"], **blank()},
            )
            for k in _TAX:
                inv[k] += ln[k]
        else:
            row = b2c[(ln["place_of_supply"], ln["gst_rate"])]
            for k in _TAX:
                row[k] += ln[k]

    def rate_rows(kind: str) -> list[dict[str, Any]]:
        return [
            {"rate": fmt_rate(r), **_money_row({**v, "total": sum(v.values())})}
            for r, v in sorted(rates[kind].items())
        ]

    def total(kind: str) -> dict[str, int]:
        t = blank()
        for v in rates[kind].values():
            for k in _TAX:
                t[k] += v[k]
        return t

    out_t, in_t = total("outward"), total("inward")
    net = {k: out_t[k] - in_t[k] for k in ("cgst", "sgst", "igst")}
    return {
        "from": frm,
        "to": to,
        "outward_by_rate": rate_rows("outward"),
        "inward_by_rate": rate_rows("inward"),
        "outward_total": _money_row(out_t),
        "inward_total": _money_row(in_t),
        "net_tax": {k: fmt_money(v) for k, v in net.items()} | {"total": fmt_money(sum(net.values()))},
        "b2b": [_money_row({**v, "total": sum(v[k] for k in _TAX)}) for v in b2b.values()],
        "b2c": [
            {"place_of_supply": pos, "state_name": gst.STATES.get(pos, ""), "rate": fmt_rate(r),
             **_money_row({**v, "total": sum(v.values())})}
            for (pos, r), v in sorted(b2c.items())
        ],
        "hsn": [
            {"hsn": code, "rate": fmt_rate(r), "qty": fmt_qty(v.pop("qty")),
             **_money_row({**v, "total": sum(v.values())})}
            for (code, r), v in sorted(hsn.items())
        ],
    }
