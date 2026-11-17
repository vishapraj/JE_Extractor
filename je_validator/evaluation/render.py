"""Writes a gold-set case's files, in the layouts of sample_templates/ and samples/."""

import csv
import io
import random
from datetime import datetime, time
from pathlib import Path
from typing import Any, Dict, List

import docx
from docx.shared import Inches
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


def picture_of(lines: List[str], rng: random.Random) -> Image.Image:
    """Lines as a pasted screenshot or scan clipping; columns ("a   b   c") at fixed positions."""
    font = ImageFont.load_default(size=28)
    img = Image.new("L", (1240, 60 + 50 * len(lines)), 255)
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        cells = line.split(COLUMN)
        xs = [40] if len(cells) == 1 else [40, 1200] if len(cells) == 2 else [40, 640, 940]
        for j, (x, cell) in enumerate(zip(xs, cells)):
            right_aligned = len(cells) == 2 and j == 1
            draw.text((x - draw.textlength(cell, font=font) if right_aligned else x, 30 + 50 * i), cell, fill=20, font=font)
    return img.filter(ImageFilter.GaussianBlur(rng.uniform(0.2, 0.7)))


def invoice_pdf_mixed(path: Path, typed: List[str], pictured: List[str], rng: random.Random) -> None:
    """A PDF whose heading is typed (a text layer) and whose items and totals are a pasted picture."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, typed[0], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    for line in typed[1:]:
        left, _, right = line.partition(COLUMN)
        pdf.cell(130, 7, left)
        pdf.cell(0, 7, right, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.image(picture_of(pictured, rng), x=10, y=pdf.get_y() + 4, w=190)
    pdf.output(str(path))


def invoice_complex(path: Path, invoice: Dict[str, Any], scan: bool, rng: random.Random) -> List[str]:
    """An invoice laid out in blocks, as a scan (PNG) or a typed PDF: vendor top left, 'INVOICE' and a
    grid of headings over values top right, Bill to beside a contact block, an items table with column
    headings, totals bottom right, bank details at the foot. Returns its text, block by block."""
    p, cur = invoice["pii"], invoice["currency"]
    total = round(sum(a for _, a in invoice["items"]), 2)
    month = f"{invoice['date']:%B %Y}"
    place = []  # (x, y, text, size) on a 1240 x 1754 page

    def put(x, y, text, size=24):
        place.append((x, y, text, size))
    put(80, 70, invoice["vendor"], 40)
    put(80, 130, p["street"]), put(80, 160, f"{p['city']} {p['postcode']}")
    put(760, 70, "INVOICE", 40)
    for i, (k, v) in enumerate([("Invoice No", invoice["number"]), ("Date", f"{invoice['date']:%d/%m/%Y}"),
                                ("PO", invoice["po"])]):
        put(760 + i * 150, 140, k), put(760 + i * 150, 172, v)
    for j, s in enumerate(["Bill to", invoice["bill_to"]]):
        put(80, 270 + j * 32, s)
    for j, s in enumerate(["Contact", p["contact"], p["email"], p["phone"]]):
        put(700, 270 + j * 32, s)
    columns = (80, 700, 860, 1010)
    for x, s in zip(columns, ("Description", "Qty", "Unit price", "Amount")):
        put(x, 450, s)
    for r, (text, a) in enumerate(invoice["items"], 1):
        for x, s in zip(columns, (f"{text} {month}", "1", money(a), money(a))):
            put(x, 450 + r * 44, s)
    y = 450 + (len(invoice["items"]) + 1) * 44 + 60
    # A VAT summary without borders right beside the totals, on the same rows and with the same spacing:
    # geometry alone can't tell where one ends and the other begins.
    put(80, y - 40, "VAT summary")
    for x, (k, v) in zip((80, 240, 400, 560), [("Rate", "0%"), ("Net", money(total)), ("VAT", "0.00"),
                                                ("Gross", money(total))]):
        put(x, y, k), put(x, y + 40, v)
    for r, (k, v) in enumerate([("Subtotal", money(total)), ("VAT", "0.00"), (f"Total {cur}", money(total))]):
        put(760, y + r * 40, k), put(1010, y + r * 40, v)
    put(80, 1560, f"Bank: Sort code {p['sort_code']}  Account No {p['account']}")
    put(80, 1592, f"IBAN: {p['iban']}"), put(80, 1640, f"Thank you for your business - {p['signer']}")

    if scan:
        img = Image.new("L", (1240, 1754), 255)
        draw = ImageDraw.Draw(img)
        for x, y0, s, size in place:
            draw.text((x, y0), s, fill=rng.randint(0, 60), font=ImageFont.load_default(size=size))
        img = img.rotate(rng.uniform(-0.8, 0.8), resample=Image.BICUBIC, fillcolor=255)
        img.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.8))).save(path)
    else:
        k = 210 / 1240  # page pixels to mm
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(False)
        for x, y0, s, size in place:
            pdf.set_font("Helvetica", "B" if size > 30 else "", size=size / 2)
            pdf.text(x * k, (y0 + size * 0.9) * k, s)
        pdf.output(str(path))
    return [s for _, _, s, _ in place]


def approval_docx_mixed(path: Path, approval: Dict[str, Any], rng: random.Random) -> None:
    """The approval with its table pasted as a picture (e.g. a screenshot from the approval system)."""
    d = docx.Document()
    d.add_paragraph(f"Approval of {approval['je_number']}")
    d.add_paragraph("Approved.")
    d.add_paragraph(f"Prepared by: {approval['preparer']}")
    d.add_paragraph(f"Approver email: {approval['email']}")
    table = picture_of([COLUMN.join(("Approver", "Amount", "Date")),
                        COLUMN.join((approval["approver"], f"{approval['currency']} {money(approval['amount'])}",
                                     f"{approval['date']:%d/%m/%Y}"))], rng)
    buffer = io.BytesIO()
    table.save(buffer, format="PNG")
    buffer.seek(0)
    d.add_picture(buffer, width=Inches(6))
    d.save(path)


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
