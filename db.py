"""
SQLite storage for filings and holdings.

Everything goes through get_connection(), which makes sure the tables
exist before handing back a connection. There's no separate "migration"
step - the schema is created on first use.
"""

import sqlite3
from pathlib import Path

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS funds (
    cik TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS filings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_cik TEXT NOT NULL REFERENCES funds(cik),
    accession_no TEXT NOT NULL,
    period_of_report TEXT NOT NULL,
    filed_date TEXT NOT NULL,
    UNIQUE(fund_cik, accession_no)
);

CREATE TABLE IF NOT EXISTS holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filing_id INTEGER NOT NULL REFERENCES filings(id),
    cusip TEXT NOT NULL,
    issuer_name TEXT NOT NULL,
    value_usd INTEGER NOT NULL,
    shares INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cusip_map (
    cusip TEXT PRIMARY KEY,
    ticker TEXT,
    company_name TEXT,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS ownership_filings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_cik TEXT NOT NULL REFERENCES funds(cik),
    accession_no TEXT NOT NULL,
    form TEXT NOT NULL,
    issuer_cik TEXT,
    issuer_name TEXT NOT NULL,
    cusip TEXT,
    pct_of_class REAL,
    shares REAL,
    event_date TEXT,
    filed_date TEXT NOT NULL,
    UNIQUE(fund_cik, accession_no)
);

CREATE TABLE IF NOT EXISTS insider_filings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issuer_cik TEXT NOT NULL,
    issuer_name TEXT NOT NULL,
    ticker TEXT,
    accession_no TEXT NOT NULL,
    owner_name TEXT NOT NULL,
    owner_title TEXT,
    is_officer INTEGER NOT NULL DEFAULT 0,
    is_director INTEGER NOT NULL DEFAULT 0,
    is_ten_percent_owner INTEGER NOT NULL DEFAULT 0,
    transaction_date TEXT NOT NULL,
    transaction_code TEXT NOT NULL,
    acquired_disposed TEXT NOT NULL,
    shares REAL,
    price_per_share REAL,
    shares_owned_after REAL,
    filed_date TEXT NOT NULL
);
"""


def get_connection():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_fund(conn, cik, name):
    conn.execute(
        "INSERT INTO funds (cik, name) VALUES (?, ?) "
        "ON CONFLICT(cik) DO UPDATE SET name = excluded.name",
        (cik, name),
    )


def get_existing_accession_numbers(conn, fund_cik):
    rows = conn.execute(
        "SELECT accession_no FROM filings WHERE fund_cik = ?", (fund_cik,)
    ).fetchall()
    return {row["accession_no"] for row in rows}


def insert_filing(conn, fund_cik, accession_no, period_of_report, filed_date):
    cur = conn.execute(
        "INSERT INTO filings (fund_cik, accession_no, period_of_report, filed_date) "
        "VALUES (?, ?, ?, ?)",
        (fund_cik, accession_no, period_of_report, filed_date),
    )
    return cur.lastrowid


def insert_holdings(conn, filing_id, holdings):
    """holdings: list of dicts with cusip, issuer_name, value_usd, shares."""
    conn.executemany(
        "INSERT INTO holdings (filing_id, cusip, issuer_name, value_usd, shares) "
        "VALUES (:filing_id, :cusip, :issuer_name, :value_usd, :shares)",
        [{**h, "filing_id": filing_id} for h in holdings],
    )


def prune_old_filings(conn, fund_cik, keep_accession_numbers):
    """Delete filings (and their holdings) for a fund that aren't in the keep set."""
    rows = conn.execute(
        "SELECT id, accession_no FROM filings WHERE fund_cik = ?", (fund_cik,)
    ).fetchall()
    for row in rows:
        if row["accession_no"] not in keep_accession_numbers:
            conn.execute("DELETE FROM holdings WHERE filing_id = ?", (row["id"],))
            conn.execute("DELETE FROM filings WHERE id = ?", (row["id"],))


