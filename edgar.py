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

import re
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


def get_recent_13f_hr_filings(cik, limit=2, submissions_data=None):
    """
    Return up to `limit` most recent 13F-HR filings for a fund, as dicts with
    accession_no, period_of_report, filed_date - newest first.

    Only looks at the "recent" filings block, which covers at least the last
    year (plenty for actively-filing funds). Amendments (13F-HR/A) are skipped
    since they report the same period as the original and would complicate
    quarter-over-quarter comparisons.

    Pass `submissions_data` (the result of an earlier get_submissions(cik)
    call) to skip a redundant fetch if the caller already has it - e.g.
    fetch_filings.py also needs it for get_recent_ownership_filings().
    """
    data = submissions_data or get_submissions(cik)
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


# EDGAR has used two different labels for the same form types over time:
# the old abbreviated "SC 13D"/"SC 13G", and "SCHEDULE 13D"/"SCHEDULE 13G"
# for newer filings (the switch lines up with the Dec 2024 structured-XML
# mandate). Both need to be matched or recent filings get silently missed.
_OWNERSHIP_FORMS = {
    "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
    "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A",
}


def get_recent_ownership_filings(cik, since_date, submissions_data=None):
    """
    Return every Schedule 13D/13G (5%+ beneficial ownership) filing for a
    fund on or after `since_date`, as dicts with accession_no, form,
    filed_date - newest first. Unlike get_recent_13f_hr_filings, there's no
    "keep the latest N" pruning here: these filings are rare (a fund only
    files when it crosses, or materially changes, a 5%+ stake in some
    company), so there's no volume problem with keeping all of them.

    Pass `submissions_data` to reuse an already-fetched get_submissions(cik)
    result instead of fetching it again.
    """
    data = submissions_data or get_submissions(cik)
    recent = data["filings"]["recent"]
    matches = []
    for i, form in enumerate(recent["form"]):
        if form in _OWNERSHIP_FORMS and recent["filingDate"][i] >= since_date:
            matches.append(
                {
                    "accession_no": recent["accessionNumber"][i],
                    "form": form,
                    "filed_date": recent["filingDate"][i],
                }
            )
    matches.sort(key=lambda f: f["filed_date"], reverse=True)
    return matches


def _ownership_document_url(cik, accession_no):
    """
    The raw structured XML for a Schedule 13D/13G always seems to live at
    this fixed path (unlike 13F's infotable, which filers name freely) -
    verified against live EDGAR filings. find_ownership_document_url()
    falls back to the filing index if that ever turns out wrong for some
    filer's software.
    """
    accession_nodash = accession_no.replace("-", "")
    cik_nodash = str(int(cik))
    return f"https://www.sec.gov/Archives/edgar/data/{cik_nodash}/{accession_nodash}/primary_doc.xml"


def find_ownership_document_url(cik, accession_no):
    direct_url = _ownership_document_url(cik, accession_no)
    try:
        resp = _get(direct_url)
        if "xml" in resp.headers.get("Content-Type", "").lower() or resp.content.strip().startswith(b"<?xml"):
            return direct_url
    except requests.HTTPError:
        pass

    index = _get(_filing_index_url(cik, accession_no)).json()
    items = index["directory"]["item"]
    accession_nodash = accession_no.replace("-", "")
    cik_nodash = str(int(cik))
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_nodash}/{accession_nodash}"

    for item in items:
        if item["name"].lower() == "primary_doc.xml":
            return f"{base}/{item['name']}"

    for item in items:
        if not item["name"].lower().endswith(".xml"):
            continue
        url = f"{base}/{item['name']}"
        try:
            root = ET.fromstring(_get(url).content)
        except ET.ParseError:
            continue
        if "schedule13" in root.tag.lower():
            return url

    return None


def _find_all(root, tag_name):
    """All descendants (any depth) whose namespace-stripped tag matches."""
    return [el for el in root.iter() if _strip_ns(el.tag) == tag_name]


