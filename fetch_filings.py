"""
Refresh script: pulls each tracked fund's two most recent 13F-HR filings
from SEC EDGAR and stores them locally.

Run this:
  - the first time you set up the project
  - any time you want to check for a newly published quarter

It's safe to re-run any time - filings already stored are skipped, and
only the two most recent filings per fund are kept (older ones are
dropped so quarter-over-quarter comparisons always mean "this quarter
vs. last quarter").

Usage:
    python fetch_filings.py
"""

from datetime import datetime, timezone

import cusip_lookup
import db
import edgar
from config import FUNDS


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
            latest_filings = edgar.get_recent_13f_hr_filings(cik, limit=2)
        except Exception as exc:
            print(f"  Could not fetch filing list: {exc}")
            continue

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

        # Keep only the current latest-2 filings for this fund.
        keep = {f["accession_no"] for f in latest_filings}
        db.prune_old_filings(conn, cik, keep)
        conn.commit()

    if all_new_cusips:
        print()
        cusip_lookup.resolve_new_cusips(conn, all_new_cusips)

    conn.close()
    print(f"\nDone. Refreshed at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
