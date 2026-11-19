"""Whole-company backup as a single JSON document."""

from __future__ import annotations

import sqlite3
from typing import Any

from .books import BahiError, transaction
from .db import SCHEMA_VERSION

FORMAT = "bahi-backup"
TABLES = ("meta", "account_groups", "ledgers", "units", "stock_items", "vouchers", "entries", "invoice_lines")


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def export_company(conn: sqlite3.Connection) -> dict[str, Any]:
    data: dict[str, Any] = {"format": FORMAT, "version": SCHEMA_VERSION}
    for table in TABLES:
        order = "key" if table == "meta" else "id"
        data[table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")]
    return data


def import_company(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    """Load a backup into an empty company database."""
    if data.get("format") != FORMAT:
        raise BahiError("This file is not a Bahi backup")
    if int(data.get("version", 0)) > SCHEMA_VERSION:
        raise BahiError("This backup was made by a newer version of Bahi")
    with transaction(conn):
        conn.execute("DELETE FROM meta")
        for table in TABLES:
            cols = _columns(conn, table)
            rows = data.get(table) or []
            for row in rows:
                if not isinstance(row, dict) or set(row) - set(cols):
                    raise BahiError(f"Backup table {table} has unexpected columns")
            if rows:
                names = list(rows[0])
                conn.executemany(
                    f"INSERT INTO {table}({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
                    [[row.get(c) for c in names] for row in rows],
                )
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise BahiError("Backup refers to records that are missing")
