"""
Low-level helpers for talking to SEC EDGAR.

Every request goes through _get(), which:
  - sends the required User-Agent header (name + email)
  - sleeps briefly after every call to stay under SEC's 10 requests/second
    limit (we go well under that - about 5/sec - to be polite)

Two things this module knows how to fetch:
  1. A fund's filing history (submissions JSON)
  2. A single 13F-HR filing's holdings (the "information table" XML)
"""

import statistics
import time
import xml.etree.ElementTree as ET
from collections import defaultdict

import requests

from config import SEC_USER_AGENT

_MIN_INTERVAL = 0.2  # seconds between requests (~5 req/sec, under the 10/sec cap)
_last_request_time = 0.0


def _throttle():
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_request_time = time.time()


def _get(url):
    _throttle()
    resp = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp


def pad_cik(cik):
    return str(cik).strip().zfill(10)


def get_submissions(cik):
    """Fetch a fund's filing history from data.sec.gov."""
    url = f"https://data.sec.gov/submissions/CIK{pad_cik(cik)}.json"
    return _get(url).json()


def get_recent_13f_hr_filings(cik, limit=2):
    """
    Return up to `limit` most recent 13F-HR filings for a fund, as dicts with
    accession_no, period_of_report, filed_date - newest first.

    Only looks at the "recent" filings block, which covers at least the last
    year (plenty for actively-filing funds). Amendments (13F-HR/A) are skipped
    since they report the same period as the original and would complicate
    quarter-over-quarter comparisons.
    """
    data = get_submissions(cik)
    recent = data["filings"]["recent"]
    matches = []
    for i, form in enumerate(recent["form"]):
        if form == "13F-HR":
            matches.append(
                {
                    "accession_no": recent["accessionNumber"][i],
                    "period_of_report": recent["reportDate"][i],
                    "filed_date": recent["filingDate"][i],
                }
            )
    matches.sort(key=lambda f: f["period_of_report"], reverse=True)
    return matches[:limit]


def _filing_index_url(cik, accession_no):
    accession_nodash = accession_no.replace("-", "")
    cik_nodash = str(int(cik))  # archive paths use the CIK without leading zeros
    return f"https://www.sec.gov/Archives/edgar/data/{cik_nodash}/{accession_nodash}/index.json"


def _strip_ns(tag):
    return tag.split("}")[-1] if "}" in tag else tag


def find_infotable_document_url(cik, accession_no):
    """
    Locate the information-table XML document within a 13F-HR filing.
    Filers name this file differently, so we first look for "infotable" in
    the filename, and fall back to inspecting each XML file's root tag.
    """
    index = _get(_filing_index_url(cik, accession_no)).json()
    items = index["directory"]["item"]
    accession_nodash = accession_no.replace("-", "")
    cik_nodash = str(int(cik))
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_nodash}/{accession_nodash}"

    xml_items = [item for item in items if item["name"].lower().endswith(".xml")]

    for item in xml_items:
        if "infotable" in item["name"].lower():
            return f"{base}/{item['name']}"

    # Fall back: download each XML candidate and check its root element.
    for item in xml_items:
        if item["name"].lower() == "primary_doc.xml":
            continue
        url = f"{base}/{item['name']}"
        try:
            root = ET.fromstring(_get(url).content)
        except ET.ParseError:
            continue
        if _strip_ns(root.tag).lower() == "informationtable":
            return url

    return None


def parse_infotable(xml_bytes):
    """
    Parse a 13F information-table XML document into a list of holdings:
    [{cusip, issuer_name, value_usd, shares}, ...]

    Rows are aggregated by CUSIP, since a filer can report the same security
    across multiple rows (e.g. split by voting authority).
    """
    root = ET.fromstring(xml_bytes)
    aggregated = defaultdict(lambda: {"issuer_name": "", "value_usd": 0, "shares": 0})

    for info_table in root.iter():
        if _strip_ns(info_table.tag).lower() != "infotable":
            continue

        fields = {}
        for child in info_table.iter():
            name = _strip_ns(child.tag)
            if name == "sshPrnamt":
                fields["shares"] = child.text
            elif name in ("nameOfIssuer", "cusip", "value"):
                fields[name] = child.text

        cusip = (fields.get("cusip") or "").strip()
        if not cusip:
            continue

        # Filings from 2023 onward are supposed to report value in whole
        # dollars (SEC's 2023 structured-data amendment) rather than the
        # old $ thousands - but not every filing agent's software actually
        # complies, so we can't just trust it (see the unit-fixup below).
        value_usd = int(float(fields.get("value", 0) or 0))
        shares = int(float(fields.get("shares", 0) or 0))

        entry = aggregated[cusip]
        entry["issuer_name"] = fields.get("nameOfIssuer", "").strip() or entry["issuer_name"]
        entry["value_usd"] += value_usd
        entry["shares"] += shares

    entries = [{"cusip": cusip, **values} for cusip, values in aggregated.items()]

    # Detect filings that still report value in $ thousands despite the
    # 2023 rule: a real portfolio's median implied share price should be a
    # few dollars at least, so a median under $1 means the whole filing is
    # off by 1000x.
    implied_prices = [e["value_usd"] / e["shares"] for e in entries if e["shares"] > 0]
    if implied_prices and statistics.median(implied_prices) < 1.0:
        for e in entries:
            e["value_usd"] *= 1000

    return entries


def get_filing_holdings(cik, accession_no):
    """Fetch and parse the holdings for one 13F-HR filing."""
    doc_url = find_infotable_document_url(cik, accession_no)
    if doc_url is None:
        return []
    xml_bytes = _get(doc_url).content
    return parse_infotable(xml_bytes)
