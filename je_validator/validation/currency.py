"""
Checks in code that the documents are in the JE's currency: a document that
states another currency (EUR on the invoice of a GBP JE) fails. Currencies
are found by ISO code (GBP, EUR, ...) and by the symbols that name one
currency (£, €, ₹); $ and ¥ are shared by several currencies, so they're not
used.
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from je_validator.validation.amounts import CURRENCY_CODES

_CODE = re.compile(r"\b(" + "|".join(CURRENCY_CODES) + r")(?![A-Za-z])")  # also "GBP7,917.00"
_SYMBOLS = {"£": "GBP", "€": "EUR", "₹": "INR"}


def check_currency(je: Dict[str, Any], documents: List[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """(status, details), or None when the JE states no currency."""
    wanted = {c.strip().upper() for c in [je["header"].get("currency"), *(l.get("currency") for l in je["lines"])]
              if c and c.strip()}
    if not wanted:
        return None
    stated = {d["file"]: _currencies(d["text"]) for d in documents if d.get("text")}
    other = {f: sorted(c - wanted) for f, c in stated.items() if c - wanted}
    je_currency = "/".join(sorted(wanted))
    if other:
        return "FAIL", "; ".join(f"{f} states {', '.join(c)}" for f, c in other.items()) + f"; the JE is in {je_currency}"
    agreeing = [f for f, c in stated.items() if c & wanted]
    if agreeing:
        return "PASS", f"{je_currency} in {', '.join(agreeing)}"
    return "CANNOT_VERIFY", f"No document states a currency; the JE is in {je_currency}"


def _currencies(text: str) -> Set[str]:
    return set(_CODE.findall(text)) | {code for symbol, code in _SYMBOLS.items() if symbol in text}
