# 13F Hedge Fund Tracker

A small web app that pulls 13F-HR filings from SEC EDGAR for a list of
hedge funds you choose, stores them in a SQLite database, and shows four
views: current positions, quarter-over-quarter selling/buying activity,
cross-fund overlap, and funds grouped by strategy. Runs locally out of the
box; see [Deploying to Render (free)](#deploying-to-render-free) below if
you want it reachable from anywhere.

## 1. Install

Requires Python 3.9+.

```
pip install -r requirements.txt
```

## 2. Set your email (required by SEC)

Open `config.py` and confirm `SEC_USER_AGENT` has your name and email. SEC
requires this on every request - it's not a login, just a courtesy header.

## 3. Download filings

```
python fetch_filings.py
```

This pulls the two most recent 13F-HR filings for every fund listed in
`config.py`, plus ticker symbols for the stocks held (via the free OpenFIGI
API). It prints progress as it goes. It can take a few minutes the first
time, mostly because of the polite delays between SEC requests.

## 4. Run the app

```
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

## Refreshing later (new quarter published)

Just re-run:

```
python fetch_filings.py
```

It only downloads filings it doesn't already have, and automatically drops
the oldest quarter once a new one comes in, so the database always holds
the latest two quarters per fund.

## Adding more funds

Edit the `FUNDS` list in `config.py`:

```python
FUNDS = [
    {
        "cik": "1067983",
        "name": "Berkshire Hathaway",
        "focus": "Value / Concentrated Equity",  # optional, feeds the "By Strategy" page
        "known_for": "Warren Buffett - long-term value investing",  # optional
    },
    # add more here
]
```

To find a fund's CIK number:
1. Go to https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany
2. Search the fund's name
3. Copy the CIK number shown next to it

Then re-run `python fetch_filings.py`. Removing a fund from the list also
removes its data from the database next time you run the refresh script.

## Deploying to Render (free)

Render's free web services don't have persistent disk storage, so instead
of writing the database on the server, a scheduled **GitHub Action**
refreshes it and commits the result back to the repo - Render then
auto-deploys on every push, so the live site just ships with whatever
data is currently in the repo. No paid plan, no separate database service.

**One-time setup:**

1. Push this project to a new GitHub repository (from inside this folder):
   ```
   git init
   git add .
   git commit -m "Initial commit"
   ```
   Then create a repo on https://github.com/new (don't initialize it with
   a README) and follow the "push an existing repository" instructions it
   shows you. Note `config.py`'s `SEC_USER_AGENT` default has your email
   in it - fine for a **private** repo, but if you make the repo public,
   either edit that default first or set `SEC_USER_AGENT` as an
   environment variable instead (it overrides the hardcoded value).

2. On https://render.com, sign in with GitHub, click **New > Blueprint**,
   and pick this repository. Render will read `render.yaml` and set up a
   free web service automatically (build: `pip install -r requirements.txt`,
   start: `gunicorn app:app`).

3. In the new service's **Environment** tab on Render, optionally set
   `ADMIN_USERNAME` and `ADMIN_PASSWORD` if you want the site to require a
   login (leave both unset to keep it open to anyone with the link).

4. In your GitHub repo's **Settings > Actions > General**, under "Workflow
   permissions," make sure **"Read and write permissions"** is selected -
   the scheduled refresh workflow needs this to commit updated data back.

That's it. The `data/filings.db` already in this repo (from your local
runs) is what the site launches with; the GitHub Action in
`.github/workflows/refresh.yml` keeps it current every Monday, and you can
also trigger it manually any time from your repo's **Actions** tab (the
"Refresh 13F filings" workflow has a **Run workflow** button).

**Free-tier tradeoffs worth knowing:**
- The free web service spins down after ~15 minutes of no traffic and
  takes 30-50 seconds to wake back up on the next visit.
- Refreshing still happens via GitHub Actions, not by clicking anything
  on the live site - the deployed app itself is read-only.

## Notes and limitations

- 13F filings are quarterly and can be filed up to **45 days after quarter-end**,
  so this is never real-time data.
- 13F only covers **long U.S. equity positions** - no short positions, no cash,
  no bonds, no non-US holdings. A fund's real portfolio may look very
  different once you account for what 13F doesn't report.
- "Selling" in the Selling & Buying Activity view means a fund reported fewer
  shares this quarter than last quarter - not a real-time trade.
- Amendments (form type `13F-HR/A`) are skipped; only original `13F-HR`
  filings are used.
- If OpenFIGI can't map a CUSIP to a ticker, the app falls back to showing
  the raw CUSIP and the company name straight from the SEC filing, so
  nothing is ever blank.

## Project files

| File | Purpose |
|---|---|
| `config.py` | Your email, the tracked funds list, optional login/OpenFIGI settings |
| `db.py` | SQLite schema and data access helpers |
| `edgar.py` | Talks to SEC EDGAR (rate-limited) |
| `cusip_lookup.py` | Maps CUSIPs to tickers via OpenFIGI, with caching |
| `fetch_filings.py` | Refresh script - run this to get new data |
| `queries.py` | Turns raw data into the four views |
| `app.py` | Flask web app (routes + page rendering) |
| `templates/` | HTML pages |
| `static/style.css`, `static/app.js` | Styling + scroll/frozen-column behavior |
| `data/filings.db` | The database (created automatically; committed to git for the Render deploy) |
| `render.yaml` | Render Blueprint (free web service, no disk) |
| `.github/workflows/refresh.yml` | Scheduled GitHub Action that refreshes data and commits it |
