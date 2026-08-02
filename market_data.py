"""
Fetches real historical stock prices from Yahoo Finance's public chart
endpoint (no API key needed) - used only for funds that have a
"public_ticker" set in config.py (see the note there on why that's
almost never applicable: most hedge funds are private and don't have a
real, public price at all).
"""

import time
from datetime import datetime, timedelta, timezone

import requests

_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; 13F-Tracker/1.0)"}


def get_close_price_near(ticker, target_date_str):
    """
    Returns the closing price on the trading day closest to
    target_date_str (an ISO date string, e.g. a 13F period_of_report).
    Looks in a +/- 5 day window to skip past weekends/holidays. Returns
    None if the ticker or window has no data.
    """
    target = datetime.strptime(target_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    period1 = int((target - timedelta(days=5)).timestamp())
    period2 = int((target + timedelta(days=5)).timestamp())

    resp = requests.get(
        _URL.format(ticker=ticker),
        params={"period1": period1, "period2": period2, "interval": "1d"},
        headers=_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"]
    if not result:
        return None

    timestamps = result[0]["timestamp"]
    closes = result[0]["indicators"]["quote"][0]["close"]

    best_price, best_diff = None, None
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        day = datetime.fromtimestamp(ts, tz=timezone.utc)
        diff = abs((day - target).total_seconds())
        if best_diff is None or diff < best_diff:
            best_price, best_diff = close, diff

    return best_price


def compute_return(ticker, period_start, period_end):
    """
    Real price return for `ticker` between two 13F period_of_report
    dates, plus that same return compounded up to an annual rate. Returns
    None if either price couldn't be found.
    """
    price_start = get_close_price_near(ticker, period_start)
    time.sleep(0.2)
    price_end = get_close_price_near(ticker, period_end)

    if price_start is None or price_end is None or price_start == 0:
        return None

    period_return = (price_end - price_start) / price_start

    # Our two stored filings are one quarter apart, so ^4 approximates a
    # year - but this is one quarter's move blown up to an annual rate,
    # not a real multi-quarter track record. Flagged clearly in the UI.
    annualized_return = (1 + period_return) ** 4 - 1

    return {
        "price_start": price_start,
        "price_end": price_end,
        "period_return_pct": period_return * 100,
        "annualized_return_pct": annualized_return * 100,
    }
