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
                    "fund_cik": fund["cik"],
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
        latest_total = sum(r["value_usd"] for r in latest_h.values()) or 1

        reduced, increased = [], []
        for cusip in set(latest_h) | set(previous_h):
            prev_row = previous_h.get(cusip)
            latest_row = latest_h.get(cusip)
            prev_shares = prev_row["shares"] if prev_row else 0
            latest_shares = latest_row["shares"] if latest_row else 0

            if latest_shares == prev_shares:
                continue

            issuer_name = (latest_row or prev_row)["issuer_name"]
            value_usd = latest_row["value_usd"] if latest_row else 0
            entry = {
                "ticker": _display_ticker(cusip, cusip_map),
                "company_name": _display_company(cusip, issuer_name, cusip_map),
                "shares_previous": prev_shares,
                "shares_latest": latest_shares,
                "value_usd": value_usd,
                "pct_of_portfolio": value_usd / latest_total * 100,
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
                "fund_cik": fund["cik"],
                "fund_name": fund["name"],
                "have_comparison": True,
                "latest_period": latest_filing["period_of_report"],
                "previous_period": previous_filing["period_of_report"],
                "reduced": reduced,
                "increased": increased,
            }
        )

    return results


def get_new_bets_leaderboard(conn):
    """
    Every brand-new position (a fund reporting a stock this quarter that
    it didn't hold last quarter) across all tracked funds, ranked by
    dollar size rather than % of portfolio - a different cut from the
    Selling & Buying page: a giant fund's $2B new stake might be 0.3% of
    its book but still a much bigger real commitment than a $50M position
    somewhere else. % of portfolio is included alongside for context.
    """
    leaderboard = [
        {
            "fund_cik": fund["fund_cik"],
            "fund_name": fund["fund_name"],
            "ticker": entry["ticker"],
            "company_name": entry["company_name"],
            "value_usd": entry["value_usd"],
            "pct_of_portfolio": entry["pct_of_portfolio"],
            "period": fund["latest_period"],
        }
        for fund in get_selling_buying_by_fund(conn)
        for entry in fund["increased"]
        if entry.get("is_new")
    ]
    leaderboard.sort(key=lambda r: r["value_usd"], reverse=True)
    return leaderboard


def get_ownership_filings_by_fund(conn):
    """
    For each fund, its Schedule 13D/13G filings (5%+ beneficial ownership
    in a single company) stored since OWNERSHIP_FILINGS_SINCE, sorted by
    stake size (% of class) largest to smallest, with filing date as a
    tiebreaker. Unlike 13F, this isn't a portfolio snapshot - each row is one
    filing event (a fund crossing, or materially changing, a 5%+ stake in
    one company), so a fund with several filings for the same company
    (e.g. an original 13D followed by amendments) shows one row per filing,
    letting you see the stake's history the same way an amendment trail
    would read on EDGAR itself.

    13D means the filer may seek to influence or control the company;
    13G means a passive stake with no such intent - both require crossing
    5% of a single class of the company's stock, unrelated to 13F's
    $100M-AUM reporting threshold.
    """
    cusip_map = _cusip_map_lookup(conn)
    funds = conn.execute("SELECT * FROM funds ORDER BY name").fetchall()

    results = []
    for fund in funds:
        rows = conn.execute(
            "SELECT * FROM ownership_filings WHERE fund_cik = ? "
            "ORDER BY pct_of_class DESC, filed_date DESC",
            (fund["cik"],),
        ).fetchall()

        filings = [
            {
                "ticker": _display_ticker(r["cusip"], cusip_map) if r["cusip"] else None,
                "issuer_name": r["issuer_name"],
                "form": r["form"],
                "type": "13D" if "13D" in r["form"] else "13G",
                "pct_of_class": r["pct_of_class"],
                "shares": r["shares"],
                "event_date": r["event_date"],
                "filed_date": r["filed_date"],
                "sec_url": f"https://www.sec.gov/Archives/edgar/data/{int(fund['cik'])}/{r['accession_no'].replace('-', '')}/",
            }
            for r in rows
        ]

        results.append({"fund_cik": fund["cik"], "fund_name": fund["name"], "filings": filings})

    return results


