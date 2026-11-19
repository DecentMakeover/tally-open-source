"""SQLite storage. One database file per company inside a data directory."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_groups (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT NOT NULL UNIQUE COLLATE NOCASE,
    parent_id            INTEGER REFERENCES account_groups(id),
    nature               TEXT CHECK (nature IN ('asset', 'liability', 'income', 'expense')),
    affects_gross_profit INTEGER NOT NULL DEFAULT 0,
    reserved             INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ledgers (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    group_id        INTEGER NOT NULL REFERENCES account_groups(id),
    opening_balance INTEGER NOT NULL DEFAULT 0,      -- paise, + debit / - credit
    gstin           TEXT NOT NULL DEFAULT '',
    state_code      TEXT NOT NULL DEFAULT '',
    address         TEXT NOT NULL DEFAULT '',
    tax_type        TEXT CHECK (tax_type IN ('cgst', 'sgst', 'igst')),
    tax_direction   TEXT CHECK (tax_direction IN ('input', 'output'))
);

CREATE TABLE IF NOT EXISTS units (
    id     INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_items (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    unit_id       INTEGER NOT NULL REFERENCES units(id),
    hsn           TEXT NOT NULL DEFAULT '',
    gst_rate      INTEGER NOT NULL DEFAULT 0,        -- basis points
    opening_qty   INTEGER NOT NULL DEFAULT 0,        -- thousandths
    opening_value INTEGER NOT NULL DEFAULT 0         -- paise
);

CREATE TABLE IF NOT EXISTS vouchers (
    id              INTEGER PRIMARY KEY,
    vtype           TEXT NOT NULL,
    number          TEXT NOT NULL,
    date            TEXT NOT NULL,                   -- ISO yyyy-mm-dd
    narration       TEXT NOT NULL DEFAULT '',
    reference       TEXT NOT NULL DEFAULT '',
    party_ledger_id INTEGER REFERENCES ledgers(id),
    main_ledger_id  INTEGER REFERENCES ledgers(id),
    place_of_supply TEXT NOT NULL DEFAULT '',
    round_off       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (vtype, number)
);

CREATE TABLE IF NOT EXISTS entries (
    id         INTEGER PRIMARY KEY,
    voucher_id INTEGER NOT NULL REFERENCES vouchers(id) ON DELETE CASCADE,
    line_no    INTEGER NOT NULL,
    ledger_id  INTEGER NOT NULL REFERENCES ledgers(id),
    amount     INTEGER NOT NULL                      -- paise, + debit / - credit
);

CREATE TABLE IF NOT EXISTS invoice_lines (
    id            INTEGER PRIMARY KEY,
    voucher_id    INTEGER NOT NULL REFERENCES vouchers(id) ON DELETE CASCADE,
    line_no       INTEGER NOT NULL,
    stock_item_id INTEGER REFERENCES stock_items(id),
    description   TEXT NOT NULL DEFAULT '',
    hsn           TEXT NOT NULL DEFAULT '',
    qty           INTEGER NOT NULL DEFAULT 0,
    rate          INTEGER NOT NULL DEFAULT 0,
    taxable       INTEGER NOT NULL,
    gst_rate      INTEGER NOT NULL DEFAULT 0,
    cgst          INTEGER NOT NULL DEFAULT 0,
    sgst          INTEGER NOT NULL DEFAULT 0,
    igst          INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_entries_ledger   ON entries(ledger_id);
CREATE INDEX IF NOT EXISTS ix_entries_voucher  ON entries(voucher_id);
CREATE INDEX IF NOT EXISTS ix_vouchers_date    ON vouchers(date);
CREATE INDEX IF NOT EXISTS ix_lines_voucher    ON invoice_lines(voucher_id);
CREATE INDEX IF NOT EXISTS ix_lines_item       ON invoice_lines(stock_item_id);
"""

_CID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:48] or "company"


class Store:
    """A directory of company books, one ``<id>.bahi`` SQLite file each."""

    SUFFIX = ".bahi"

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, cid: str) -> Path:
        if not _CID_RE.match(cid):
            raise KeyError(cid)
        return self.data_dir / f"{cid}{self.SUFFIX}"

    def exists(self, cid: str) -> bool:
        try:
            return self.path_for(cid).exists()
        except KeyError:
            return False

    def new_id(self, name: str) -> str:
        base = slugify(name)
        cid, n = base, 2
        while self.exists(cid):
            cid, n = f"{base}-{n}", n + 1
        return cid

    def open(self, cid: str) -> sqlite3.Connection:
        path = self.path_for(cid)
        if not path.exists():
            raise KeyError(cid)
        return connect(path)

    def create(self, cid: str) -> sqlite3.Connection:
        conn = connect(self.path_for(cid))
        init_schema(conn)
        return conn

    def ids(self) -> list[str]:
        return sorted(p.stem for p in self.data_dir.glob(f"*{self.SUFFIX}"))

    def delete(self, cid: str) -> None:
        path = self.path_for(cid)
        for extra in ("", "-wal", "-shm"):
            Path(f"{path}{extra}").unlink(missing_ok=True)
