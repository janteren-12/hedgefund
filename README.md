# 13F Hedge Fund Tracker

A small web app that pulls 13F-HR filings from SEC EDGAR for a list of
hedge funds you choose, stores them in a SQLite database, and shows five
views: current positions, quarter-over-quarter selling/buying activity,
cross-fund overlap, funds grouped by strategy (including a real,
market-data-based return for the rare fund that's genuinely public - see
below), and a biggest-new-bets leaderboard ranked by dollar size. The
Overlap table is also downloadable as an Excel file. Runs locally out of
the box; see [Deploying to Vercel (free)](#deploying-to-vercel-free)
below if you want it reachable from anywhere.

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

## Annualized returns ("By Strategy" page)

13F filings don't report a fund's actual performance - no NAV, no P&L,
just holdings. So instead of guessing or scraping unreliable numbers off
the internet, this only shows a return for a fund when there's a real,
verifiable, freely-available price to compute it from: a `"public_ticker"`
in `config.py` (currently just Berkshire Hathaway's `BRK-B` - it's a
public stock, so its price *is* a real return, not an estimate). For that
handful of funds, `fetch_filings.py` pulls real closing prices from Yahoo
Finance's free chart API on our two stored filing dates and computes:

- the real price return between those two dates
- that same return compounded up to an annual rate (one quarter's move
  blown up ^4 - a genuine number, but noisy, not a multi-year track
  record)

Every other fund - the vast majority - is privately held and under no
obligation to disclose returns anywhere public, so it just shows "Not
publicly disclosed" rather than a fabricated figure. If you know of
another fund in your list that trades as a real public vehicle (e.g. a
listed closed-end fund that publishes NAV, like Pershing Square
Holdings), you can add its ticker the same way Berkshire's is set up.

## Deploying to Vercel (free)

Like Render's free tier, Vercel's free (Hobby) plan has no persistent
disk - but unlike Render, Vercel also runs each request as a short-lived
serverless function (10 second time limit on Hobby), so it doesn't suit
an app that writes to its own database at request time. This app doesn't
need to, though: a scheduled **GitHub Action** refreshes the database and
commits the result back to the repo, and the deployed app only ever
*reads* it - a good fit for Vercel, and its cold starts are typically much
faster than Render's free-tier ~30-50 second wake-up.

**One-time setup:**

1. Push this project to a GitHub repository, if you haven't already:
   ```
   git init
   git add .
   git commit -m "Initial commit"
   ```
   Then create a repo on https://github.com/new (don't initialize it with
   a README) and follow the "push an existing repository" instructions it
   shows you. Note `config.py`'s `SEC_USER_AGENT` default has your email
   in it - fine for a **private** repo, but if the repo is public, either
   edit that default first or set `SEC_USER_AGENT` as an environment
   variable instead (it overrides the hardcoded value).

2. On https://vercel.com, sign in with GitHub, click **Add New > Project**,
   and import this repository. Vercel reads `vercel.json` and deploys the
   Flask app automatically - no build/start commands to fill in.

3. In the project's **Settings > Environment Variables**, optionally set
   `ADMIN_USERNAME` and `ADMIN_PASSWORD` if you want the site to require a
   login (leave both unset to keep it open to anyone with the link).

4. In your GitHub repo's **Settings > Actions > General**, under "Workflow
   permissions," make sure **"Read and write permissions"** is selected -
   the scheduled refresh workflow needs this to commit updated data back.

That's it. The `data/filings.db` already in this repo (from your local
runs) is what the site launches with; the GitHub Action in
`.github/workflows/refresh.yml` keeps it current every Monday, and you can
also trigger it manually any time from your repo's **Actions** tab (the
"Refresh 13F filings" workflow has a **Run workflow** button). Every push
(including those from the refresh workflow) triggers a new Vercel deploy
automatically.

**Free-tier tradeoffs worth knowing:**
- Refreshing still happens via GitHub Actions, not by clicking anything
  on the live site - the deployed app itself is read-only.
- The Overlap page's Excel export is deliberately scoped to just that one
  table (rather than the whole dataset) to comfortably fit inside Vercel's
  10-second Hobby function limit - it runs in ~1-2 seconds.

(If you deployed to Render first and want to stop using it, you can
delete that service from Render's dashboard - `render.yaml` can stay in
the repo unused, or you can remove it.)

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
| `market_data.py` | Real stock prices for funds with a "public_ticker" set (Yahoo Finance) |
| `fetch_filings.py` | Refresh script - run this to get new data |
| `queries.py` | Turns raw data into the five views |
| `export.py` | Builds the Overlap page's "Download as Excel" file |
| `app.py` | Flask web app (routes + page rendering) |
| `templates/` | HTML pages |
| `static/style.css`, `static/app.js` | Styling + scroll/frozen-column/collapsible behavior |
| `data/filings.db` | The database (created automatically; committed to git for deployment) |
| `vercel.json` | Vercel deployment config (serverless, no disk) |
| `render.yaml` | Render Blueprint - kept for reference, not needed if you deploy to Vercel |
| `.github/workflows/refresh.yml` | Scheduled GitHub Action that refreshes data and commits it |
