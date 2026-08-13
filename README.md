# 13F Hedge Fund Tracker

A small web app that pulls 13F-HR filings from SEC EDGAR for a list of
hedge funds you choose, stores them in a SQLite database, and shows nine
views: current positions, quarter-over-quarter selling/buying activity,
cross-fund overlap, a momentum matrix (Overlap's grid, but showing each
fund's portfolio-weight change instead of the weight itself), multi-quarter
position history, 5%+ ownership stakes (Schedule 13D/13G, separate from
13F), insider buying/selling (Form 3/4/5, filed by a company's own
officers/directors/10%+ owners - a different disclosure regime again) for
the stocks your funds hold most widely, funds grouped by strategy, and a
biggest-new-bets leaderboard ranked by dollar size. There's also a
standalone page for Trivest Advisors Ltd's portfolio, kept deliberately
separate from the 20 tracked funds so it can't skew any of the cross-fund
views. The Overlap table is also downloadable as an Excel file. A Privacy
& Disclaimer page (linked in the footer of every page) explains what data
this collects (nothing) and where the numbers come from. Runs locally out
of the box; see
[Deploying to Vercel (free)](#deploying-to-vercel-free) below if you want
it reachable from anywhere.

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

This pulls the `QUARTERS_TO_KEEP` most recent 13F-HR filings (6 by
default - a year and a half) for every fund listed in `config.py`, plus
ticker symbols for the stocks held (via the free OpenFIGI API). It prints
progress as it goes. It can take a few minutes the first time, mostly
because of the polite delays between SEC requests.

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
the latest `QUARTERS_TO_KEEP` quarters per fund (see `config.py` to change
that number - the Selling & Buying page always compares only the newest
two regardless of how many are kept, but Position History uses all of
them).

Every page shows the next 13F filing deadline (45 calendar days after
each quarter-end, pushed to the next business day if that lands on a
weekend - see `filing_calendar.py`) next to "Last updated," so you know
roughly when to expect fresh filings instead of just guessing.

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
- The Insider Activity page (Form 3/4/5) is scoped to the
  `INSIDER_TRACKING_TOP_N` stocks (25 by default) held by the most tracked
  funds - reusing Overlap's own "how widely held" ranking - and only keeps
  the last `INSIDER_FILINGS_WINDOW_DAYS` (90 by default) of activity, both
  configurable in `config.py`. Only trades in actual common stock are
  shown (not options/RSUs), and every transaction is labeled with its real
  SEC transaction code - most Form 4s are routine compensation or tax
  events (grants, tax withholding, option exercises), not a genuine market
  decision, so "insider bought/sold" isn't always what it sounds like; use
  the page's filter to see only real open-market purchases and sales.
- The Ownership Stakes page (Schedule 13D/13G) only covers filings from
  December 18, 2024 onward - the date SEC started requiring these to be
  filed in a structured, machine-readable format. Older filings are
  free-text documents that aren't reliably parseable, so they're skipped
  rather than guessed at. A joint filing can list several related entities
  (a management company, its funds, a general partner); Schedule 13D
  identifies each by CIK so matching to a tracked fund is exact, but
  Schedule 13G's format doesn't include a CIK per entity, so that page
  falls back to matching on the entity's name - reliable for how these
  funds are usually named, but not guaranteed.
- Trivest Advisors Ltd (`TRIVEST_ADVISORS_CIK` in `config.py`) has its own
  page and its own storage (`trivest_filings`/`trivest_holdings` tables) -
  it's intentionally not part of `FUNDS`, so it never appears in Overlap,
  Momentum, By Strategy, Biggest New Bets, or Insider Activity's "most
  widely held" ranking.

## Project files

| File | Purpose |
|---|---|
| `config.py` | Your email, the tracked funds list, optional login/OpenFIGI settings |
| `db.py` | SQLite schema and data access helpers |
| `edgar.py` | Talks to SEC EDGAR (rate-limited) |
| `cusip_lookup.py` | Maps CUSIPs to tickers via OpenFIGI, with caching |
| `filing_calendar.py` | Computes the next 13F filing deadline (pure date math) |
| `fetch_filings.py` | Refresh script - run this to get new data |
| `queries.py` | Turns raw data into the nine views |
| `export.py` | Builds the Overlap page's "Download as Excel" file |
| `app.py` | Flask web app (routes + page rendering) |
| `templates/` | HTML pages |
| `static/style.css`, `static/app.js` | Styling + scroll/frozen-column/collapsible behavior |
| `data/filings.db` | The database (created automatically; committed to git for deployment) |
| `vercel.json` | Vercel deployment config (serverless, no disk) |
| `render.yaml` | Render Blueprint - kept for reference, not needed if you deploy to Vercel |
| `.github/workflows/refresh.yml` | Scheduled GitHub Action that refreshes data and commits it |
