"""
The web app. Three pages, each backed by one function in queries.py.

Run with:  python app.py
Then open: http://127.0.0.1:5000
"""

from datetime import date

from flask import Flask, render_template, request, Response, send_file

import db
import export
import filing_calendar
import queries
from config import (
    ADMIN_USERNAME,
    ADMIN_PASSWORD,
    INSIDER_FILINGS_WINDOW_DAYS,
    INSIDER_TRACKING_TOP_N,
    OWNERSHIP_FILINGS_SINCE,
    QUARTERS_TO_KEEP,
)

app = Flask(__name__)


@app.before_request
def require_login_if_configured():
    """
    If deployed somewhere public, set ADMIN_USERNAME/ADMIN_PASSWORD (see
    config.py) to put the whole site behind a login prompt. Unset (the
    default), the site stays fully open - same as running locally.
    """
    if not ADMIN_USERNAME:
        return None

    auth = request.authorization
    if not auth or auth.username != ADMIN_USERNAME or auth.password != ADMIN_PASSWORD:
        return Response(
            "Login required", 401, {"WWW-Authenticate": 'Basic realm="13F Tracker"'}
        )
    return None


@app.template_filter("usd")
def format_usd(value):
    if value is None:
        return "—"
    return f"${value:,.0f}"


@app.template_filter("usd2")
def format_usd2(value):
    """Like |usd but keeps cents - for per-share prices, where whole-dollar rounding loses meaning."""
    if value is None:
        return "—"
    return f"${value:,.2f}"


@app.template_filter("pct")
def format_pct(value):
    if value is None:
        return "—"
    return f"{value:.2f}%"


@app.template_filter("pct_change")
def format_pct_change(value):
    if value is None:
        return "—"
    return f"{value:+.1f}%"


@app.template_filter("shares")
def format_shares(value):
    if value is None:
        return "—"
    return f"{int(value):,}"


@app.template_filter("usd_compact")
def format_usd_compact(value):
    for threshold, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if value >= threshold:
            return f"${value / threshold:,.1f}{suffix}"
    return f"${value:,.0f}"


@app.context_processor
def inject_last_updated():
    conn = db.get_connection()
    last_updated = queries.get_last_updated(conn)
    conn.close()
    next_deadline, next_deadline_quarter_end = filing_calendar.upcoming_deadlines(count=1)[0]
    return {
        "last_updated": last_updated,
        "next_deadline": next_deadline,
        "next_deadline_quarter_end": next_deadline_quarter_end,
    }


@app.route("/")
def positions():
    conn = db.get_connection()
    funds = queries.get_positions_by_fund(conn)
    conn.close()
    return render_template("positions.html", funds=funds)


@app.route("/selling")
def selling():
    conn = db.get_connection()
    funds = queries.get_selling_buying_by_fund(conn)
    conn.close()
    return render_template("selling.html", funds=funds)


@app.route("/overlap")
def overlap():
    conn = db.get_connection()
    fund_columns, rows = queries.get_overlap(conn)
    conn.close()
    return render_template("overlap.html", fund_columns=fund_columns, rows=rows)


@app.route("/strategy")
def strategy():
    conn = db.get_connection()
    groups = queries.get_funds_by_focus(conn)
    conn.close()
    return render_template("strategy.html", groups=groups)


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/ownership")
def ownership():
    conn = db.get_connection()
    funds = queries.get_ownership_filings_by_fund(conn)
    conn.close()
    return render_template("ownership.html", funds=funds, since=OWNERSHIP_FILINGS_SINCE)


@app.route("/insiders")
def insiders():
    conn = db.get_connection()
    companies = queries.get_insider_activity(conn)
    conn.close()
    return render_template(
        "insiders.html",
        companies=companies,
        top_n=INSIDER_TRACKING_TOP_N,
        window_days=INSIDER_FILINGS_WINDOW_DAYS,
    )


@app.route("/momentum")
def momentum():
    conn = db.get_connection()
    fund_columns, rows = queries.get_momentum_matrix(conn)
    conn.close()
    return render_template("momentum.html", fund_columns=fund_columns, rows=rows)


@app.route("/history")
def history():
    conn = db.get_connection()
    funds = queries.get_position_history_by_fund(conn)
    conn.close()
    return render_template("history.html", funds=funds, quarters_to_keep=QUARTERS_TO_KEEP)


@app.route("/new-bets")
def new_bets():
    conn = db.get_connection()
    leaderboard = queries.get_new_bets_leaderboard(conn)
    conn.close()
    return render_template("new_bets.html", leaderboard=leaderboard)


@app.route("/overlap.xlsx")
def export_overlap_xlsx():
    conn = db.get_connection()
    buffer = export.build_overlap_workbook(conn)
    conn.close()
    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"13f-overlap-{date.today().isoformat()}.xlsx",
    )


if __name__ == "__main__":
    app.run(debug=True)
