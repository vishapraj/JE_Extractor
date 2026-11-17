"""
What each part of a page is for. The layout model says what a zone looks like
(text, table, title); its role says what the zone's data is:

    identity     the document's number, date and PO
    party        who it is to or from: Bill to, Ship to, a contact
    items        the table of items, each with its amount
    vat_summary  the VAT rates with their net, VAT and gross figures
    totals       Subtotal, VAT, Total: the figures the items add up to
    payment      the bank details
    approval     who approved it

Read from the words, not the positions, so it holds on layouts not seen
before. layout.py uses it to split a zone holding two blocks side by side and
to name each zone; validation then knows a total from an item.
"""

import re
from typing import List, Optional

from je_validator.validation.amounts import CURRENCY_CODES, has_amount

ROLES = ("identity", "party", "items", "vat_summary", "totals", "payment", "approval")

# A totals row's label: 'Subtotal', 'VAT', 'VAT 20%', 'Total EUR', 'Amount due', ...
_TOTAL_LABEL = re.compile(r"(?:sub-?\s?total|net total|grand total|total(?: (?:due|amount|[a-z]{3}))?|amount due|"
                          r"balance due|vat|tax|vat \d+(?:[.,]\d+)?\s?%)", re.IGNORECASE)
_NOT_LABEL = re.compile(r"[\d,.:£$€¥]+(?:\s?%)?|\b(?:" + "|".join(CURRENCY_CODES) + r")\b")  # amounts, a currency, a colon

_VAT_SUMMARY = re.compile(r"\b(?:vat|tax) (?:summary|analysis|breakdown)\b", re.IGNORECASE)
_VAT_HEADING = {"net", "vat", "tax", "gross"}
_ITEM_HEADING = {"description", "item", "items", "qty", "quantity", "unit price", "price", "amount", "line total",
                 "net amount", "hours"}
_PAYMENT = re.compile(r"\b(?:iban|sort code|swift|bic|account (?:no|number)|bank details|bank)\b", re.IGNORECASE)
_APPROVAL = re.compile(r"\b(?:approved by|authori[sz]ed by|approver|signature)\b", re.IGNORECASE)
_IDENTITY = re.compile(r"\b(?:invoice (?:no|number|date|#)|po|purchase order|order (?:no|number)|due date)\b",
                       re.IGNORECASE)
_PARTY = re.compile(r"\b(?:bill(?:ed)? to|ship to|sold to|invoice to|deliver to|customer|contact)\b|@", re.IGNORECASE)


def total_label(text: str) -> bool:
    """Is the text a totals row's label (amounts and a currency around it left out)?"""
    label = " ".join(_NOT_LABEL.sub(" ", text).split())
    return bool(label) and bool(_TOTAL_LABEL.fullmatch(label))


_SUM = re.compile(r"\b(?:sub-?\s?total|total|amount due|balance due)\b", re.IGNORECASE)


def sum_label(text: str) -> bool:
    """A totals label that is a sum (Subtotal, Total, Amount due), not a VAT or tax line: a column of VAT
    cells in an items table is not the totals."""
    return total_label(text) and bool(_SUM.search(text))


def role_of(rows: List[str]) -> Optional[str]:
    """The role of a block from its rows as arranged ('Description | Qty | Amount', 'Subtotal | 1,692.00'),
    or None if its words don't say."""
    text = "\n".join(rows)
    # Items first: an items table may have VAT columns, and every figure of a VAT summary counts as a total
    if any(len(h & _ITEM_HEADING) >= 2 and h & {"description", "item", "items"} for h in map(_headings, rows)):
        return "items"
    if _VAT_SUMMARY.search(text) or any(any("rate" in x for x in h) and h & _VAT_HEADING for h in map(_headings, rows)):
        return "vat_summary"
    totals = [r for r in rows if total_label(r.split(" | ")[0]) and has_amount(r)]
    if totals and len(totals) * 2 >= len(rows):
        return "totals"
    for role, words in (("payment", _PAYMENT), ("approval", _APPROVAL), ("identity", _IDENTITY), ("party", _PARTY)):
        if words.search(text):
            return role
    return None


def _headings(row: str) -> set:
    """A row's headings: its cells, or the heading written before each value ('Qty: 1')."""
    return {c.split(": ", 1)[0].strip().casefold() for c in row.split(" | ")}
