"""
Keeps only the values the checks need.

JE: the header and line fields listed below; the rest (preparer, approver,
cost centre, tax code, ...) are dropped.

Documents: a line is kept if it holds something a check uses - an amount, a
date, a reference (INV-7781, PO-5512, JE-2026-0042), a currency, the vendor or
the JE number, or a keyword (invoice, total, approved, ...) - or if it starts a
page, sheet or table. Everything else (addresses, bank details, contacts,
terms, sign-offs) is dropped.
"""

import re
from typing import Any, Dict, Iterable, List

from je_validator.markers import PART_HEADER, READER_HEADER
from je_validator.validation.amounts import CURRENCY_CODES, has_amount

HEADER_FIELDS = ("je_number", "entity_name", "period", "posting_date", "document_date", "currency", "reference",
                 "description")
LINE_FIELDS = ("line", "account", "account_description", "description", "vendor", "reference", "document_date",
               "currency", "debit", "credit")

_DATE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|"
                   r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? \d{4}\b", re.I)
_REFERENCE = re.compile(r"\b[A-Z]{2,5}[-/ ]?\d[\d-]{2,}\b")
_CURRENCY = re.compile(r"\b(?:" + "|".join(CURRENCY_CODES) + r")|[£€$¥₹]")
_KEYWORDS = re.compile(r"invoice|total|subtotal|\bnet\b|\bvat\b|\btax\b|amount|approv|authori[sz]|\bpaid\b|payment|"
                       r"period|\bdue\b|credit|debit|balance|currency|purchase order", re.I)


def minimise_je(je: Dict[str, Any]) -> Dict[str, Any]:
    return {"file": je["file"], "template": je["template"], "sheet": je["sheet"],
            "header": {k: v for k, v in je["header"].items() if k in HEADER_FIELDS},
            "totals": je["totals"],
            "lines": [{k: v for k, v in line.items() if k in LINE_FIELDS} for line in je["lines"]]}


def minimise_text(text: str, names: Iterable[str]) -> str:
    """names: vendors and the JE number, kept wherever they appear."""
    wanted = [n.casefold() for n in names if n]
    kept: List[str] = []
    block_start = True  # the first line of a document, page, sheet, table or picture: a title or column headers
    zone = None  # a zone's header line, kept only if a line of the zone is kept
    headings: List[str] = []  # the zone's heading row, which the layout reader writes before each value
    for line in text.splitlines():
        if not line.strip():
            continue
        if PART_HEADER.match(line):
            kept.append(line)
            block_start, zone, headings = True, None, []
            continue
        if READER_HEADER.match(line):
            # A zone: its lines are kept only for what they hold. A page's first zone may be a contact
            # block, so 'first line of a part' doesn't apply to zoned pages.
            zone, block_start, headings = line, False, []
            continue
        cells = [c.strip() for c in line.split(" | ")]
        if len(cells) >= 2 and not any(has_amount(c) or ": " in c for c in cells):
            headings = cells
        # A row is judged by its values, not by the headings written before them: "Invoice No: <a name>"
        # must not be kept for the word "Invoice".
        content = " | ".join(_value(c, headings) for c in cells)
        if block_start or has_amount(content) or _DATE.search(content) or _REFERENCE.search(content) \
                or _CURRENCY.search(content) or _KEYWORDS.search(content) or any(n in content.casefold() for n in wanted):
            if zone:
                kept.append(zone)
                zone = None
            kept.append(line)
        block_start = False
    return "\n".join(kept)


def _value(cell: str, headings: List[str]) -> str:
    """The cell without a 'Heading: ' the layout reader put before it."""
    for h in headings:
        if h and cell.startswith(h + ": "):
            return cell[len(h) + 2:]
    return cell
