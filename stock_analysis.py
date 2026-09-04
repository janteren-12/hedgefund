"""
Quick Stock Analysis: a 6-step heuristic checklist for a single ticker -
business understanding, basic financials, quick valuation, ownership,
industry & risk, and a final pass/watchlist/invest verdict.

Reuses this project's own data instead of duplicating it:
  - edgar.py's SEC EDGAR pipeline for real, sourced Form 4 (insider
    buy/sell) filings - the same functions app.py/fetch_filings.py use,
    just called live for an arbitrary ticker instead of the pre-scoped
    top-N list fetch_filings.py caches locally.
  - db.py's tracked-fund database, to report which of the 20 hedge funds
    in config.py currently hold the stock.
  - queries.py's TRANSACTION_CODE_LABELS/OPEN_MARKET_CODES, so "real"
    open-market insider trades are classified exactly the same way the
    Insider Activity page already does.

Financials, valuation, and ownership percentages come from yfinance
(free, no API key - already a dependency elsewhere in this workspace, in
macro-sector-optimizer/requirements.txt). This project's own data has no
general financial-statement or valuation-multiple source, so yfinance is
the one new data source this module adds.

This checklist was originally written for Indian-market conventions
("promoter holding", "pledging of shares"). Two adjustments:
  - "Promoter holding" is reported here as insider ownership % (the US
    equivalent SEC/Yahoo actually disclose).
  - "Pledging of shares" has no reliable US-market data source and is
    always reported as unavailable - see PLEDGING NOT AVAILABLE below.

Purely qualitative calls this module won't score from an API - moat,
industry growth trend, competitive fragmentation - are left as
"not available" with a pointer to Method 8 (Competitive/Sector Analysis)
or the Legends page in the investment-research-hub web app, which can
actually research and source those.

Usage (CLI):
    python stock_analysis.py AAPL
    python stock_analysis.py AAPL --json report.json

Usage (from Python):
    from stock_analysis import analyze_stock
    result = analyze_stock("AAPL")
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

import yfinance as yf

import db
import edgar
from queries import OPEN_MARKET_CODES, TRANSACTION_CODE_LABELS

# ---------------------------------------------------------------------------
# Thresholds - rough heuristic starting points from a checklist, not
# backtested. All in one place so they're easy to tune later.
# ---------------------------------------------------------------------------
MIN_ROE_PCT = 15.0
MAX_DEBT_TO_EQUITY = 1.0
MIN_NET_MARGIN_PCT = 15.0
MIN_QUICK_RATIO = 1.0
MIN_INTEREST_COVERAGE = 3.0

MAX_PE = 20.0
MAX_PEG = 2.0

# Not pass/fail thresholds - just where this module considers a stake
# "meaningfully large" enough to call out in the one-line note.
MIN_MEANINGFUL_INSIDER_OWNERSHIP_PCT = 5.0
MIN_MEANINGFUL_INSTITUTIONAL_OWNERSHIP_PCT = 30.0

INSIDER_LOOKBACK_DAYS = 90  # same convention as config.INSIDER_FILINGS_WINDOW_DAYS

# More red flags than this -> "Pass" instead of "Watchlist".
MAX_RED_FLAGS_FOR_WATCHLIST = 2

NOT_AVAILABLE = "not available"

_REVENUE_ROW_CANDIDATES = ["Total Revenue", "TotalRevenue"]
_NET_INCOME_ROW_CANDIDATES = ["Net Income", "NetIncome", "Net Income Common Stockholders"]
_EBIT_ROW_CANDIDATES = ["EBIT"]
_INTEREST_EXPENSE_ROW_CANDIDATES = ["Interest Expense", "Interest Expense Non Operating"]


def _metric(value, flag, note):
    """One checklist line item: the raw value, a pass(True)/fail(False)/not-applicable(None) flag, and why."""
    return {"value": value, "flag": flag, "note": note}


def _pct(fraction):
    """yfinance reports most ratios (ROE, margins, ownership %) as a 0-1 fraction."""
    return None if fraction is None else round(fraction * 100, 2)


def _find_row(df, candidates):
    if df is None or df.empty:
        return None
    for name in candidates:
        if name in df.index:
            return df.loc[name]
    return None


def _yoy_growth_series(row):
    """[{period, yoy_pct}, ...] oldest to newest, skipping any year with missing data."""
    if row is None:
        return []
    values = [(str(col.date()) if hasattr(col, "date") else str(col), v) for col, v in row.items() if v == v]
    values.sort(key=lambda pair: pair[0])
    growth = []
    for (_, prev_v), (year, v) in zip(values, values[1:]):
        if prev_v:
            growth.append({"period": year, "yoy_pct": round((v - prev_v) / abs(prev_v) * 100, 1)})
    return growth


# ---------------------------------------------------------------------------
# Section 1: Business Understanding
# ---------------------------------------------------------------------------
def _business_understanding(info):
    ceo = None
    for officer in info.get("companyOfficers") or []:
        title = (officer.get("title") or "").lower()
        if "ceo" in title or "chief executive" in title:
            ceo = officer.get("name")
            break

    summary = info.get("longBusinessSummary")
    short_summary = (summary[:400] + "…") if summary and len(summary) > 400 else summary

    return {
        "company_name": _metric(
            info.get("longName") or info.get("shortName") or NOT_AVAILABLE, None, "Yahoo Finance profile data."
        ),
        "what_it_does": _metric(
            short_summary or NOT_AVAILABLE, None, "Truncated business summary from Yahoo Finance's company profile."
        ),
        "sector": _metric(info.get("sector") or NOT_AVAILABLE, None, "Yahoo Finance sector classification."),
        "industry": _metric(
            info.get("industry") or NOT_AVAILABLE, None, "Yahoo Finance industry classification (finer than sector)."
        ),
        "ceo": _metric(
            ceo or NOT_AVAILABLE,
            None,
            "First listed officer whose title contains \"CEO\"/\"Chief Executive\"." if ceo else "No officer title matched.",
        ),
        "revenue_model": _metric(
            None,
            None,
            "How the company actually makes money isn't automatically derived - read \"what_it_does\" above, or run "
            "Method 8 (Competitive/Sector Analysis) in the investment-research-hub web app for a researched breakdown.",
        ),
        "moat": _metric(
            None,
            None,
            "Competitive moat is a qualitative judgment this module doesn't score from an API - run Method 8 "
            "(Competitive/Sector Analysis) or the Legends page (Buffett/Munger lens) in the investment-research-hub "
            "web app for a sourced assessment.",
        ),
    }


# ---------------------------------------------------------------------------
# Section 2: Basic Financials
# ---------------------------------------------------------------------------
def _basic_financials(t, info):
    financials = t.financials  # annual income statement, most-recent-first columns

    revenue_row = _find_row(financials, _REVENUE_ROW_CANDIDATES)
    net_income_row = _find_row(financials, _NET_INCOME_ROW_CANDIDATES)
    ebit_row = _find_row(financials, _EBIT_ROW_CANDIDATES)
    interest_row = _find_row(financials, _INTEREST_EXPENSE_ROW_CANDIDATES)

    revenue_growth = _yoy_growth_series(revenue_row)
    profit_growth = _yoy_growth_series(net_income_row)

    roe_pct = _pct(info.get("returnOnEquity"))
    # yfinance reports debtToEquity already multiplied by 100 (78.4 means a
    # 0.784 ratio) - confirmed against a live pull, not assumed. Divided back
    # down here so it's a plain ratio matching the < 1 threshold below.
    debt_to_equity_raw = info.get("debtToEquity")
    debt_to_equity = None if debt_to_equity_raw is None else round(debt_to_equity_raw / 100, 2)
    net_margin_pct = _pct(info.get("profitMargins"))
    quick_ratio = info.get("quickRatio")

    interest_coverage = None
    if ebit_row is not None and interest_row is not None:
        for col in financials.columns:
            ebit_v = ebit_row.get(col)
            int_v = interest_row.get(col)
            if ebit_v == ebit_v and int_v == int_v and int_v:  # both non-NaN and interest non-zero
                interest_coverage = round(float(ebit_v) / float(int_v), 2)
                break

    return {
        "revenue_growth_trend": _metric(
            revenue_growth or NOT_AVAILABLE,
            None,
            "Year-over-year revenue change, oldest to newest, from the annual filings yfinance has on file "
            "(typically ~4 years).",
        ),
        "profit_growth_trend": _metric(
            profit_growth or NOT_AVAILABLE, None, "Year-over-year net income change, same basis as revenue above."
        ),
        "roe_pct": _metric(
            roe_pct if roe_pct is not None else NOT_AVAILABLE,
            None if roe_pct is None else roe_pct > MIN_ROE_PCT,
            f"Flag threshold: > {MIN_ROE_PCT}%.",
        ),
        "debt_to_equity": _metric(
            debt_to_equity if debt_to_equity is not None else NOT_AVAILABLE,
            None if debt_to_equity is None else debt_to_equity < MAX_DEBT_TO_EQUITY,
            f"Flag threshold: < {MAX_DEBT_TO_EQUITY} = healthy.",
        ),
        "net_profit_margin_pct": _metric(
            net_margin_pct if net_margin_pct is not None else NOT_AVAILABLE,
            None if net_margin_pct is None else net_margin_pct > MIN_NET_MARGIN_PCT,
            f"Flag threshold: > {MIN_NET_MARGIN_PCT}%.",
        ),
        "quick_ratio": _metric(
            quick_ratio if quick_ratio is not None else NOT_AVAILABLE,
            None if quick_ratio is None else quick_ratio >= MIN_QUICK_RATIO,
            f"Flag threshold: >= {MIN_QUICK_RATIO} (current assets minus inventory comfortably cover current "
            "liabilities).",
        ),
        "interest_coverage_ratio": _metric(
            interest_coverage if interest_coverage is not None else NOT_AVAILABLE,
            None if interest_coverage is None else interest_coverage >= MIN_INTEREST_COVERAGE,
            f"EBIT / interest expense, most recent year both figures are reported. Flag threshold: "
            f">= {MIN_INTEREST_COVERAGE}.",
        ),
    }


# ---------------------------------------------------------------------------
# Section 3: Quick Valuation
# ---------------------------------------------------------------------------
def _quick_valuation(info):
    pe = info.get("trailingPE")
    peg = info.get("trailingPegRatio")
    peg_alt = info.get("pegRatio")
    ev_ebitda = info.get("enterpriseToEbitda")
    pb = info.get("priceToBook")
    # yfinance's dividendYield units have flip-flopped across versions -
    # verified live (2026-08, yfinance 1.6.0) that it's already a plain
    # percentage (0.35 meaning 0.35%), NOT a fraction needing *100. If you
    # upgrade yfinance and a known payer's yield looks 100x off, re-check.
    div_yield_pct = info.get("dividendYield")

    peg_value = peg if peg is not None else peg_alt
    peg_note = f"Flag threshold: < {MAX_PEG} = reasonable."
    if peg is not None and peg_alt is not None and round(peg, 2) != round(peg_alt, 2):
        peg_note += f" Two yfinance fields disagree (trailingPegRatio={peg}, pegRatio={peg_alt}) - used trailingPegRatio."

    return {
        "pe_ratio": _metric(
            pe if pe is not None else NOT_AVAILABLE,
            None if pe is None else pe <= MAX_PE,
            f"Flag threshold: > {MAX_PE} = expensive. Industry-average P/E isn't available from this module's data "
            "sources (would need a sector-constituent dataset this project doesn't have) - checks the absolute "
            "threshold only.",
        ),
        "peg_ratio": _metric(
            peg_value if peg_value is not None else NOT_AVAILABLE,
            None if peg_value is None else peg_value < MAX_PEG,
            peg_note,
        ),
        "ev_to_ebitda": _metric(
            ev_ebitda if ev_ebitda is not None else NOT_AVAILABLE,
            None,
            "No fixed threshold (varies a lot by industry); industry-average comparison not available from current "
            "data sources - informational only.",
        ),
        "price_to_book": _metric(
            pb if pb is not None else NOT_AVAILABLE, None, "No fixed threshold set - informational."
        ),
        "dividend_yield_pct": _metric(
            div_yield_pct if div_yield_pct is not None else 0, None, "Informational; 0 for non-payers."
        ),
    }


# ---------------------------------------------------------------------------
# Section 4: Promoter & Management / Ownership
# ---------------------------------------------------------------------------
def _tracked_fund_holders(conn, ticker):
    """Which of this project's 20 tracked hedge funds hold `ticker`, per each fund's latest stored filing."""
    rows = conn.execute(
        """
        SELECT f.name AS fund_name, h.value_usd
        FROM holdings h
        JOIN filings fi ON fi.id = h.filing_id
        JOIN funds f ON f.cik = fi.fund_cik
        JOIN cusip_map cm ON cm.cusip = h.cusip
        WHERE cm.ticker = ?
          AND fi.id = (
              SELECT fi2.id FROM filings fi2
              WHERE fi2.fund_cik = fi.fund_cik
              ORDER BY fi2.period_of_report DESC LIMIT 1
          )
        ORDER BY h.value_usd DESC
        """,
        (ticker,),
    ).fetchall()
    return [{"fund_name": r["fund_name"], "value_usd": r["value_usd"]} for r in rows]


def _insider_transactions(ticker_cik):
    """Live SEC Form 4 pull for one issuer CIK, via this project's own edgar.py - not scoped to the top-N list."""
    since_date = (datetime.now(timezone.utc) - timedelta(days=INSIDER_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    try:
        filings = edgar.get_recent_insider_filings(ticker_cik, since_date=since_date)
    except Exception as exc:
        return None, f"SEC EDGAR request failed: {exc}"

    transactions = []
    for f in filings:
        try:
            details = edgar.get_insider_filing_details(
                ticker_cik, f["accession_no"], f["primary_document"], f["filed_date"]
            )
        except Exception:
            continue
        for row in details:
            transactions.append(
                {
                    "owner_name": row["owner_name"],
                    "date": row["transaction_date"],
                    "code": row["transaction_code"],
                    "code_label": TRANSACTION_CODE_LABELS.get(row["transaction_code"], row["transaction_code"]),
                    "is_open_market": row["transaction_code"] in OPEN_MARKET_CODES,
                    "acquired_disposed": row["acquired_disposed"],
                    "shares": row["shares"],
                    "price_per_share": row["price_per_share"],
                }
            )
    transactions.sort(key=lambda r: r["date"], reverse=True)
    return transactions, None


def _ownership(conn, ticker, info, ticker_cik):
    insider_pct = _pct(info.get("heldPercentInsiders"))
    institutional_pct = _pct(info.get("heldPercentInstitutions"))
    tracked_holders = _tracked_fund_holders(conn, ticker)

    insider_txns, insider_err = (None, "Could not resolve this ticker's SEC CIK.")
    if ticker_cik:
        insider_txns, insider_err = _insider_transactions(ticker_cik)

    open_market = [tx for tx in (insider_txns or []) if tx["is_open_market"]]
    buys = [tx for tx in open_market if tx["acquired_disposed"] == "A"]
    sells = [tx for tx in open_market if tx["acquired_disposed"] == "D"]

    if insider_txns is None:
        insider_note = insider_err
        insider_flag = None
    elif open_market:
        insider_note = (
            f"{len(buys)} open-market buy(s), {len(sells)} open-market sale(s) in the last "
            f"{INSIDER_LOOKBACK_DAYS} days (SEC Form 4, via this project's own edgar.py)."
        )
        insider_flag = len(buys) >= len(sells)
    elif insider_txns:
        insider_note = (
            f"No open-market insider buys or sells in the last {INSIDER_LOOKBACK_DAYS} days "
            f"({len(insider_txns)} non-open-market Form 4 event(s) only - grants, tax withholding, etc.)."
        )
        insider_flag = None
    else:
        insider_note = f"No Form 4 filings at all in the last {INSIDER_LOOKBACK_DAYS} days."
        insider_flag = None

    return {
        "insider_ownership_pct": _metric(
            insider_pct if insider_pct is not None else NOT_AVAILABLE,
            None if insider_pct is None else insider_pct >= MIN_MEANINGFUL_INSIDER_OWNERSHIP_PCT,
            "US equivalent of \"promoter holding\" - Yahoo Finance ownership data. Higher = better alignment; "
            f"informal threshold >= {MIN_MEANINGFUL_INSIDER_OWNERSHIP_PCT}% just flags a meaningfully large stake, "
            "not a hard pass/fail.",
        ),
        "pledging_pct": _metric(
            NOT_AVAILABLE,
            None,
            "PLEDGING NOT AVAILABLE: no reliable US-market data source for shares pledged as loan collateral - this "
            "line item comes from Indian-market (SEBI) disclosure conventions with no direct US equivalent. Always "
            "reported as unavailable here, on purpose, rather than guessed at.",
        ),
        "institutional_ownership_pct": _metric(
            institutional_pct if institutional_pct is not None else NOT_AVAILABLE,
            None if institutional_pct is None else institutional_pct >= MIN_MEANINGFUL_INSTITUTIONAL_OWNERSHIP_PCT,
            f"Yahoo Finance ownership data (all institutional holders market-wide, not just this project's 20 "
            f"tracked funds below). Informal threshold >= {MIN_MEANINGFUL_INSTITUTIONAL_OWNERSHIP_PCT}%.",
        ),
        "tracked_fund_holders": _metric(
            tracked_holders,
            None,
            f"Held by {len(tracked_holders)} of the 20 hedge funds tracked in this project's own 13F database "
            "(config.py FUNDS), as of each fund's latest stored filing - a different, narrower thing than total "
            "institutional ownership above.",
        ),
        "recent_insider_transactions": _metric(
            insider_txns if insider_txns is not None else NOT_AVAILABLE,
            insider_flag,
            insider_note,
        ),
    }


# ---------------------------------------------------------------------------
# Section 5: Industry & Risk
# ---------------------------------------------------------------------------
def _industry_risk(info):
    beta = info.get("beta")
    return {
        "sector": _metric(info.get("sector") or NOT_AVAILABLE, None, "Yahoo Finance classification."),
        "industry": _metric(
            info.get("industry") or NOT_AVAILABLE, None, "Yahoo Finance classification, finer than sector."
        ),
        "industry_growth_trend": _metric(
            None,
            None,
            "Whether the industry itself is growing, flat, or shrinking is a qualitative judgment this module "
            "doesn't score from an API - run Method 10 (Macro Impact Assessment) or Method 8 (Competitive/Sector "
            "Analysis) in the investment-research-hub web app.",
        ),
        "cyclicality_beta": _metric(
            beta if beta is not None else NOT_AVAILABLE,
            None,
            "Beta vs the broad market (Yahoo Finance) - a rough numeric proxy for cyclicality/macro sensitivity, "
            "not a substitute for knowing the industry's actual business cycle. >1 moves more than the market, "
            "<1 moves less.",
        ),
        "fragmentation": _metric(
            None,
            None,
            "Whether the industry is fragmented (price-war risk) or concentrated (oligopoly/monopoly pricing "
            "power) is a qualitative judgment this module doesn't score from an API - run Method 8 "
            "(Competitive/Sector Analysis) in the web app for a sourced comparison against named competitors.",
        ),
    }


# ---------------------------------------------------------------------------
# Section 6: Final Decision
# ---------------------------------------------------------------------------
def _final_decision(sections):
    red_flags = []
    total_checkable = 0
    total_passed = 0

    for section_name, section in sections.items():
        for field_name, metric in section.items():
            if metric["flag"] is None:
                continue
            total_checkable += 1
            if metric["flag"]:
                total_passed += 1
            else:
                red_flags.append({"section": section_name, "field": field_name, "note": metric["note"]})

    failed = len(red_flags)

    if total_checkable == 0:
        verdict = "Watchlist"
        reason = "No checkable metrics were available - can't form a verdict from data alone."
    elif failed == 0:
        verdict = "Invest"
        reason = (
            f"All {total_checkable} checkable metrics cleared their threshold. Still read the qualitative fields "
            "(moat, industry trend, fragmentation) flagged as needing manual research before acting on this."
        )
    elif failed <= MAX_RED_FLAGS_FOR_WATCHLIST:
        verdict = "Watchlist"
        reason = (
            f"{failed} of {total_checkable} checkable metrics missed their threshold - not disqualifying on its "
            "own (MAX_RED_FLAGS_FOR_WATCHLIST not exceeded), but worth digging into the flagged sections."
        )
    else:
        verdict = "Pass"
        reason = (
            f"{failed} of {total_checkable} checkable metrics missed their threshold - more red flags than "
            f"MAX_RED_FLAGS_FOR_WATCHLIST ({MAX_RED_FLAGS_FOR_WATCHLIST})."
        )

    return {
        "verdict": verdict,
        "reason": reason,
        "checkable_metrics": total_checkable,
        "passed": total_passed,
        "failed": failed,
        "red_flags": red_flags,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def analyze_stock(ticker):
    """
    Run the full 6-step checklist for one ticker and return a structured
    dict: business_understanding, basic_financials, quick_valuation,
    ownership, industry_risk, summary. Each metric within a section is
    {"value", "flag", "note"}. Returns {"ticker", "error"} instead if the
    ticker can't be resolved at all.
    """
    ticker = ticker.strip().upper()

    t = yf.Ticker(ticker)
    try:
        info = t.info
    except Exception as exc:
        return {"ticker": ticker, "error": f"Failed to fetch data from Yahoo Finance: {exc}"}

    if not info or not (info.get("longName") or info.get("shortName")):
        return {"ticker": ticker, "error": f"No data found for ticker '{ticker}' - check the symbol."}

    conn = db.get_connection()

    ticker_cik = None
    try:
        cik_map = edgar.get_ticker_cik_map()
        entry = cik_map.get(ticker)
        ticker_cik = entry["cik"] if entry else None
    except Exception:
        ticker_cik = None

    sections = {
        "business_understanding": _business_understanding(info),
        "basic_financials": _basic_financials(t, info),
        "quick_valuation": _quick_valuation(info),
        "ownership": _ownership(conn, ticker, info, ticker_cik),
        "industry_risk": _industry_risk(info),
    }

    return {
        "ticker": ticker,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **sections,
        "summary": _final_decision(sections),
    }


def main():
    parser = argparse.ArgumentParser(description="Quick Stock Analysis - 6-step heuristic checklist for one ticker.")
    parser.add_argument("ticker", help="Ticker symbol, e.g. AAPL")
    parser.add_argument("--json", metavar="PATH", help="Also write the full result to this file.")
    args = parser.parse_args()

    result = analyze_stock(args.ticker)
    output = json.dumps(result, indent=2, default=str)
    print(output)

    if args.json:
        with open(args.json, "w") as f:
            f.write(output)
        print(f"\nWrote full result to {args.json}", file=sys.stderr)

    sys.exit(1 if "error" in result else 0)


if __name__ == "__main__":
    main()