def get_position_history_by_fund(conn):
    """
    For each fund, a stock x quarter matrix of share counts across every
    stored filing (oldest to newest, left to right) - a longer view than
    Selling & Buying's single quarter-over-quarter comparison, useful for
    spotting a position a fund has been steadily building or trimming over
    several quarters rather than a single move.

    Share count (not dollar value) is used deliberately: value can rise or
    fall purely from price movement even when a fund hasn't traded at all,
    so it would be misleading as a "did they actually buy or sell" signal.

    Rows are sorted by the stock's value in the fund's latest filing,
    descending, so currently-largest positions surface first regardless of
    how far back they first appear.
    """
    cusip_map = _cusip_map_lookup(conn)
    funds = conn.execute("SELECT * FROM funds ORDER BY name").fetchall()

    results = []
    for fund in funds:
        filings = conn.execute(
            "SELECT * FROM filings WHERE fund_cik = ? ORDER BY period_of_report ASC",
            (fund["cik"],),
        ).fetchall()

        periods = [f["period_of_report"] for f in filings]

        if len(filings) < 2:
            results.append(
                {"fund_cik": fund["cik"], "fund_name": fund["name"], "periods": periods, "rows": []}
            )
            continue

        latest_period = periods[-1]

        # cusip -> {issuer_name, shares_by_period: {period: shares}, latest_value}
        by_cusip = {}
        for filing in filings:
            holdings = conn.execute(
                "SELECT * FROM holdings WHERE filing_id = ?", (filing["id"],)
            ).fetchall()
            for h in holdings:
                entry = by_cusip.setdefault(
                    h["cusip"],
                    {"issuer_name": h["issuer_name"], "shares_by_period": {}, "latest_value": 0},
                )
                entry["shares_by_period"][filing["period_of_report"]] = h["shares"]
                if filing["period_of_report"] == latest_period:
                    entry["latest_value"] = h["value_usd"]

        rows = [
            {
                "ticker": _display_ticker(cusip, cusip_map),
                "company_name": _display_company(cusip, entry["issuer_name"], cusip_map),
                "shares_by_period": entry["shares_by_period"],
                "latest_value": entry["latest_value"],
            }
            for cusip, entry in by_cusip.items()
        ]
        rows.sort(key=lambda r: r["latest_value"], reverse=True)

        results.append(
            {"fund_cik": fund["cik"], "fund_name": fund["name"], "periods": periods, "rows": rows}
        )

    return results


