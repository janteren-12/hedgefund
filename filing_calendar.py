"""
SEC's 13F-HR filing deadlines: due 45 calendar days after the end of each
calendar quarter, pushed to the next business day if that lands on a
weekend. This is pure date math, not derived from EDGAR - it's the
regulatory schedule every 13F filer is required to follow, which is why
our own fetched filing dates cluster right around these dates each
quarter (e.g. "2026-02-17" instead of "2026-02-14" when the 14th falls on
a weekend).

Doesn't account for SEC holidays (a deadline could shift by a day or two
around one) - good enough to know roughly when to expect new filings,
not meant to be authoritative for compliance purposes.
"""

from datetime import date, timedelta

_QUARTER_ENDS = [(3, 31), (6, 30), (9, 30), (12, 31)]


def _next_business_day(d):
    while d.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        d += timedelta(days=1)
    return d


def deadline_for_quarter_end(quarter_end):
    return _next_business_day(quarter_end + timedelta(days=45))


def upcoming_deadlines(today=None, count=2):
    """
    The next `count` 13F filing deadlines from today onward, each as
    (deadline_date, quarter_end_date) - soonest first.
    """
    today = today or date.today()
    candidates = []
    for year in (today.year, today.year + 1):
        for month, day in _QUARTER_ENDS:
            quarter_end = date(year, month, day)
            deadline = deadline_for_quarter_end(quarter_end)
            if deadline >= today:
                candidates.append((deadline, quarter_end))
    candidates.sort()
    return candidates[:count]
