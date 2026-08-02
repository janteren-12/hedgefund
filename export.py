"""
Builds a downloadable Excel workbook of the Overlap table - the stock x
fund matrix, exactly as shown on the Overlap page - reusing
queries.get_overlap() so there's only one place that computes it.
"""

import io

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

import queries

BOLD = Font(bold=True)


def build_overlap_workbook(conn):
    fund_columns, rows = queries.get_overlap(conn)

    wb = Workbook()
    ws = wb.active
    ws.title = "Overlap"

    headers = ["Ticker", "Company", "# Funds", "Weighted Avg %"] + [
        f["name"] for f in fund_columns
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = BOLD

    for r in rows:
        ws.append(
            (
                r["ticker"],
                r["company_name"],
                r["fund_count"],
                round(r["weighted_avg_pct"], 2),
                *(
                    round(r["pct_by_fund"][f["name"]], 2)
                    if f["name"] in r["pct_by_fund"]
                    else None
                    for f in fund_columns
                ),
            )
        )

    for i, h in enumerate(headers):
        ws.column_dimensions[get_column_letter(i + 1)].width = min(max(len(str(h)) + 2, 12), 30)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
