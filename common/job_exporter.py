"""
Exports and manages skipped external jobs into CSV and styled Excel spreadsheets (.xlsx).
Provides clickable links to Naukri listings, external company career sites, and Google search.
"""
import csv
import urllib.parse
from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

CSV_FILE = Path(__file__).resolve().parent.parent / "skipped_external_jobs.csv"
XLSX_FILE = Path(__file__).resolve().parent.parent / "skipped_external_jobs.xlsx"
APP_LOG = Path(__file__).resolve().parent.parent / "applications_log.csv"

HEADERS = [
    "Date Detected",
    "Platform",
    "Job Title",
    "Company",
    "Experience Required",
    "Location",
    "Listing URL",
    "External Company Apply URL",
    "Status",
]


def log_external_job(
    title: str,
    company: str,
    platform: str = "Naukri",
    exp: str = "",
    location: str = "",
    listing_url: str = "",
    external_url: str = "",
    status: str = "External Site - Manual Apply Needed",
):
    """Logs a skipped external job to CSV and refreshes the Excel spreadsheet."""
    new_file = not CSV_FILE.exists()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # If external_url is empty, build a fallback google search link
    if not external_url and company and title:
        q = urllib.parse.quote(f"{title} {company} careers apply")
        external_url = f"https://www.google.com/search?q={q}"

    with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(HEADERS)
        writer.writerow([
            now_str,
            platform,
            title,
            company,
            exp or "Not specified",
            location or "Not specified",
            listing_url,
            external_url,
            status,
        ])

    export_to_excel()


def export_to_excel(
    csv_path: Path = CSV_FILE,
    xlsx_path: Path = XLSX_FILE,
):
    """Converts the CSV to a formatted Excel file with clickable links and styling."""
    if not csv_path.exists():
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "External Apply Jobs"

    # Header styling
    header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Calibri", size=10)
    link_font = Font(name="Calibri", size=10, color="0563C1", underline="single")
    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )

    with open(csv_path, mode="r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        for row_idx, row in enumerate(reader, start=1):
            ws.append(row)
            is_header = row_idx == 1

            for col_idx, val in enumerate(row, start=1):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.border = thin_border
                cell.alignment = Alignment(vertical="center")

                if is_header:
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    cell.font = data_font
                    # Check if column is a URL (Listing URL = 7, External Apply URL = 8)
                    if col_idx in (7, 8) and val.startswith("http"):
                        cell.hyperlink = val
                        cell.font = link_font
                        if col_idx == 7:
                            cell.value = "Open Listing"
                        elif col_idx == 8:
                            cell.value = "Apply on Company Site"

    # Auto-fit column widths
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or "")
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    # Enable autofilter
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions

    ws.freeze_panes = "A2"
    wb.save(xlsx_path)
    print(f"Excel sheet successfully updated at: {xlsx_path.name}")


def build_from_existing_log():
    """
    Parses existing applications_log.csv for previously skipped 'external apply' jobs,
    deduplicates them, generates direct search links, and creates the Excel sheet.
    """
    if not APP_LOG.exists():
        return

    records = []
    seen = set()

    with open(APP_LOG, mode="r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r.get("reason") == "external apply":
                title = (r.get("title") or "").strip()
                company = (r.get("company") or "").strip()
                if not title or not company:
                    continue
                key = (title.lower(), company.lower())
                if key in seen:
                    continue
                seen.add(key)

                ts = r.get("timestamp", "")
                if " " in ts:
                    ts = ts.split(".")[0]  # trim milliseconds

                # Generate direct Naukri search URL
                naukri_slug = urllib.parse.quote(f"{title} {company}")
                listing_url = f"https://www.naukri.com/{naukri_slug.replace('%20', '-')}-jobs"

                # Generate Google Careers / Company apply search URL
                google_q = urllib.parse.quote(f"{title} {company} careers apply")
                ext_url = f"https://www.google.com/search?q={google_q}"

                records.append([
                    ts,
                    r.get("source", "naukri").capitalize(),
                    title,
                    company,
                    "Not specified",
                    "Not specified",
                    listing_url,
                    ext_url,
                    "External Site - Manual Apply Needed",
                ])

    with open(CSV_FILE, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS)
        writer.writerows(records)

    export_to_excel()
    print(f"Generated {len(records)} unique skipped external jobs in {XLSX_FILE.name} and {CSV_FILE.name}.")


if __name__ == "__main__":
    build_from_existing_log()
