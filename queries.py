"""
Turns the raw funds/filings/holdings/cusip_map tables into the views the
app shows. Kept separate from app.py so the Flask routes stay thin.
"""

from config import FUNDS as CONFIG_FUNDS


def get_last_updated(conn):
    row = conn.execute("SELECT MAX(filed_date) AS d FROM filings").fetchone()
    return row["d"]


def _cusip_map_lookup(conn):
    rows = conn.execute("SELECT * FROM cusip_map").fetchall()
    return {row["cusip"]: row for row in rows}


def _latest_filing_per_fund(conn):
    """fund_cik -> filing row for that fund's most recent stored filing."""
    funds = conn.execute("SELECT * FROM funds ORDER BY name").fetchall()
    latest = {}
    for fund in funds:
        filing = conn.execute(
            "SELECT * FROM filings WHERE fund_cik = ? ORDER BY period_of_report DESC LIMIT 1",
            (fund["cik"],),
        ).fetchone()
        if filing:
            latest[fund["cik"]] = filing
    return funds, latest


def _display_ticker(cusip, cusip_map):
    row = cusip_map.get(cusip)
    if row and row["ticker"]:
        return row["ticker"]
    return cusip


def _display_company(cusip, issuer_name, cusip_map):
    row = cusip_map.get(cusip)
    if row and row["company_name"]:
        return row["company_name"]
    return issuer_name


def get_positions_by_fund(conn):
    """
    For each fund, the holdings in its latest stored filing, with each
    position's % of that filing's total value, sorted by % descending.
    """
    cusip_map = _cusip_map_lookup(conn)
    funds, latest = _latest_filing_per_fund(conn)

    results = []
    for fund in funds:
        filing = latest.get(fund["cik"])
        if not filing:
            results.append(
                {"fund_cik": fund["cik"], "fund_name": fund["name"], "period": None, "rows": []}
            )
            continue

        holdings = conn.execute(
            "SELECT * FROM holdings WHERE filing_id = ?", (filing["id"],)
        ).fetchall()
        total = sum(h["value_usd"] for h in holdings) or 1

        rows = [
            {
                "ticker": _display_ticker(h["cusip"], cusip_map),
                "company_name": _display_company(h["cusip"], h["issuer_name"], cusip_map),
                "value_usd": h["value_usd"],
                "pct": h["value_usd"] / total * 100,
            }
            for h in holdings
        ]
        rows.sort(key=lambda r: r["pct"], reverse=True)

        results.append(
            {
                "fund_cik": fund["cik"],
                "fund_name": fund["name"],
                "period": filing["period_of_report"],
                "rows": rows,
            }
        )

    return results


def get_selling_buying_by_fund(conn):
    """
    For each fund with two stored filings, compare latest vs. previous by
    CUSIP. Returns a list per fund with two row lists: reduced/exited and
    new/increased.
    """
    cusip_map = _cusip_map_lookup(conn)
    funds = conn.execute("SELECT * FROM funds ORDER BY name").fetchall()

    results = []
    for fund in funds:
        filings = conn.execute(
            "SELECT * FROM filings WHERE fund_cik = ? ORDER BY period_of_report DESC",
            (fund["cik"],),
        ).fetchall()

        if len(filings) < 2:
            results.append(
                {
                    "fund_name": fund["name"],
                    "have_comparison": False,
                    "latest_period": filings[0]["period_of_report"] if filings else None,
                    "previous_period": None,
                    "reduced": [],
                    "increased": [],
                }
            )
            continue

        latest_filing, previous_filing = filings[0], filings[1]

        def holdings_by_cusip(filing_id):
            rows = conn.execute(
                "SELECT * FROM holdings WHERE filing_id = ?", (filing_id,)
            ).fetchall()
            return {r["cusip"]: r for r in rows}

        latest_h = holdings_by_cusip(latest_filing["id"])
        previous_h = holdings_by_cusip(previous_filing["id"])

        reduced, increased = [], []
        for cusip in set(latest_h) | set(previous_h):
            prev_row = previous_h.get(cusip)
            latest_row = latest_h.get(cusip)
            prev_shares = prev_row["shares"] if prev_row else 0
            latest_shares = latest_row["shares"] if latest_row else 0

            if latest_shares == prev_shares:
                continue

            issuer_name = (latest_row or prev_row)["issuer_name"]
            entry = {
                "ticker": _display_ticker(cusip, cusip_map),
                "company_name": _display_company(cusip, issuer_name, cusip_map),
                "shares_previous": prev_shares,
                "shares_latest": latest_shares,
            }

            if latest_shares < prev_shares:
                entry["pct_change"] = (
                    (latest_shares - prev_shares) / prev_shares * 100 if prev_shares else None
                )
                entry["sold_out"] = latest_shares == 0
                reduced.append(entry)
            else:
                entry["pct_change"] = (
                    (latest_shares - prev_shares) / prev_shares * 100 if prev_shares else None
                )
                entry["is_new"] = prev_shares == 0
                increased.append(entry)

        reduced.sort(key=lambda r: (not r["sold_out"], r["pct_change"] if r["pct_change"] is not None else 0))
        increased.sort(key=lambda r: (not r["is_new"], -(r["pct_change"] or 0)))

        results.append(
            {
                "fund_name": fund["name"],
                "have_comparison": True,
                "latest_period": latest_filing["period_of_report"],
                "previous_period": previous_filing["period_of_report"],
                "reduced": reduced,
                "increased": increased,
            }
        )

    return results