def _field(el, tag_name):
    """
    First value within el's own subtree (inclusive) matching tag_name.
    Handles both flat leaf elements (<tag>value</tag>, used by 13D/13G and
    some Form 4 fields) and EDGAR's "wrapped value" convention
    (<tag><value>value</value></tag>, used elsewhere in Form 4 so a field
    can carry a footnote reference instead of a value) - tries the wrapped
    form first, then falls back to the element's own text.
    """
    for child in el.iter():
        if _strip_ns(child.tag) == tag_name:
            value_child = child.find("value")
            if value_child is not None:
                return value_child.text
            return child.text
    return None


def _is_true(text):
    return text is not None and text.strip().lower() in ("true", "1")


def _names_match(fund_name, person_name):
    """
    Best-effort match between our config.py fund name (e.g. "Point72") and
    the legal entity name SEC has on file (e.g. "Point72 Asset Management,
    L.P."), used only for Schedule 13G, which - unlike 13D - doesn't give a
    CIK per reporting person. Every word in our fund name (minus generic
    corporate words) must appear as a whole word in the filing's name. This
    is only asked to disambiguate between the handful of related entities
    in one joint filing already tied to our fund's own CIK, not to search
    all of EDGAR, so it doesn't need to be more precise than that.
    """
    noise = {"management", "capital", "management,", "l.p.", "llc", "lp", "inc", "group", "the"}
    fund_words = set(re.findall(r"[a-z0-9]+", fund_name.lower())) - noise
    person_words = set(re.findall(r"[a-z0-9]+", person_name.lower()))
    return bool(fund_words) and fund_words.issubset(person_words)


def parse_ownership_filing(xml_bytes, fund_cik, fund_name):
    """
    Parse a structured Schedule 13D/13G XML and pull out the row for our
    tracked fund specifically - a joint filing can list several related
    entities (a management company, its funds, a general partner, an
    individual) each with their own reported % of class, so we can't just
    take the first one.

    13D gives a CIK per reporting person, so that's matched exactly. 13G
    doesn't (SEC's schema just omits it), so falls back to name matching
    (see _names_match) - best-effort, since legal entity names on filings
    don't always match our config.py fund names exactly.

    Returns None if the schema isn't recognized, or if we can't confidently
    identify which row is ours - it's better to skip a filing than to
    misattribute someone else's stake to our fund.
    """
    root = ET.fromstring(xml_bytes)
    is_13g = "schedule13g" in root.tag.lower()

    cover = next(iter(_find_all(root, "coverPageHeader")), None)
    if cover is None:
        return None

    issuer_cik = _field(cover, "issuerCIK") or _field(cover, "issuerCik")
    issuer_name = _field(cover, "issuerName")
    cusip = _field(cover, "issuerCusipNumber")
    event_date = _field(cover, "dateOfEvent") or _field(cover, "eventDateRequiresFilingThisStatement")
    if not issuer_name:
        return None

    if is_13g:
        person_blocks = _find_all(root, "coverPageHeaderReportingPersonDetails")
    else:
        person_blocks = _find_all(root, "reportingPersonInfo")

    matched = None
    fund_cik_int = str(int(fund_cik))
    for block in person_blocks:
        person_cik = _field(block, "reportingPersonCIK")
        if person_cik and str(int(person_cik)) == fund_cik_int:
            matched = block
            break

    if matched is None:
        for block in person_blocks:
            person_name = _field(block, "reportingPersonName") or ""
            if _names_match(fund_name, person_name):
                matched = block
                break

    if matched is None:
        return None

    pct_text = _field(matched, "percentOfClass") or _field(matched, "classPercent")
    shares_text = _field(matched, "aggregateAmountOwned") or _field(
        matched, "reportingPersonBeneficiallyOwnedAggregateNumberOfShares"
    )

    try:
        pct_of_class = float(pct_text) if pct_text is not None else None
    except ValueError:
        pct_of_class = None
    try:
        shares = float(shares_text) if shares_text is not None else None
    except ValueError:
        shares = None

    # The XML gives dates as MM/DD/YYYY; normalize to ISO like every other
    # date field in the app (filed_date, period_of_report) for consistent
    # sorting and display.
    if event_date and re.match(r"^\d{2}/\d{2}/\d{4}$", event_date):
        month, day, year = event_date.split("/")
        event_date = f"{year}-{month}-{day}"

    return {
        "issuer_cik": issuer_cik,
        "issuer_name": issuer_name.strip(),
        "cusip": (cusip or "").strip(),
        "pct_of_class": pct_of_class,
        "shares": shares,
        "event_date": event_date,
    }