def get_momentum_matrix(conn):
    """
    A stock x fund matrix showing how much each fund's portfolio
    allocation to a stock changed between its two most recent stored
    filings - the same latest-vs-previous comparison the Selling & Buying
    page uses, just pivoted into Overlap's grid format so multiple funds'
    moves in the same stock are visible side by side.

    A cell is the change in percentage points of that fund's own 13F
    portfolio devoted to the stock (e.g. 2% of the portfolio last quarter
    to 12% this quarter shows as "+10.0%"). This is a portfolio-weight
    change, not a share-count change - it can be nudged by the stock's own
    price move as well as by actual buying/selling, same caveat as the
    Overlap page's percentages. A row only appears here if a fund's share
    count actually changed, though, so every row reflects real trading
    activity, not just a price move on an untouched position.

    Only stocks where at least one tracked fund's position changed are
    included (unlike Overlap, which shows every 2+-fund holding regardless
    of whether it moved).

    Returns (fund_columns, rows), same shape as get_overlap(). fund_columns
    is ordered by each fund's latest 13F portfolio value, descending; rows
    are sorted by how many funds moved on that stock, descending (ties
    broken by the single biggest swing, up or down, in percentage-point
    terms).
    """
    cusip_map = _cusip_map_lookup(conn)
    funds = conn.execute("SELECT * FROM funds ORDER BY name").fetchall()

    by_cusip = {}
    fund_totals = {}

    for fund in funds:
        filings = conn.execute(
            "SELECT * FROM filings WHERE fund_cik = ? ORDER BY period_of_report DESC",
            (fund["cik"],),
        ).fetchall()
        if len(filings) < 2:
            continue

        latest_filing, previous_filing = filings[0], filings[1]

        def holdings_by_cusip(filing_id):
            rows = conn.execute(
                "SELECT * FROM holdings WHERE filing_id = ?", (filing_id,)
            ).fetchall()
            return {r["cusip"]: r for r in rows}

        latest_h = holdings_by_cusip(latest_filing["id"])
        previous_h = holdings_by_cusip(previous_filing["id"])
        latest_total = sum(r["value_usd"] for r in latest_h.values()) or 1
        previous_total = sum(r["value_usd"] for r in previous_h.values()) or 1
        fund_totals[fund["name"]] = latest_total

        for cusip in set(latest_h) | set(previous_h):
            prev_row = previous_h.get(cusip)
            latest_row = latest_h.get(cusip)
            prev_shares = prev_row["shares"] if prev_row else 0
            latest_shares = latest_row["shares"] if latest_row else 0
            if latest_shares == prev_shares:
                continue

            prev_pct = (prev_row["value_usd"] / previous_total * 100) if prev_row else 0
            latest_pct = (latest_row["value_usd"] / latest_total * 100) if latest_row else 0
            issuer_name = (latest_row or prev_row)["issuer_name"]

            if latest_shares == 0:
                direction = "sold_out"
            elif prev_shares == 0:
                direction = "new"
            elif latest_pct >= prev_pct:
                direction = "increase"
            else:
                direction = "decrease"

            entry = by_cusip.setdefault(cusip, {"issuer_name": issuer_name, "changes_by_fund": {}})
            entry["changes_by_fund"][fund["name"]] = {
                "pct_point_change": latest_pct - prev_pct,
                "direction": direction,
            }

    rows = []
    for cusip, entry in by_cusip.items():
        max_abs_change = max(abs(c["pct_point_change"]) for c in entry["changes_by_fund"].values())
        rows.append(
            {
                "ticker": _display_ticker(cusip, cusip_map),
                "company_name": _display_company(cusip, entry["issuer_name"], cusip_map),
                "fund_count": len(entry["changes_by_fund"]),
                "changes_by_fund": entry["changes_by_fund"],
                "max_abs_change": max_abs_change,
            }
        )
    rows.sort(key=lambda r: (r["fund_count"], r["max_abs_change"]), reverse=True)

    fund_columns = [
        {"name": name, "total_value": total_value}
        for name, total_value in sorted(fund_totals.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return fund_columns, rows


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


# SEC's standard Form 4 transaction codes, in plain language. Only P and S
# are genuine open-market decisions - most of the rest are routine
# compensation or tax mechanics that happen to also show up as Form 4s.
TRANSACTION_CODE_LABELS = {
    "P": "Open market purchase",
    "S": "Open market sale",
    "A": "Grant or award",
    "D": "Disposition to the issuer",
    "F": "Tax withholding",
    "M": "Option exercise",
    "C": "Derivative conversion",
    "G": "Gift",
    "V": "Voluntarily reported early",
    "I": "Discretionary transaction",
    "L": "Small acquisition (Rule 16a-6)",
    "W": "Acquired/disposed by will or inheritance",
    "Z": "Voting trust deposit/withdrawal",
    "U": "Disposed via tender in change of control",
    "J": "Other (see filing footnotes)",
}
OPEN_MARKET_CODES = {"P", "S"}


def _insider_role(row):
    roles = []
    if row["is_officer"]:
        roles.append(row["owner_title"] or "Officer")
    if row["is_director"]:
        roles.append("Director")
    if row["is_ten_percent_owner"]:
        roles.append("10%+ Owner")
    return ", ".join(roles) if roles else "Other"


def get_insider_activity(conn):
    """
    Insider (officer/director/10%+ owner) buy/sell activity in the stocks
    your tracked funds hold most widely - Form 3/4/5 filings, a completely
    different disclosure regime from 13F/13D/13G: these are filed by a
    COMPANY's own executives and directors about their own personal
    trades, not by any of the hedge funds this app otherwise tracks.
    Scoped to INSIDER_TRACKING_TOP_N companies (config.py) - the stocks
    held by the most of your tracked funds, per Overlap's own ranking -
    and a rolling INSIDER_FILINGS_WINDOW_DAYS window, since insider
    trading is far higher-frequency than 13F/13D/13G.

    Only non-derivative transactions (actual common stock, not options or
    RSUs) are included. Every transaction code is shown, not just P (open
    market purchase) and S (open market sale) - most Form 4s are routine
    compensation events (grants, tax withholding, option exercises), not a
    genuine market decision, so each row is labeled in plain English
    rather than just the SEC's letter code.

    Returns a list of {ticker, issuer_name, fund_count, transactions},
    sorted by fund_count descending (the same "how widely held" ranking
    Overlap uses); each company's transactions are sorted by date, most
    recent first.
    """
    rows = conn.execute("SELECT * FROM insider_filings ORDER BY transaction_date DESC").fetchall()

    by_issuer = {}
    for r in rows:
        entry = by_issuer.setdefault(
            r["issuer_cik"], {"ticker": r["ticker"], "issuer_name": r["issuer_name"], "transactions": []}
        )
        code = r["transaction_code"]
        entry["transactions"].append(
            {
                "owner_name": r["owner_name"],
                "role": _insider_role(r),
                "transaction_date": r["transaction_date"],
                "code": code,
                "code_label": TRANSACTION_CODE_LABELS.get(code, code),
                "is_open_market": code in OPEN_MARKET_CODES,
                "acquired_disposed": r["acquired_disposed"],
                "shares": r["shares"],
                "price_per_share": r["price_per_share"],
                "shares_owned_after": r["shares_owned_after"],
            }
        )

    _, overlap_rows = get_overlap(conn)
    fund_count_by_ticker = {row["ticker"]: row["fund_count"] for row in overlap_rows}

    results = [
        {
            "ticker": entry["ticker"],
            "issuer_name": entry["issuer_name"],
            "fund_count": fund_count_by_ticker.get(entry["ticker"], 0),
            "transactions": entry["transactions"],
        }
        for entry in by_issuer.values()
    ]
    results.sort(key=lambda r: r["fund_count"], reverse=True)
    return results


def get_trivest_portfolio(conn):
    """
    Trivest Advisors Ltd's latest stored 13F-HR holdings - a standalone
    page, not one of the tracked FUNDS, so this reads from its own
    trivest_filings/trivest_holdings tables rather than the shared ones
    every other view uses. Same row shape as get_positions_by_fund()'s
    per-fund rows: ticker, company, value, % of portfolio, sorted by %
    descending.
    """
    cusip_map = _cusip_map_lookup(conn)

    filing = conn.execute(
        "SELECT * FROM trivest_filings ORDER BY period_of_report DESC LIMIT 1"
    ).fetchone()
    if not filing:
        return {"period": None, "filed_date": None, "rows": []}

    holdings = conn.execute(
        "SELECT * FROM trivest_holdings WHERE filing_id = ?", (filing["id"],)
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

    return {
        "period": filing["period_of_report"],
        "filed_date": filing["filed_date"],
        "total_value": total,
        "rows": rows,
    }
