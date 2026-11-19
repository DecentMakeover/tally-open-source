import pytest

from bahi import books
from bahi.db import Store
from bahi.gst import make_gstin

KA_GSTIN = make_gstin("29", "AAAPD1234K")


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


@pytest.fixture
def conn(store):
    """A GST-registered Karnataka company with books from 1 April 2026."""
    c = store.create("acme")
    books.setup_company(
        c, {"name": "Acme", "state_code": "29", "gstin": KA_GSTIN, "books_from": "2026-04-01"}
    )
    yield c
    c.close()


def lid(conn, name):
    return conn.execute("SELECT id FROM ledgers WHERE name = ?", (name,)).fetchone()["id"]


def gid(conn, name):
    return conn.execute("SELECT id FROM account_groups WHERE name = ?", (name,)).fetchone()["id"]


def unit(conn, symbol="Nos"):
    return conn.execute("SELECT id FROM units WHERE symbol = ?", (symbol,)).fetchone()["id"]