def get_ownership_filing_details(cik, accession_no, fund_cik, fund_name):
    """Fetch and parse one Schedule 13D/13G filing for our fund's own row."""
    doc_url = find_ownership_document_url(cik, accession_no)
    if doc_url is None:
        return None
    xml_bytes = _get(doc_url).content
    return parse_ownership_filing(xml_bytes, fund_cik, fund_name)


def get_ticker_cik_map():
    """
    SEC's official ticker -> CIK mapping (every exchange-listed ticker),
    used to resolve a stock's ticker (already known from Overlap/cusip_map)
    to the issuer's own SEC CIK so we can look up its insider filings -
    those are filed under the company's CIK as issuer, not under any of
    our tracked funds.
    """
    data = _get("https://www.sec.gov/files/company_tickers.json").json()
    return {
        entry["ticker"].upper(): {"cik": str(entry["cik_str"]), "name": entry["title"]}
        for entry in data.values()
    }


_INSIDER_FORMS = {"3", "3/A", "4", "4/A", "5", "5/A"}


def get_recent_insider_filings(issuer_cik, since_date, submissions_data=None):
    """
    Every Form 3/4/5 (insider ownership/transaction report) filed against
    one company, on or after `since_date`, newest first. Unlike 13F or
    13D/13G, these are filed by the company's own officers, directors, and
    10%+ owners about their personal holdings - a different disclosure
    regime entirely, just cross-referenced under the issuer's own CIK by
    SEC's system the same way 13D/13G filings are.
    """
    data = submissions_data or get_submissions(issuer_cik)
    recent = data["filings"]["recent"]
    matches = []
    for i, form in enumerate(recent["form"]):
        if form in _INSIDER_FORMS and recent["filingDate"][i] >= since_date:
            matches.append(
                {
                    "accession_no": recent["accessionNumber"][i],
                    "form": form,
                    "filed_date": recent["filingDate"][i],
                    "primary_document": recent["primaryDocument"][i],
                }
            )
    matches.sort(key=lambda f: f["filed_date"], reverse=True)
    return matches


def find_insider_document_url(issuer_cik, accession_no, primary_document):
    """
    submissions.json's primaryDocument field points at a rendered viewer
    path (e.g. "xslF345X06/form4.xml") - the raw XML actually sits at the
    accession folder's root, under just the filename (the last path
    segment) - verified against live EDGAR filings. Falls back to the
    filing index if that ever turns out wrong for some filer's software.
    """
    filename = primary_document.rsplit("/", 1)[-1]
    accession_nodash = accession_no.replace("-", "")
    cik_nodash = str(int(issuer_cik))
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_nodash}/{accession_nodash}"
    direct_url = f"{base}/{filename}"

    try:
        resp = _get(direct_url)
        if resp.content.strip().startswith(b"<?xml") or resp.content.strip().startswith(b"<ownershipDocument"):
            return direct_url
    except requests.HTTPError:
        pass

    index = _get(_filing_index_url(issuer_cik, accession_no)).json()
    items = index["directory"]["item"]
    for item in items:
        if item["name"].lower() == filename.lower():
            return f"{base}/{item['name']}"

    for item in items:
        if not item["name"].lower().endswith(".xml"):
            continue
        url = f"{base}/{item['name']}"
        try:
            root = ET.fromstring(_get(url).content)
        except ET.ParseError:
            continue
        if _strip_ns(root.tag) == "ownershipDocument":
            return url

    return None


