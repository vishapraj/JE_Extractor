"""
A synthetic gold set: JEs and supporting documents with known answers. All
names and figures are invented.

Each case is one vendor invoice booked as a JE: a debit line per invoice item
and a credit line to accounts payable, in either template layout, with the
invoice as a PDF or as a scan. Most cases then get exactly one planted error
(ERRORS). expected.json records what a correct review finds:

    je              the JE's header and lines, to score extraction
    document_gold   each document's true text and amounts, to score extraction,
                    and the personal data planted in it (pii), to score masking
    je_pii          personal data in the JE (the preparer)
    lines, checks   each line's and check's status; a list holds every status that
                    counts as right, and null means the check isn't scored
    flagged         whether the JE should be stopped (anything but PASS)

The same seed always makes the same gold set.
"""

import calendar
import json
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

from je_validator.evaluation import render

ERRORS = {
    "CLEAN": "No error: every check should pass",
    "AMOUNT": "One JE line's amount differs from the invoice",
    "VENDOR": "The JE names another vendor than the invoice",
    "REFERENCE": "The JE's invoice reference differs from the invoice number",
    "DATE": "The invoice is dated months before the JE period",
    "APPROVAL_MISSING": "There is no approval document",
    "APPROVAL_SHORT": "The approved amount is below the JE total",
    "CURRENCY": "The invoice is in another currency than the JE",
    "UNSUPPORTED_LINE": "The JE has a line no document supports",
    "UNBALANCED": "Debits don't equal credits",
}
# The order cases are made in, so any first part of the gold set has every kind of case.
CYCLE = ["CLEAN", "AMOUNT", "VENDOR", "CLEAN", "REFERENCE", "DATE", "CLEAN", "APPROVAL_MISSING",
         "APPROVAL_SHORT", "CLEAN", "CURRENCY", "UNSUPPORTED_LINE", "UNBALANCED"]

VENDORS = ["Kestrel IT Services Ltd", "Meridian Cloud Ltd", "Bluewater Facilities Ltd", "Northgate Couriers Ltd",
           "Harbour Print Co", "Pinecrest Consulting LLP", "Oakline Office Supplies Ltd", "Ashford Equipment Hire Ltd"]
ITEMS = [("IT support", "620100", "IT services"), ("Cloud hosting", "620200", "Cloud services"),
         ("Software licences", "620300", "Software"), ("Office cleaning", "640100", "Facilities"),
         ("Courier services", "650200", "Freight and postage"), ("Printing", "660100", "Printing and stationery"),
         ("Consulting fees", "670100", "Professional fees"), ("Equipment rental", "640300", "Equipment hire"),
         ("Staff training", "680100", "Training")]
ENTITIES = [("Acme UK Ltd", "1000", "GBP"), ("Acme Europe BV", "2000", "EUR"), ("Acme US Inc", "3000", "USD")]
PREPARERS = ["J. Smith", "A. Patel", "L. Chen", "R. Okafor"]
APPROVERS = ["M. Jones (Finance Manager)", "S. Rossi (Controller)", "K. Novak (Finance Director)"]
PEOPLE = ["Sarah Thompson", "David Okoro", "Priya Raman", "Tom Becker", "Elena Petrova", "James O'Neill",
          "Grace Mwangi", "Lukas Brandt"]
STREETS = ["High", "Station", "Mill", "Church", "Victoria", "Park"]
CITIES = ["London", "Leeds", "Bristol", "Manchester", "Glasgow"]
PAYABLES = ("210000", "Accounts payable")
CHECKS = ("BALANCED", "AMOUNTS", "VENDOR", "REFERENCE", "DATES_IN_PERIOD", "APPROVAL", "CURRENCY")
# The amounts are right but the line points elsewhere, so a reviewer may also call it NOT_FOUND.
AMOUNTS_RIGHT = ["SUPPORTED", "NOT_FOUND"]