def prune_funds_not_in(conn, ciks_to_keep):
    """Remove funds (and their filings/holdings/ownership filings) no longer listed in config.py."""
    rows = conn.execute("SELECT cik FROM funds").fetchall()
    for row in rows:
        if row["cik"] not in ciks_to_keep:
            filing_ids = [
                f["id"]
                for f in conn.execute(
                    "SELECT id FROM filings WHERE fund_cik = ?", (row["cik"],)
                ).fetchall()
            ]
            for filing_id in filing_ids:
                conn.execute("DELETE FROM holdings WHERE filing_id = ?", (filing_id,))
            conn.execute("DELETE FROM filings WHERE fund_cik = ?", (row["cik"],))
            conn.execute("DELETE FROM ownership_filings WHERE fund_cik = ?", (row["cik"],))
            conn.execute("DELETE FROM funds WHERE cik = ?", (row["cik"],))


def get_existing_ownership_accessions(conn, fund_cik):
    rows = conn.execute(
        "SELECT accession_no FROM ownership_filings WHERE fund_cik = ?", (fund_cik,)
    ).fetchall()
    return {row["accession_no"] for row in rows}


def insert_ownership_filing(conn, fund_cik, accession_no, form, filed_date, details):
    """details: dict with issuer_cik, issuer_name, cusip, pct_of_class, shares, event_date."""
    conn.execute(
        "INSERT INTO ownership_filings "
        "(fund_cik, accession_no, form, issuer_cik, issuer_name, cusip, pct_of_class, shares, event_date, filed_date) "
        "VALUES (:fund_cik, :accession_no, :form, :issuer_cik, :issuer_name, :cusip, :pct_of_class, :shares, :event_date, :filed_date)",
        {
            "fund_cik": fund_cik,
            "accession_no": accession_no,
            "form": form,
            "filed_date": filed_date,
            **details,
        },
    )


def get_existing_insider_accessions(conn, issuer_cik):
    rows = conn.execute(
        "SELECT DISTINCT accession_no FROM insider_filings WHERE issuer_cik = ?", (issuer_cik,)
    ).fetchall()
    return {row["accession_no"] for row in rows}


def insert_insider_transactions(conn, transactions):
    """transactions: list of dicts, one per Form 3/4/5 non-derivative transaction row."""
    conn.executemany(
        "INSERT INTO insider_filings "
        "(issuer_cik, issuer_name, ticker, accession_no, owner_name, owner_title, "
        "is_officer, is_director, is_ten_percent_owner, transaction_date, transaction_code, "
        "acquired_disposed, shares, price_per_share, shares_owned_after, filed_date) "
        "VALUES (:issuer_cik, :issuer_name, :ticker, :accession_no, :owner_name, :owner_title, "
        ":is_officer, :is_director, :is_ten_percent_owner, :transaction_date, :transaction_code, "
        ":acquired_disposed, :shares, :price_per_share, :shares_owned_after, :filed_date)",
        transactions,
    )


def prune_insider_filings(conn, cutoff_date, keep_issuer_ciks):
    """Drop transactions older than cutoff_date, and any company no longer in the current top-N universe."""
    conn.execute("DELETE FROM insider_filings WHERE transaction_date < ?", (cutoff_date,))
    if keep_issuer_ciks:
        placeholders = ",".join("?" for _ in keep_issuer_ciks)
        conn.execute(
            f"DELETE FROM insider_filings WHERE issuer_cik NOT IN ({placeholders})",
            list(keep_issuer_ciks),
        )
    else:
        conn.execute("DELETE FROM insider_filings")


def get_fund_filings_latest_two(conn, fund_cik):
    """Return the fund's stored filings, newest first (should be <= 2)."""
    return conn.execute(
        "SELECT * FROM filings WHERE fund_cik = ? ORDER BY period_of_report DESC",
        (fund_cik,),
    ).fetchall()


def get_uncached_cusips(conn, cusips):
    if not cusips:
        return []
    cached = {
        row["cusip"]
        for row in conn.execute("SELECT cusip FROM cusip_map").fetchall()
    }
    return [c for c in set(cusips) if c not in cached]


def upsert_cusip_map(conn, cusip, ticker, company_name, resolved_at):
    conn.execute(
        "INSERT INTO cusip_map (cusip, ticker, company_name, resolved_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(cusip) DO UPDATE SET ticker = excluded.ticker, "
        "company_name = excluded.company_name, resolved_at = excluded.resolved_at",
        (cusip, ticker, company_name, resolved_at),
    )
