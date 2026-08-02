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
    """Remove funds (and their filings/holdings) no longer listed in config.py."""
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
            conn.execute("DELETE FROM funds WHERE cik = ?", (row["cik"],))


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
