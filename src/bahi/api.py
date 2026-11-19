"""HTTP API and static web UI."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, books, demo, gst, reports, transfer
from .db import Store

STATIC = Path(__file__).parent / "static"

Num = Decimal | str | None


class CompanyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    address: str = ""
    state_code: str = ""
    gstin: str = ""
    books_from: date


class CompanyUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    state_code: str | None = None
    gstin: str | None = None
    books_from: date | None = None


class GroupIn(BaseModel):
    name: str
    parent_id: int


class LedgerIn(BaseModel):
    name: str | None = None
    group_id: int | None = None
    opening_balance: Num = None
    opening_side: Literal["dr", "cr"] = "dr"
    gstin: str | None = None
    state_code: str | None = None
    address: str | None = None
    tax_type: Literal["cgst", "sgst", "igst"] | None = None
    tax_direction: Literal["input", "output"] | None = None


class UnitIn(BaseModel):
    symbol: str
    name: str = ""


class ItemIn(BaseModel):
    name: str | None = None
    unit_id: int | None = None
    hsn: str | None = None
    gst_rate: Num = None
    opening_qty: Num = None
    opening_value: Num = None


class EntryIn(BaseModel):
    ledger_id: int | None = None
    side: Literal["dr", "cr"]
    amount: Num = None


class LineIn(BaseModel):
    stock_item_id: int | None = None
    description: str = ""
    hsn: str = ""
    qty: Num = None
    rate: Num = None
    amount: Num = None
    gst_rate: Num = None


class VoucherIn(BaseModel):
    vtype: str
    date: date
    number: str = ""
    narration: str = ""
    reference: str = ""
    entries: list[EntryIn] = []
    party_ledger_id: int | None = None
    main_ledger_id: int | None = None
    place_of_supply: str = ""
    lines: list[LineIn] = []
    round_off: bool = True


def create_app(data_dir: Path | str) -> FastAPI:
    store = Store(data_dir)
    app = FastAPI(title="Bahi", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.exception_handler(books.BahiError)
    async def _bahi_error(_: Request, exc: books.BahiError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404 if isinstance(exc, books.NotFound) else 400)

    @app.exception_handler(sqlite3.IntegrityError)
    async def _integrity(_: Request, exc: sqlite3.IntegrityError) -> JSONResponse:
        return JSONResponse({"detail": f"That change conflicts with existing records ({exc})"}, status_code=400)

    def db(cid: str) -> Iterator[sqlite3.Connection]:
        try:
            conn = store.open(cid)
        except KeyError:
            raise HTTPException(404, f"No company {cid!r}") from None
        try:
            yield conn
        finally:
            conn.close()

    Conn = Depends(db)

    # -- companies ---------------------------------------------------------

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        return {
            "version": __version__,
            "states": [{"code": c, "name": n} for c, n in gst.STATES.items()],
            "voucher_types": list(books.VTYPES),
            "invoice_types": list(books.INVOICE_TYPES),
        }

    @app.get("/api/companies")
    def companies() -> list[dict[str, Any]]:
        out = []
        for cid in store.ids():
            conn = store.open(cid)
            try:
                out.append({"id": cid, **books.company(conn)})
            finally:
                conn.close()
        return out

    def _new_company(name: str, fill) -> dict[str, str]:
        cid = store.new_id(name)
        conn = store.create(cid)
        try:
            fill(conn)
        except Exception:
            conn.close()
            store.delete(cid)
            raise
        conn.close()
        return {"id": cid}

    @app.post("/api/companies", status_code=201)
    def create_company(body: CompanyIn) -> dict[str, str]:
        return _new_company(body.name, lambda conn: books.setup_company(conn, body.model_dump()))

    @app.post("/api/companies/demo", status_code=201)
    def create_demo() -> dict[str, str]:
        return _new_company("Demo Traders", demo.populate)

    @app.post("/api/companies/import", status_code=201)
    def import_company(body: dict[str, Any]) -> dict[str, str]:
        name = next((m.get("value") for m in body.get("meta") or [] if isinstance(m, dict) and m.get("key") == "name"), "")
        return _new_company(name or "imported", lambda conn: transfer.import_company(conn, body))

    @app.delete("/api/companies/{cid}", status_code=204)
    def delete_company(cid: str) -> None:
        if not store.exists(cid):
            raise HTTPException(404, f"No company {cid!r}")
        store.delete(cid)

    @app.get("/api/c/{cid}/company")
    def get_company(conn: sqlite3.Connection = Conn) -> dict[str, Any]:
        return books.company(conn)

    @app.put("/api/c/{cid}/company")
    def put_company(body: CompanyUpdate, conn: sqlite3.Connection = Conn) -> dict[str, Any]:
        books.update_company(conn, body.model_dump(exclude_none=True))
        return books.company(conn)

    @app.get("/api/c/{cid}/export")
    def export(cid: str, conn: sqlite3.Connection = Conn) -> JSONResponse:
        return JSONResponse(
            transfer.export_company(conn),
            headers={"Content-Disposition": f'attachment; filename="{cid}-{date.today().isoformat()}.json"'},
        )

    # -- masters -----------------------------------------------------------

    @app.get("/api/c/{cid}/groups")
    def groups(conn: sqlite3.Connection = Conn) -> list[dict[str, Any]]:
        return books.list_groups(conn)

    @app.post("/api/c/{cid}/groups", status_code=201)
    def create_group(body: GroupIn, conn: sqlite3.Connection = Conn) -> dict[str, int]:
        return {"id": books.create_group(conn, body.model_dump())}

    @app.put("/api/c/{cid}/groups/{gid}")
    def update_group(gid: int, body: GroupIn, conn: sqlite3.Connection = Conn) -> dict[str, int]:
        books.update_group(conn, gid, body.model_dump())
        return {"id": gid}

    @app.delete("/api/c/{cid}/groups/{gid}", status_code=204)
    def delete_group(gid: int, conn: sqlite3.Connection = Conn) -> None:
        books.delete_group(conn, gid)

    @app.get("/api/c/{cid}/ledgers")
    def ledgers(conn: sqlite3.Connection = Conn) -> list[dict[str, Any]]:
        return books.list_ledgers(conn)

    @app.get("/api/c/{cid}/ledgers/{lid}")
    def ledger(lid: int, conn: sqlite3.Connection = Conn) -> dict[str, Any]:
        return books.get_ledger(conn, lid)

    @app.post("/api/c/{cid}/ledgers", status_code=201)
    def create_ledger(body: LedgerIn, conn: sqlite3.Connection = Conn) -> dict[str, int]:
        return {"id": books.create_ledger(conn, body.model_dump())}

    @app.put("/api/c/{cid}/ledgers/{lid}")
    def update_ledger(lid: int, body: LedgerIn, conn: sqlite3.Connection = Conn) -> dict[str, int]:
        books.update_ledger(conn, lid, body.model_dump(exclude_unset=True))
        return {"id": lid}

    @app.delete("/api/c/{cid}/ledgers/{lid}", status_code=204)
    def delete_ledger(lid: int, conn: sqlite3.Connection = Conn) -> None:
        books.delete_ledger(conn, lid)

    @app.get("/api/c/{cid}/units")
    def units(conn: sqlite3.Connection = Conn) -> list[dict[str, Any]]:
        return books.list_units(conn)

    @app.post("/api/c/{cid}/units", status_code=201)
    def create_unit(body: UnitIn, conn: sqlite3.Connection = Conn) -> dict[str, int]:
        return {"id": books.create_unit(conn, body.model_dump())}

    @app.get("/api/c/{cid}/items")
    def items(conn: sqlite3.Connection = Conn) -> list[dict[str, Any]]:
        return books.list_items(conn)

    @app.post("/api/c/{cid}/items", status_code=201)
    def create_item(body: ItemIn, conn: sqlite3.Connection = Conn) -> dict[str, int]:
        return {"id": books.create_item(conn, body.model_dump())}

    @app.put("/api/c/{cid}/items/{iid}")
    def update_item(iid: int, body: ItemIn, conn: sqlite3.Connection = Conn) -> dict[str, int]:
        books.update_item(conn, iid, body.model_dump(exclude_unset=True))
        return {"id": iid}

    @app.delete("/api/c/{cid}/items/{iid}", status_code=204)
    def delete_item(iid: int, conn: sqlite3.Connection = Conn) -> None:
        books.delete_item(conn, iid)

    # -- vouchers ----------------------------------------------------------

    def _voucher_dict(body: VoucherIn) -> dict[str, Any]:
        return body.model_dump()

    @app.post("/api/c/{cid}/vouchers/preview")
    def preview(body: VoucherIn, conn: sqlite3.Connection = Conn) -> dict[str, Any]:
        return books.posting_out(conn, books.build_voucher(conn, _voucher_dict(body)))

    @app.post("/api/c/{cid}/vouchers", status_code=201)
    def create_voucher(body: VoucherIn, conn: sqlite3.Connection = Conn) -> dict[str, Any]:
        vid = books.save_voucher(conn, _voucher_dict(body))
        return books.get_voucher(conn, vid)

    @app.get("/api/c/{cid}/vouchers/{vid}")
    def get_voucher(vid: int, conn: sqlite3.Connection = Conn) -> dict[str, Any]:
        return books.get_voucher(conn, vid)

    @app.put("/api/c/{cid}/vouchers/{vid}")
    def update_voucher(vid: int, body: VoucherIn, conn: sqlite3.Connection = Conn) -> dict[str, Any]:
        books.save_voucher(conn, _voucher_dict(body), vid)
        return books.get_voucher(conn, vid)

    @app.delete("/api/c/{cid}/vouchers/{vid}", status_code=204)
    def delete_voucher(vid: int, conn: sqlite3.Connection = Conn) -> None:
        books.delete_voucher(conn, vid)

    # -- reports -----------------------------------------------------------

    @app.get("/api/c/{cid}/reports/day-book")
    def day_book(frm: str | None = None, to: str | None = None, vtype: str | None = None,
                 conn: sqlite3.Connection = Conn) -> dict[str, Any]:
        return reports.day_book(conn, frm, to, vtype)

    @app.get("/api/c/{cid}/reports/ledger/{lid}")
    def ledger_statement(lid: int, frm: str | None = None, to: str | None = None,
                         conn: sqlite3.Connection = Conn) -> dict[str, Any]:
        return reports.ledger_statement(conn, lid, frm, to)

    @app.get("/api/c/{cid}/reports/trial-balance")
    def trial_balance(to: str | None = None, conn: sqlite3.Connection = Conn) -> dict[str, Any]:
        return reports.trial_balance(conn, to)

    @app.get("/api/c/{cid}/reports/profit-loss")
    def profit_loss(frm: str | None = None, to: str | None = None, conn: sqlite3.Connection = Conn) -> dict[str, Any]:
        return reports.profit_and_loss(conn, frm, to)

    @app.get("/api/c/{cid}/reports/balance-sheet")
    def balance_sheet(to: str | None = None, conn: sqlite3.Connection = Conn) -> dict[str, Any]:
        return reports.balance_sheet(conn, to)

    @app.get("/api/c/{cid}/reports/stock-summary")
    def stock_summary(frm: str | None = None, to: str | None = None, conn: sqlite3.Connection = Conn) -> dict[str, Any]:
        return reports.stock_summary(conn, frm, to)

    @app.get("/api/c/{cid}/reports/gst")
    def gst_summary(frm: str | None = None, to: str | None = None, conn: sqlite3.Connection = Conn) -> dict[str, Any]:
        return reports.gst_summary(conn, frm, to)

    # -- web UI ------------------------------------------------------------

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    return app
