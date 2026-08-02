"""
Settings for the 13F tracker.

SEC_USER_AGENT: SEC requires every request to identify who is making it
(name + email). Requests without this header get blocked. This is not a
login or API key - just a courtesy header SEC asks all automated tools
to send.

FUNDS: the list of hedge funds to track, identified by their SEC CIK
number (a unique ID SEC assigns to every filer). To add a fund:
  1. Go to https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany
  2. Search the fund's name
  3. Copy the CIK number shown next to it
  4. Add a new entry below - "focus" and "known_for" are optional but
     feed the "By Strategy" page; leave them out (or set focus to
     "Uncategorized") if you don't know how to classify a fund.

The CIK can be entered with or without leading zeros - fetch_filings.py
will pad it to 10 digits automatically.

Note: "focus" is not derived from the 13F data - it's just our own
label based on each fund's public reputation, since 13F filings only
show holdings, not strategy or intent.

"public_ticker" (optional): most hedge funds are private and don't
report real returns anywhere public - only add this for a fund that
genuinely trades as a public vehicle (e.g. Berkshire Hathaway's BRK-B
stock, or a listed closed-end fund like Pershing Square Holdings). When
set, fetch_filings.py pulls real closing prices for that ticker on our
two stored filing dates and computes a real, verifiable return - not an
estimate. Leave it out for private funds; the By Strategy page will show
"Not publicly disclosed" for those instead of a fabricated number.
"""

import os

# Can be overridden with an environment variable of the same name - handy
# if you'd rather not commit your email address into a public repo (e.g.
# if you deploy this on Render).
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Jannik Brose jannikbrose1@gmail.com")

FUNDS = [
    {
        "cik": "1067983",
        "name": "Berkshire Hathaway",
        "focus": "Value / Concentrated Equity",
        "known_for": "Warren Buffett - long-term value investing, concentrated bets",
        "public_ticker": "BRK-B",
    },
    {
        "cik": "1350694",
        "name": "Bridgewater Associates",
        "focus": "Global Macro",
        "known_for": 'Ray Dalio - macro strategies, "Pure Alpha"',
    },
    {
        "cik": "1336528",
        "name": "Pershing Square Capital Management",
        "focus": "Activist",
        "known_for": "Bill Ackman - concentrated activist bets",
    },
    {
        "cik": "1649339",
        "name": "Scion Asset Management",
        "focus": "Value / Concentrated Equity",
        "known_for": "Michael Burry - deep-value, contrarian bets",
    },
    {
        "cik": "1656456",
        "name": "Appaloosa LP",
        "focus": "Value / Concentrated Equity",
        "known_for": "David Tepper - distressed debt, macro-driven equity bets",
    },
    {
        "cik": "1040273",
        "name": "Third Point",
        "focus": "Activist",
        "known_for": "Dan Loeb - event-driven, activist investing",
    },
    {
        "cik": "1489933",
        "name": "Greenlight Capital (DME)",
        "focus": "Long/Short Equity",
        "known_for": "David Einhorn - long/short value investing",
    },
    {
        "cik": "1536411",
        "name": "Duquesne Family Office",
        "focus": "Global Macro",
        "known_for": "Stanley Druckenmiller - concentrated global macro",
    },
    {
        "cik": "1167483",
        "name": "Tiger Global Management",
        "focus": "Long/Short Equity",
        "known_for": "Chase Coleman - tech-focused long/short (a \"Tiger Cub\")",
    },
    {
        "cik": "921669",
        "name": "Carl Icahn",
        "focus": "Activist",
        "known_for": "Carl Icahn - activist investing",
    },
    {
        "cik": "1423053",
        "name": "Citadel",
        "focus": "Multi-Strategy",
        "known_for": "Ken Griffin - multi-strategy, top performer",
    },
    {
        "cik": "1037389",
        "name": "Renaissance Technologies",
        "focus": "Quant / Systematic",
        "known_for": "Jim Simons - quant/algorithmic trading (Medallion Fund)",
    },
    {
        "cik": "1273087",
        "name": "Millennium Management",
        "focus": "Multi-Strategy",
        "known_for": "Israel Englander - multi-strategy, pod structure",
    },
    {
        "cik": "1603466",
        "name": "Point72",
        "focus": "Multi-Strategy",
        "known_for": "Steve Cohen - long/short equity, multi-strategy",
    },
    {
        "cik": "1791786",
        "name": "Elliott Management",
        "focus": "Activist",
        "known_for": "Paul Singer - activist investing",
    },
    {
        "cik": "1179392",
        "name": "Two Sigma",
        "focus": "Quant / Systematic",
        "known_for": "John Overdeck & David Siegel - quant, data science",
    },
    {
        "cik": "1009207",
        "name": "D.E. Shaw",
        "focus": "Quant / Systematic",
        "known_for": "David E. Shaw - quant + multi-strategy",
    },
    {
        "cik": "1167557",
        "name": "AQR Capital",
        "focus": "Quant / Systematic",
        "known_for": "Cliff Asness - factor-based/systematic investing",
    },
    {
        "cik": "1637460",
        "name": "Man Group",
        "focus": "Quant / Systematic",
        "known_for": "Listed company (UK) - world's largest listed hedge fund",
    },
    {
        "cik": "1512857",
        "name": "Brevan Howard",
        "focus": "Global Macro",
        "known_for": "Alan Howard - global macro, rates",
    },
]

# OpenFIGI is used to map CUSIPs to ticker symbols. A free API key raises
# the rate limit substantially (sign up at https://www.openfigi.com/api).
# Leave blank to use the free, unauthenticated (slower) rate limit.
OPENFIGI_API_KEY = ""

# Anchored to this file's own location (not the process's working
# directory) so it resolves the same way locally, on Render, and on
# Vercel, regardless of where each one happens to run the app from.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(_BASE_DIR, "data", "filings.db"))

# Optional: if you deploy this somewhere public (e.g. Render) and want to
# keep it private, set both of these (as environment variables on the
# host, not by editing this file) to require a login. Leave both unset -
# the default - and the site stays fully open, same as running locally.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
