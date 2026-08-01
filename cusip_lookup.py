"""
Resolves CUSIPs to ticker symbols using the free OpenFIGI API.

We never actually need this to succeed - every holding already has an
issuer name straight from the 13F filing itself (see edgar.py), so a
CUSIP that OpenFIGI can't map just displays without a ticker. Results
are cached forever in the cusip_map table, so a given CUSIP is only
ever looked up once, no matter how many times you refresh.
"""

import time
from datetime import datetime, timezone

import requests

import db
from config import OPENFIGI_API_KEY

_URL = "https://api.openfigi.com/v3/mapping"

# OpenFIGI limits unauthenticated requests to 25/minute, with up to 10 jobs
# per request. A free API key (config.OPENFIGI_API_KEY) raises both limits -
# see https://www.openfigi.com/api for details.
if OPENFIGI_API_KEY:
    _BATCH_SIZE = 100
    _SLEEP_BETWEEN_BATCHES = 0.3
else:
    _BATCH_SIZE = 10
    _SLEEP_BETWEEN_BATCHES = 2.6


def _headers():
    headers = {"Content-Type": "application/json"}
    if OPENFIGI_API_KEY:
        headers["X-OPENFIGI-APIKEY"] = OPENFIGI_API_KEY
    return headers


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def resolve_new_cusips(conn, cusips):
    """
    Look up any CUSIPs not already in cusip_map and store the results
    (including misses, so we don't keep retrying dead CUSIPs on every
    refresh). Safe to call with an empty or all-cached list.
    """
    todo = db.get_uncached_cusips(conn, cusips)
    if not todo:
        return

    print(f"  Resolving {len(todo)} new CUSIP(s) via OpenFIGI...")
    now = datetime.now(timezone.utc).isoformat()

    for batch in _chunks(todo, _BATCH_SIZE):
        jobs = [{"idType": "ID_CUSIP", "idValue": cusip} for cusip in batch]
        try:
            resp = requests.post(_URL, json=jobs, headers=_headers(), timeout=30)
            resp.raise_for_status()
            results = resp.json()
        except requests.RequestException as exc:
            print(f"  OpenFIGI request failed ({exc}); leaving these CUSIPs unmapped for now.")
            time.sleep(_SLEEP_BETWEEN_BATCHES)
            continue

        for cusip, result in zip(batch, results):
            ticker, company_name = None, None
            data = result.get("data")
            if data:
                ticker = data[0].get("ticker")
                company_name = data[0].get("name")
            db.upsert_cusip_map(conn, cusip, ticker, company_name, now)

        conn.commit()
        time.sleep(_SLEEP_BETWEEN_BATCHES)
