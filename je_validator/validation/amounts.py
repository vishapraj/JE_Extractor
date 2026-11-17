"""Finds and tags every amount in the documents' text, so a model can only point at amounts that are there."""

import re
from typing import Any, Dict, List, Optional, Tuple

from je_validator.markers import READER_HEADER

_NUMBER = re.compile(r"\d[\d,.]*")
_EUROPEAN = re.compile(r"\d{1,3}(\.\d{3})+(,\d+)?")  # 1.234,50
CURRENCY_CODES = ("GBP", "EUR", "USD", "CHF", "JPY", "INR", "AUD", "CAD", "SGD", "HKD", "CNY", "SEK", "NOK", "DKK",
                  "PLN", "ZAR", "AED")
_CURRENCY_BEFORE = re.compile(r"\b(?:" + "|".join(CURRENCY_CODES) + r")$")  # "GBP7,917.00", as OCR often reads it


def tag_amounts(documents: List[Dict[str, Any]]) -> Tuple[Dict[str, str], Dict[int, Dict[str, Any]]]:
    """Each document's text with every amount tagged [#n], and n -> {value, document, text of its line}.
    The model refers to amounts by tag, so it can't put a figure in a document that isn't there."""
    tagged, amounts = {}, {}
    for d in documents:
        out = []
        # where on the document's pages, for the viewer, and the role of the zone the amount is in
        where = {"page": None, "zone": None, "image": None, "role": None}
        for row, text_line in enumerate(d["text"].splitlines()):
            def tag(m, text_line=text_line, row=row):
                value = _amount_value(text_line, m)
                if value is None or _quantity(text_line, m):
                    return m.group(0)
                n = len(amounts) + 1
                # row and at - the line of the text, and where the amount starts in "text" (whose spaces
                # are collapsed) - tell two picks from one row apart, and what is written before each
                before = text_line[:m.start()]
                at = len(" ".join(before.split())) + (1 if before.strip() and before[-1].isspace() else 0)
                amounts[n] = {"value": value, "document": d["file"], "text": " ".join(text_line.split()),
                              "row": row, "at": at, **where}
                return f"{m.group(0)} [#{n}]"
            # "[Page 1 - text layer]" and similar headers come from the readers, not the document
            if READER_HEADER.match(text_line):
                _locate(text_line, where)
                out.append(text_line)
            else:
                out.append(_NUMBER.sub(tag, text_line))
        tagged[d["file"]] = "\n".join(out)
    return tagged, amounts


_MONEY_AFTER = re.compile(r"\d[.,]\d{2}(?!\d)")


def _quantity(text_line: str, m: "re.Match") -> bool:
    """A whole number alone in its table cell, or after the cell's heading ('Qty: 1'), with money amounts
    after it in the row: the row's quantity, not an amount ('Printing | 1 | 2,296.00 | 2,296.00'). It is
    not tagged, so a model can't take it for the item's amount."""
    if "|" not in text_line or not m.group(0).isdigit():
        return False
    start, end = text_line.rfind("|", 0, m.start()) + 1, text_line.find("|", m.end())
    cell = text_line[start:end if end >= 0 else len(text_line)].strip()
    return cell.split(": ", 1)[-1] == m.group(0) and bool(_MONEY_AFTER.search(text_line, m.end()))


def _locate(header: str, where: Dict[str, Any]) -> None:
    """Follows the readers' headers: [Page 2 - ...] starts a page, [Zone 3 - ...] a zone on it,
    [Image 1 - ...] a picture read by OCR on it. A picture's text has zones of its own (it is laid out
    like a page); they are parts of the picture, not zones of the page, so the picture is the place."""
    m = re.match(r"\[(Page|Zone|Image) (\d+) - ([^\]]*)", header)
    if not m:
        where.update(page=None, zone=None, image=None, role=None)  # [Sheet: ...] or [Table n]: not a page
    elif m.group(1) == "Page":
        where.update(page=int(m.group(2)), zone=None, image=None, role=None)
    elif m.group(1) == "Image":
        where.update(zone=None, image=int(m.group(2)), role=None)
    else:
        where.update(role=m.group(3))  # the zone's name: its role (documents/roles.py), or the layout's label
        if where["image"] is None:
            where.update(zone=int(m.group(2)))


def has_amount(text_line: str) -> bool:
    return bool(amount_values(text_line))


def amount_values(text_line: str) -> List[float]:
    """The amounts in a line, as the tagger reads them (dates, years and codes left out)."""
    return [v for m in _NUMBER.finditer(text_line) if (v := _amount_value(text_line, m)) is not None]


def _amount_value(text_line: str, m: "re.Match") -> Optional[float]:
    """The number as an amount, or None when it is part of a date (28/10/2026, 2026-10-28) or a code (INV-7781)."""
    before = text_line[max(0, m.start() - 2):m.start()]
    after = text_line[m.end():m.end() + 2]
    if before.endswith("/") or after.startswith("/") or (after[:1] == "-" and after[1:2].isdigit()):
        return None
    glued_to_currency = bool(_CURRENCY_BEFORE.search(text_line[max(0, m.start() - 4):m.start()]))
    if (before[-1:].isalpha() and not glued_to_currency) or (before[-1:] == "-" and (before[:1].isalnum())) \
            or after[:1].isalpha():
        return None
    token = m.group(0).rstrip(".,")
    if token.isdigit() and 1900 <= int(token) <= 2100:
        return None  # a year, as in "October 2026"
    token = token.replace(".", "").replace(",", ".") if _EUROPEAN.fullmatch(token) else token.replace(",", "")
    try:
        return abs(float(token))
    except ValueError:
        return None