def parse_form4(xml_bytes):
    """
    Parse a structured Form 3/4/5 XML into the issuer, the reporting
    owner (their name and whether they're an officer/director/10%+
    owner), and their non-derivative transactions - i.e. trades in actual
    common stock, not options or RSUs (the derivative table is skipped;
    equity-compensation mechanics aren't "insider bought/sold the stock"
    in the way most people mean that phrase).

    Only the first <reportingOwner> block is used - the large majority of
    Form 4s have exactly one; a handful of joint filings (e.g. an
    individual plus a trust they control) would only show the first
    here.

    Returns None if the document isn't recognized, or reports no
    non-derivative transactions at all (e.g. a Form 4 that only changed
    derivative/option holdings).
    """
    root = ET.fromstring(xml_bytes)
    if _strip_ns(root.tag) != "ownershipDocument":
        return None

    issuer = next(iter(_find_all(root, "issuer")), None)
    owner = next(iter(_find_all(root, "reportingOwner")), None)
    if issuer is None or owner is None:
        return None

    issuer_cik = _field(issuer, "issuerCik")
    issuer_name = _field(issuer, "issuerName")
    ticker = _field(issuer, "issuerTradingSymbol")
    if not issuer_cik or not issuer_name:
        return None

    owner_name = _field(owner, "rptOwnerName")
    owner_title = _field(owner, "officerTitle")
    is_officer = _is_true(_field(owner, "isOfficer"))
    is_director = _is_true(_field(owner, "isDirector"))
    is_ten_percent_owner = _is_true(_field(owner, "isTenPercentOwner"))

    def _to_float(text):
        try:
            return float(text) if text is not None else None
        except ValueError:
            return None

    transactions = []
    for txn in _find_all(root, "nonDerivativeTransaction"):
        transaction_date = _field(txn, "transactionDate")
        transaction_code = _field(txn, "transactionCode")
        if not transaction_date or not transaction_code:
            continue

        transactions.append(
            {
                "owner_name": (owner_name or "").strip(),
                "owner_title": (owner_title or "").strip() or None,
                "is_officer": 1 if is_officer else 0,
                "is_director": 1 if is_director else 0,
                "is_ten_percent_owner": 1 if is_ten_percent_owner else 0,
                "transaction_date": transaction_date,
                "transaction_code": transaction_code,
                "acquired_disposed": _field(txn, "transactionAcquiredDisposedCode") or "",
                "shares": _to_float(_field(txn, "transactionShares")),
                "price_per_share": _to_float(_field(txn, "transactionPricePerShare")),
                "shares_owned_after": _to_float(_field(txn, "sharesOwnedFollowingTransaction")),
            }
        )

    if not transactions:
        return None

    return {
        "issuer_cik": issuer_cik,
        "issuer_name": issuer_name.strip(),
        "ticker": (ticker or "").strip() or None,
        "transactions": transactions,
    }


def get_insider_filing_details(issuer_cik, accession_no, primary_document, filed_date):
    """Fetch and parse one Form 3/4/5 filing into a list of DB-ready transaction rows."""
    doc_url = find_insider_document_url(issuer_cik, accession_no, primary_document)
    if doc_url is None:
        return []
    xml_bytes = _get(doc_url).content
    parsed = parse_form4(xml_bytes)
    if parsed is None:
        return []
    return [
        {
            "issuer_cik": parsed["issuer_cik"],
            "issuer_name": parsed["issuer_name"],
            "ticker": parsed["ticker"],
            "accession_no": accession_no,
            "filed_date": filed_date,
            **txn,
        }
        for txn in parsed["transactions"]
    ]
