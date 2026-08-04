"""
Refresh script: pulls each tracked fund's most recent 13F-HR filings
(QUARTERS_TO_KEEP of them, see config.py) plus any Schedule 13D/13G (5%+
ownership) filings from SEC EDGAR and stores them locally. Afterward, also
refreshes insider (Form 3/4/5) activity for the INSIDER_TRACKING_TOP_N
stocks your funds hold most widely.

Run this:
  - the first time you set up the project
  - any time you want to check for a newly published quarter

It's safe to re-run any time - filings already stored are skipped, and
only the latest QUARTERS_TO_KEEP 13F-HR filings per fund are kept (older
ones are dropped). The Selling & Buying page only ever compares the
newest two of those, so quarter-over-quarter comparisons there still
always mean "this quarter vs. last quarter" regardless of how much
history is kept overall. Schedule 13D/13G filings aren't pruned - they're
rare enough (only filed when a fund crosses, or materially changes, a 5%+
stake in some company) that there's no volume problem keeping all of
them. Insider filings use a rolling day-count window instead (see
INSIDER_FILINGS_WINDOW_DAYS) since they're filed far more often.

Usage:
    python fetch_filings.py
"""

from datetime import date, datetime, timedelta, timezone

import cusip_lookup
import db
import edgar
import queries
from config import (
    FUNDS,
    INSIDER_FILINGS_WINDOW_DAYS,
    INSIDER_TRACKING_TOP_N,
    OWNERSHIP_FILINGS_SINCE,
    QUARTERS_TO_KEEP,
)


def fetch_ownership_filings(conn, cik, name, submissions_data, all_new_cusips):
    """Fetch and store any new Schedule 13D/13G filings (since OWNERSHIP_FILINGS_SINCE) for one fund."""
    try:
        filings = edgar.get_recent_ownership_filings(
            cik, OWNERSHIP_FILINGS_SINCE, submissions_data=submissions_data
        )
    except Exception as exc:
        print(f"  Could not fetch 13D/13G filing list: {exc}")
        return

    if not filings:
        return

    existing = db.get_existing_ownership_accessions(conn, cik)

    for filing in filings:
        accession_no = filing["accession_no"]
        if accession_no in existing:
            continue

        print(f"  {filing['form']} ({filing['filed_date']}): downloading...")
        try:
            details = edgar.get_ownership_filing_details(cik, accession_no, cik, name)
        except Exception as exc:
            print(f"    Failed to fetch/parse this filing: {exc}")
            continue

        if details is None:
            print("    Could not identify our fund's own row in this filing - skipping.")
            continue

        db.insert_ownership_filing(conn, cik, accession_no, filing["form"], filing["filed_date"], details)
        conn.commit()
        print(f"    {details['issuer_name']}: {details['pct_of_class']}% of class.")
        if details["cusip"]:
            all_new_cusips.append(details["cusip"])


def fetch_insider_activity(conn):
    """
    Refresh insider (Form 3/4/5) activity for the INSIDER_TRACKING_TOP_N
    stocks held by the most tracked funds, per Overlap's own ranking. Runs
    after all funds' 13F holdings are refreshed, since it depends on
    current Overlap data to know which companies to look at.
    """
    print(f"\nInsider activity (top {INSIDER_TRACKING_TOP_N} most widely-held stocks)")

    _, overlap_rows = queries.get_overlap(conn)
    top_rows = overlap_rows[:INSIDER_TRACKING_TOP_N]

    try:
        ticker_map = edgar.get_ticker_cik_map()
    except Exception as exc:
        print(f"  Could not fetch SEC ticker/CIK mapping: {exc}")
        return

    cutoff = (date.today() - timedelta(days=INSIDER_FILINGS_WINDOW_DAYS)).isoformat()
    kept_issuer_ciks = []

    for row in top_rows:
        ticker = row["ticker"]
        match = ticker_map.get(ticker.upper())
        if not match:
            print(f"  {ticker}: no SEC ticker/CIK match, skipping.")
            continue

        # Zero-pad to 10 digits to match the format Form 4's own XML uses
        # for <issuerCik> (what actually gets stored in insider_filings) -
        # SEC's ticker map returns it unpadded, and comparing the two
        # formats as plain strings would never match.
        issuer_cik = edgar.pad_cik(match["cik"])
        kept_issuer_ciks.append(issuer_cik)
        print(f"  {ticker} ({match['name']})")

        try:
            filings = edgar.get_recent_insider_filings(issuer_cik, cutoff)
        except Exception as exc:
            print(f"    Could not fetch filing list: {exc}")
            continue

        existing = db.get_existing_insider_accessions(conn, issuer_cik)

        for filing in filings:
            if filing["accession_no"] in existing:
                continue

            try:
                transactions = edgar.get_insider_filing_details(
                    issuer_cik, filing["accession_no"], filing["primary_document"], filing["filed_date"]
                )
            except Exception as exc:
                print(f"    {filing['accession_no']}: failed to fetch/parse ({exc})")
                continue

            if not transactions:
                continue

            db.insert_insider_transactions(conn, transactions)
            conn.commit()
            print(f"    {filing['form']} ({filing['filed_date']}): stored {len(transactions)} transaction(s).")

    db.prune_insider_filings(conn, cutoff, kept_issuer_ciks)
    conn.commit()


def main():
    conn = db.get_connection()
    all_new_cusips = []

    db.prune_funds_not_in(conn, {fund["cik"] for fund in FUNDS})
    conn.commit()

    for fund in FUNDS:
        cik = fund["cik"]
        name = fund["name"]
        print(f"\n{name} (CIK {cik})")

        db.upsert_fund(conn, cik, name)
        conn.commit()

        try:
            submissions_data = edgar.get_submissions(cik)
        except Exception as exc:
            print(f"  Could not fetch filing list: {exc}")
            continue

        fetch_ownership_filings(conn, cik, name, submissions_data, all_new_cusips)

        latest_filings = edgar.get_recent_13f_hr_filings(
            cik, limit=QUARTERS_TO_KEEP, submissions_data=submissions_data
        )

        if not latest_filings:
            print("  No 13F-HR filings found.")
            continue

        existing = db.get_existing_accession_numbers(conn, cik)

        for filing in latest_filings:
            accession_no = filing["accession_no"]
            period = filing["period_of_report"]

            if accession_no in existing:
                print(f"  {period}: already have it, skipping download.")
                continue

            print(f"  {period}: downloading holdings (accession {accession_no})...")
            try:
                holdings = edgar.get_filing_holdings(cik, accession_no)
            except Exception as exc:
                print(f"    Failed to fetch/parse this filing: {exc}")
                continue

            if not holdings:
                print("    No holdings parsed from this filing - skipping.")
                continue

            filing_id = db.insert_filing(
                conn, cik, accession_no, period, filing["filed_date"]
            )
            db.insert_holdings(conn, filing_id, holdings)
            conn.commit()
            print(f"    Stored {len(holdings)} holdings.")
            all_new_cusips.extend(h["cusip"] for h in holdings)

        # Keep only the current latest QUARTERS_TO_KEEP filings for this fund.
        keep = {f["accession_no"] for f in latest_filings}
        db.prune_old_filings(conn, cik, keep)
        conn.commit()

    if all_new_cusips:
        print()
        cusip_lookup.resolve_new_cusips(conn, all_new_cusips)

    fetch_insider_activity(conn)

    conn.close()
    print(f"\nDone. Refreshed at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