def get_overlap(conn):
    """
    A stock x fund matrix: one row per stock held by 2+ tracked funds, one
    column per fund, cell = that fund's % of portfolio in that stock (using
    each fund's latest filing). Rows are sorted by number of funds holding
    the stock, descending.

    Columns are ordered by each fund's total 13F portfolio value, biggest
    to smallest, left to right - so a small fund with a huge stake in one
    stock still shows up on the right, not the left. Note this is 13F
    long-US-equity value, not full AUM (macro/multi-strategy funds can run
    much bigger books than their 13F filing shows).

    Each row also gets "weighted_avg_pct": the average of the holding
    funds' % of portfolio in that stock, weighted by each fund's own 13F
    portfolio value - so a big fund's stake counts for more than a small
    fund's. Equivalent to (combined $ these funds hold in the stock) /
    (combined $ these funds manage), averaged only across funds that hold
    it. This is a "how much conviction do these funds have" signal, not
    investment advice - it ignores valuation, correlation between the
    funds' bets, and 13F's own staleness (up to 45 days old).

    Returns (fund_columns, rows). fund_columns is a list of
    {name, total_value} dicts, one per fund, in that display order. Each
    row has a "pct_by_fund" dict keyed by fund name (funds not holding the
    stock are simply absent from the dict).
    """
    cusip_map = _cusip_map_lookup(conn)
    funds, latest = _latest_filing_per_fund(conn)

    # cusip -> {issuer_name, pct_by_fund: {fund_name: pct}}
    by_cusip = {}
    fund_totals = {}
    for fund in funds:
        filing = latest.get(fund["cik"])
        if not filing:
            continue
        holdings = conn.execute(
            "SELECT * FROM holdings WHERE filing_id = ?", (filing["id"],)
        ).fetchall()
        total = sum(h["value_usd"] for h in holdings) or 1
        fund_totals[fund["name"]] = total

        for h in holdings:
            entry = by_cusip.setdefault(
                h["cusip"], {"issuer_name": h["issuer_name"], "pct_by_fund": {}}
            )
            entry["pct_by_fund"][fund["name"]] = h["value_usd"] / total * 100

    rows = []
    for cusip, entry in by_cusip.items():
        if len(entry["pct_by_fund"]) < 2:
            continue

        weight_sum = sum(fund_totals[name] for name in entry["pct_by_fund"])
        weighted_avg_pct = (
            sum(pct * fund_totals[name] for name, pct in entry["pct_by_fund"].items())
            / weight_sum
            if weight_sum
            else 0
        )

        rows.append(
            {
                "ticker": _display_ticker(cusip, cusip_map),
                "company_name": _display_company(cusip, entry["issuer_name"], cusip_map),
                "fund_count": len(entry["pct_by_fund"]),
                "weighted_avg_pct": weighted_avg_pct,
                "pct_by_fund": entry["pct_by_fund"],
            }
        )

    rows.sort(key=lambda r: (r["fund_count"], max(r["pct_by_fund"].values())), reverse=True)

    fund_columns = [
        {"name": name, "total_value": total_value}
        for name, total_value in sorted(fund_totals.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return fund_columns, rows


def get_funds_by_focus(conn):
    """
    Tracked funds grouped by strategy "focus" - a label we assign in
    config.py based on each fund's public reputation (13F filings don't
    reveal strategy, only holdings). Groups are sorted by their combined
    13F portfolio value, descending; funds within a group are sorted the
    same way.
    """
    meta_by_cik = {f["cik"]: f for f in CONFIG_FUNDS}
    funds, latest = _latest_filing_per_fund(conn)

    groups = {}
    for fund in funds:
        meta = meta_by_cik.get(fund["cik"], {})
        focus = meta.get("focus") or "Uncategorized"
        filing = latest.get(fund["cik"])

        total_value, holding_count, period = 0, 0, None
        if filing:
            holdings = conn.execute(
                "SELECT value_usd FROM holdings WHERE filing_id = ?", (filing["id"],)
            ).fetchall()
            total_value = sum(h["value_usd"] for h in holdings)
            holding_count = len(holdings)
            period = filing["period_of_report"]

        groups.setdefault(focus, []).append(
            {
                "cik": fund["cik"],
                "name": fund["name"],
                "known_for": meta.get("known_for", ""),
                "total_value": total_value,
                "holding_count": holding_count,
                "period": period,
            }
        )

    result = []
    for focus, fund_list in groups.items():
        fund_list.sort(key=lambda f: f["total_value"], reverse=True)
        result.append(
            {
                "focus": focus,
                "funds": fund_list,
                "group_total": sum(f["total_value"] for f in fund_list),
            }
        )

    result.sort(key=lambda g: g["group_total"], reverse=True)
    return result
