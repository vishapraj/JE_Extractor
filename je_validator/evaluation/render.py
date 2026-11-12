"""Writes a gold-set case's files, in the layouts of sample_templates/ and samples/."""

import csv
import random
from datetime import datetime, time
from pathlib import Path
from typing import Any, Dict, List

import docx
from fpdf import FPDF
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFilter, ImageFont


def money(x: float) -> str:
    return f"{x:,.2f}"


def je_standard(path: Path, je: Dict[str, Any]) -> None:
    """Label block at the top, then Debit/Credit columns (the 'standard' template)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "JE"
    for label, value in (("JE Number", je["number"]), ("Company Code", je["company"]), ("Entity", je["entity"]),
                         ("Period", f"{je['posting_date']:%Y-%m}"),
                         ("Posting Date", datetime.combine(je["posting_date"], time())),
                         ("Currency", je["currency"]), ("JE Description", je["description"]),
                         ("Prepared By", je["prepared_by"])):
        ws.append([label, value])
    ws.append([])
    ws.append(["Line", "GL Account", "Account Description", "Cost Center", "Debit", "Credit", "Vendor",
               "Line Description", "Reference"])
    amount = (lambda x: None if x is None else money(x)) if je["text_amounts"] else (lambda x: x)
    for i, l in enumerate(je["lines"], 1):
        ws.append([i, l["account"], l["account_description"], l["cost_center"], amount(l["debit"]),
                   amount(l["credit"]), l["vendor"], l["description"], l["reference"]])
    ws.append([None, None, None, "Total", sum(l["debit"] or 0 for l in je["lines"]),
               sum(l["credit"] or 0 for l in je["lines"])])
    wb.save(path)


def je_sap(path: Path, je: Dict[str, Any]) -> None:
    """One row per line, signed amount with a D/C indicator, header repeated (the 'sap_export' template)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet"
    ws.append(["Document Number", "Company Code", "Document Type", "Period", "Posting Date", "Document Date",
               "Currency", "Header Text", "Entered By", "Item", "G/L Account", "G/L Account Text", "Cost Center",
               "D/C", "Amount in Doc. Curr.", "Vendor", "Text", "Reference"])
    for i, l in enumerate(je["lines"], 1):
        dc, amount = ("S", money(l["debit"])) if l["debit"] is not None else ("H", money(l["credit"]) + "-")
        ws.append([je["number"], je["company"], "KR", str(je["posting_date"].month), f"{je['posting_date']:%d.%m.%Y}",
                   f"{je['document_date']:%d.%m.%Y}", je["currency"], je["description"], je["prepared_by"], str(i),
                   l["account"], l["account_description"], l["cost_center"], dc, amount, l["vendor"],
                   l["description"], l["reference"]])
    wb.save(path)


# Invoice lines are "left   right": the right part (an amount, or the date) is printed as a
# right-aligned column, as on a real invoice.
COLUMN = "   "


def invoice_pdf(path: Path, lines: List[str]) -> None:
    """A PDF with a text layer, read by pdfplumber."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, lines[0], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    for line in lines[1:]:
        left, _, right = line.partition(COLUMN)
        pdf.cell(130, 7, left)
        pdf.cell(0, 7, right, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))


def invoice_scan(path: Path, lines: List[str], rng: random.Random) -> None:
    """The invoice as a slightly skewed, blurred page image, read by OCR."""
    img = Image.new("L", (1240, 1754), 255)  # A4 at 150 dpi
    draw = ImageDraw.Draw(img)
    title, body = ImageFont.load_default(size=40), ImageFont.load_default(size=28)
    y = 140
    for i, line in enumerate(lines):
        font, ink = title if i == 0 else body, rng.randint(0, 70)
        left, _, right = line.partition(COLUMN)
        draw.text((110, y), left, fill=ink, font=font)
        if right:
            draw.text((1130 - draw.textlength(right, font=font), y), right, fill=ink, font=font)
        y += 80 if i == 0 else 50
    img = img.rotate(rng.uniform(-1.2, 1.2), resample=Image.BICUBIC, fillcolor=255)
    img.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.0))).save(path)


def approval_docx(path: Path, approval: Dict[str, Any]) -> None:
    d = docx.Document()
    d.add_paragraph(f"Approval of {approval['je_number']}")
    d.add_paragraph("Approved.")
    if "preparer" in approval:
        d.add_paragraph(f"Prepared by: {approval['preparer']}")
        d.add_paragraph(f"Approver email: {approval['email']}")
    table = d.add_table(rows=2, cols=3)
    for cell, value in zip(table.rows[0].cells, ("Approver", "Amount", "Date")):
        cell.text = value
    for cell, value in zip(table.rows[1].cells, (approval["approver"],
                                                 f"{approval['currency']} {money(approval['amount'])}",
                                                 f"{approval['date']:%d/%m/%Y}")):
        cell.text = value
    d.save(path)


def bank_csv(path: Path, bank: Dict[str, Any]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Description", "Amount"])
        w.writerow([f"{bank['date']:%d/%m/%Y}", bank["description"], f"-{bank['amount']:.2f}"])