def generate(out: Path, cases: int, seed: int, complex_share: float = 0.0, layouts: int = 1) -> None:
    """complex_share: the share of invoices laid out in blocks and tables. layouts: 1 lays them all out as
    render.invoice_complex; 2 alternates it with render.invoice_stacked, so that rules for reading a
    layout are measured on one they weren't written against."""
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} is not empty - delete it or choose another folder")
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    counts: Dict[str, int] = {}
    for n in range(1, cases + 1):
        error = CYCLE[(n - 1) % len(CYCLE)]
        # Its own random stream decides which documents mix typed text with a picture, so adding
        # these variants leaves every case's content as it was.
        _write(out / f"case-{n:03d}", _case(n, error, rng), rng, random.Random(seed * 1_000_003 + n), complex_share,
               "stacked" if layouts == 2 and n % 2 == 0 else "blocks")
        counts[error] = counts.get(error, 0) + 1
    (out / "manifest.json").write_text(json.dumps({"seed": seed, "cases": cases, "complex_share": complex_share,
                                                   "layouts": layouts, "errors": counts, "descriptions": ERRORS},
                                                  indent=1),
                                       encoding="utf-8")
    print(f"Made {cases} cases in {out}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))


def _case(n: int, error: str, rng: random.Random) -> Dict[str, Any]:
    entity, company, currency = rng.choice(ENTITIES)
    vendor = rng.choice(VENDORS)
    month = rng.randint(1, 12)
    posting = date(2026, month, calendar.monthrange(2026, month)[1])
    invoice_date = date(2026, month, rng.randint(1, posting.day - 3))
    picked = rng.sample(ITEMS, rng.randint(1, 3))
    items = [(text, account, account_text, f"CC{rng.randint(1, 9)}00", _amount(rng)) for text, account, account_text in picked]
    total = round(sum(i[4] for i in items), 2)
    invoice_no = f"INV-{rng.randint(1000, 9999)}"
    sap = rng.random() < 0.35

    lines = [{"account": account, "account_description": account_text, "cost_center": cc, "debit": amount,
              "credit": None, "vendor": vendor, "description": f"{text} {posting:%b %Y}", "reference": invoice_no,
              "expect": ["SUPPORTED"]} for text, account, account_text, cc, amount in items]
    lines.append({"account": PAYABLES[0], "account_description": PAYABLES[1], "cost_center": None, "debit": None,
                  "credit": total, "vendor": vendor, "description": f"Invoice {invoice_no}", "reference": invoice_no,
                  "expect": ["SUPPORTED"]})
    je_number = f"1900{n:05d}" if sap else f"JE-2026-{n:04d}"
    je = {"layout": "sap" if sap else "standard", "number": je_number, "company": company, "entity": entity,
          "posting_date": posting, "document_date": invoice_date, "currency": currency,
          "description": f"{vendor} invoice {invoice_no} - {posting:%B %Y}", "prepared_by": rng.choice(PREPARERS),
          "lines": lines, "text_amounts": rng.random() < 0.3}
    invoice = {"vendor": vendor, "number": invoice_no, "date": invoice_date, "bill_to": entity,
               "po": f"PO-{rng.randint(1000, 9999)}", "currency": currency, "items": [(i[0], i[4]) for i in items]}
    approval = {"je_number": je_number, "approver": rng.choice(APPROVERS), "currency": currency, "amount": total,
                "date": invoice_date + timedelta(days=rng.randint(1, 3))}
    bank = ({"date": invoice_date + timedelta(days=rng.randint(1, 3)), "description": f"{vendor} {invoice_no}",
             "amount": total} if rng.random() < 0.4 else None)
    checks: Dict[str, Any] = {c: ["PASS"] for c in CHECKS}
    credit = lines[-1]

    if error == "AMOUNT":
        delta = rng.choice([1.0, 10.0, 90.0, 100.0])
        line = lines[rng.randrange(len(items))]
        line["debit"] = round(line["debit"] - delta, 2)
        credit["credit"] = round(total - delta, 2)
        line["expect"], credit["expect"] = ["MISMATCH"], ["MISMATCH", "NOT_FOUND"]
        checks["AMOUNTS"] = ["FAIL"]
    elif error in ("VENDOR", "REFERENCE", "DATE", "CURRENCY"):
        for line in lines:
            line["expect"] = AMOUNTS_RIGHT
        checks["AMOUNTS"] = ["PASS", "CANNOT_VERIFY"]
        if error == "VENDOR":
            other = rng.choice([v for v in VENDORS if v != vendor])
            for line in lines:
                line["vendor"] = other
            je["description"] = je["description"].replace(vendor, other)
            checks["VENDOR"] = ["FAIL"]
        elif error == "REFERENCE":
            wrong = invoice_no[:-2] + invoice_no[-1] + invoice_no[-2]
            if wrong == invoice_no:
                wrong = invoice_no[:-1] + str((int(invoice_no[-1]) + 1) % 10)
            for line in lines:
                line["reference"] = wrong
                line["description"] = line["description"].replace(invoice_no, wrong)
            je["description"] = je["description"].replace(invoice_no, wrong)
            checks["REFERENCE"] = ["FAIL"]
        elif error == "DATE":
            m = (month - 5) % 12 + 1  # four months earlier
            invoice["date"] = je["document_date"] = date(2026 if m < month else 2025, m, rng.randint(1, 28))
            bank = None
            checks["DATES_IN_PERIOD"] = ["FAIL"]
        else:
            invoice["currency"] = rng.choice([c for _, _, c in ENTITIES if c != currency])
            bank = None
            checks["CURRENCY"] = ["FAIL"]
    elif error == "APPROVAL_MISSING":
        approval = None
        checks["APPROVAL"] = ["FAIL", "CANNOT_VERIFY"]
    elif error == "APPROVAL_SHORT":
        approval["amount"] = round(total * rng.uniform(0.5, 0.9), 0)
        checks["APPROVAL"] = ["FAIL"]
    elif error == "UNSUPPORTED_LINE":
        text, account, account_text = rng.choice([i for i in ITEMS if i not in picked])
        extra = _amount(rng)
        lines.insert(-1, {"account": account, "account_description": account_text, "cost_center": "CC100",
                          "debit": extra, "credit": None, "vendor": vendor, "description": f"{text} {posting:%b %Y}",
                          "reference": invoice_no, "expect": ["NOT_FOUND"]})
        credit["credit"] = approval["amount"] = round(total + extra, 2)
        credit["expect"] = ["MISMATCH", "NOT_FOUND"]
        bank = None
        checks["AMOUNTS"] = ["FAIL", "CANNOT_VERIFY"]
    elif error == "UNBALANCED":
        credit["credit"] = round(total - rng.choice([10.0, 100.0]), 2)
        credit["expect"] = ["MISMATCH", "NOT_FOUND"]
        checks["BALANCED"] = ["FAIL"]
        checks["AMOUNTS"] = ["FAIL", "CANNOT_VERIFY"]
        checks["APPROVAL"] = None  # which total should the approval cover? Not scored.

    invoice["pii"] = _invoice_pii(vendor, rng)
    if approval:
        approval["preparer"] = je["prepared_by"]
        approval["email"] = _email(approval["approver"].split(" (")[0], "acme")
    expected_lines = {str(i): line.pop("expect") for i, line in enumerate(lines, 1)}
    return {"case": n, "error": error, "je": je, "invoice": invoice, "approval": approval, "bank": bank,
            "scan": rng.random() < 0.3, "lines": expected_lines, "checks": checks}


def _invoice_pii(vendor: str, rng: random.Random) -> Dict[str, str]:
    """Personal data an invoice typically carries; none of it is needed by a check."""
    contact, signer = rng.sample(PEOPLE, 2)
    sort_code = f"{rng.randint(10, 99)}-{rng.randint(10, 99)}-{rng.randint(10, 99)}"
    account = f"{rng.randint(10_000_000, 99_999_999)}"
    letters = "ABDEFGHJLNPQRSTUWXYZ"
    return {"contact": contact, "email": _email(contact, vendor), "phone": f"+44 20 7946 0{rng.randint(100, 999)}",
            "sort_code": sort_code, "account": account, "iban": _iban(sort_code.replace("-", ""), account),
            "street": f"{rng.randint(1, 250)} {rng.choice(STREETS)} {rng.choice(['Street', 'Road', 'Lane'])}",
            "postcode": f"{rng.choice(letters)}{rng.choice(letters)}{rng.randint(1, 20)} "
                        f"{rng.randint(1, 9)}{rng.choice(letters)}{rng.choice(letters)}",
            "city": rng.choice(CITIES), "signer": signer}


def _email(name: str, organisation: str) -> str:
    local = ".".join(name.replace("'", "").replace(".", "").lower().split())
    return f"{local}@{organisation.split()[0].lower()}.example"


def _iban(sort_code: str, account: str) -> str:
    """A UK IBAN with valid check digits (ISO 13616), for an invented bank code."""
    bban = f"ACME{sort_code}{account}"
    digits = "".join(str(int(ch, 36)) for ch in bban + "GB00")
    s = f"GB{98 - int(digits) % 97:02d}{bban}"
    return " ".join(s[i:i + 4] for i in range(0, len(s), 4))


def _amount(rng: random.Random) -> float:
    whole = rng.randint(150, 9000)
    return float(whole) if rng.random() < 0.4 else round(whole + rng.randint(1, 99) / 100, 2)


def _invoice_lines(invoice: Dict[str, Any]) -> List[str]:
    total = round(sum(a for _, a in invoice["items"]), 2)
    p = invoice["pii"]
    return [f"{invoice['vendor']} - INVOICE",
            f"{p['street']}, {p['city']} {p['postcode']}",
            f"Invoice No: {invoice['number']}   Date: {invoice['date']:%d/%m/%Y}",
            f"Bill to: {invoice['bill_to']}",
            f"PO: {invoice['po']}",
            f"Contact: {p['contact']}   {p['email']}",
            f"Tel: {p['phone']}",
            *(f"{text} {invoice['date']:%B %Y}   {render.money(a)}" for text, a in invoice["items"]),
            f"Subtotal   {render.money(total)}",
            "VAT   0.00",
            f"Total {invoice['currency']}   {render.money(total)}",
            f"Bank: Sort code {p['sort_code']}   Account No {p['account']}",
            f"IBAN: {p['iban']}",
            f"Thank you for your business - {p['signer']}"]


def _layout_gold(invoice: Dict[str, Any], layout: str = "blocks") -> Dict[str, List[List[str]]]:
    """What reading a block layout right means (render.invoice_complex, or invoice_stacked for 'stacked'):
        pairs  a heading and the value under it, read on one line
        rows   an item's description and its amount (or a label and its value), on one line
        apart  text from blocks side by side, never on one line
        roles  text and the role of the zone it should be in (documents/roles.py)"""
    p = invoice["pii"]
    total = render.money(round(sum(a for _, a in invoice["items"]), 2))
    items = [[f"{text} {invoice['date']:%B %Y}", render.money(a)] for text, a in invoice["items"]]
    if layout == "stacked":
        return {"pairs": [["Net", total], ["Total", total]],
                "rows": items + [["Invoice number", invoice["number"]], ["Invoice date", f"{invoice['date']:%d/%m/%Y}"],
                                 ["Order ref", invoice["po"]], ["Net total", total], ["Amount due", total]],
                "apart": [["Invoice number", p["street"]], [invoice["number"], p["street"]], ["Order ref", p["email"]],
                          ["Bill to", "Ship to"], [invoice["bill_to"], p["street"]]],
                "roles": [["identity", "Invoice number"], ["party", "Bill to"], ["party", "Ship to"],
                          ["totals", "Net total"], ["totals", "Amount due"], ["vat_summary", "VAT rate"],
                          ["payment", "IBAN"]] + [["items", i[0]] for i in items]}
    return {"pairs": [["Invoice No", invoice["number"]], ["Date", f"{invoice['date']:%d/%m/%Y}"], ["PO", invoice["po"]],
                      ["Net", total], ["Gross", total]],
            "rows": items,
            "apart": [[p["street"], "Invoice No"], [p["city"], invoice["number"]], ["Bill to", "Contact"],
                      [invoice["bill_to"], p["contact"]], ["Rate", "Subtotal"]],
            "roles": [["identity", "Invoice No"], ["party", "Bill to"], ["party", "Contact"], ["vat_summary", "Rate"],
                      ["vat_summary", "Gross"], ["totals", "Subtotal"], ["totals", f"Total {invoice['currency']}"],
                      ["payment", "IBAN"]] + [["items", i[0]] for i in items]}


def _invoice_pii_values(p: Dict[str, str]) -> List[str]:
    return [p[k] for k in ("contact", "email", "phone", "sort_code", "account", "iban", "street", "postcode",
                           "signer")]


HEADING_LINES = 7  # an invoice's lines before its items: title, address, number and date, bill-to, PO, contact, phone


def _write(folder: Path, case: Dict[str, Any], rng: random.Random, side: random.Random,
           complex_share: float = 0.0, layout: str = "blocks") -> None:
    folder.mkdir()
    je, invoice = case["je"], case["invoice"]
    (render.je_sap if je["layout"] == "sap" else render.je_standard)(folder / "je.xlsx", je)

    text = _invoice_lines(invoice)
    invoice_file = "invoice_scan.png" if case["scan"] else "invoice.pdf"
    mixed = []  # documents with typed text and a picture
    layout_gold = None
    if complex_share and side.random() < complex_share:  # drawn only for complex gold sets
        draw = render.invoice_stacked if layout == "stacked" else render.invoice_complex
        text = draw(folder / invoice_file, invoice, case["scan"], side)
        layout_gold = _layout_gold(invoice, layout)
    elif case["scan"]:
        render.invoice_scan(folder / invoice_file, text, rng)
    elif side.random() < 0.4:
        render.invoice_pdf_mixed(folder / invoice_file, text[:HEADING_LINES], text[HEADING_LINES:], side)
        mixed.append(invoice_file)
    else:
        render.invoice_pdf(folder / invoice_file, text)
    gold = {invoice_file: {"text": "\n".join(text),
                           "amounts": sorted({a for _, a in invoice["items"]} | {round(sum(a for _, a in invoice["items"]), 2)}),
                           "pii": _invoice_pii_values(invoice["pii"])}}
    if layout_gold:
        gold[invoice_file]["layout"] = layout_gold
    if case["approval"]:
        a = case["approval"]
        if side.random() < 0.4:
            render.approval_docx_mixed(folder / "approval.docx", a, side)
            mixed.append("approval.docx")
        else:
            render.approval_docx(folder / "approval.docx", a)
        gold["approval.docx"] = {"amounts": [a["amount"]],
                                 "pii": [a["approver"].split(" (")[0], a["preparer"], a["email"]]}
    if case["bank"]:
        render.bank_csv(folder / "bank.csv", case["bank"])
        gold["bank.csv"] = {"amounts": [case["bank"]["amount"]], "pii": []}

    if je["layout"] == "sap":
        header = {"je_number": je["number"], "company_code": je["company"], "je_type": "KR",
                  "period": str(je["posting_date"].month), "posting_date": f"{je['posting_date']:%d.%m.%Y}",
                  "document_date": f"{je['document_date']:%d.%m.%Y}", "currency": je["currency"],
                  "description": je["description"], "prepared_by": je["prepared_by"]}
    else:
        header = {"je_number": je["number"], "company_code": je["company"], "entity_name": je["entity"],
                  "period": f"{je['posting_date']:%Y-%m}", "posting_date": je["posting_date"].isoformat(),
                  "currency": je["currency"], "description": je["description"], "prepared_by": je["prepared_by"]}
    expected = {
        "case": folder.name, "error": case["error"], "about": ERRORS[case["error"]],
        "je_file": "je.xlsx", "documents": list(gold), "mixed": mixed,
        "template": "sap_export" if je["layout"] == "sap" else "standard",
        "je": {"header": header, "lines": [{k: l[k] for k in ("account", "debit", "credit", "vendor", "reference",
                                                               "description")} for l in je["lines"]]},
        "document_gold": gold, "je_pii": [je["prepared_by"]],
        "lines": case["lines"], "checks": case["checks"], "flagged": case["error"] != "CLEAN",
    }
    (folder / "expected.json").write_text(json.dumps(expected, indent=1), encoding="utf-8")
