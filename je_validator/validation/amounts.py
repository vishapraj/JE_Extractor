"""Finds and tags every amount in the documents' text, so a model can only point at amounts that are there."""

import re
from typing import Any, Dict, List, Optional, Tuple

from je_validator.documents import READER_HEADER

_NUMBER = re.compile(r"\d[\d,.]*")
_EUROPEAN = re.compile(r"\d{1,3}(\.\d{3})+(,\d+)?")  # 1.234,50


def tag_amounts(documents: List[Dict[str, Any]]) -> Tuple[Dict[str, str], Dict[int, Dict[str, Any]]]:
    """Each document's text with every amount tagged [#n], and n -> {value, document, text of its line}.
    The model refers to amounts by tag, so it can't put a figure in a document that isn't there."""
    tagged, amounts = {}, {}
    for d in documents:
        out = []
        for text_line in d["text"].splitlines():
            def tag(m, text_line=text_line):
                value = _amount_value(text_line, m)
                if value is None:
                    return m.group(0)
                n = len(amounts) + 1
                amounts[n] = {"value": value, "document": d["file"], "text": " ".join(text_line.split())}
                return f"{m.group(0)} [#{n}]"
            # "[Page 1 - text layer]" and similar headers come from the readers, not the document
            out.append(text_line if READER_HEADER.match(text_line) else _NUMBER.sub(tag, text_line))
        tagged[d["file"]] = "\n".join(out)
    return tagged, amounts


def has_amount(text_line: str) -> bool:
    return any(_amount_value(text_line, m) is not None for m in _NUMBER.finditer(text_line))


def _amount_value(text_line: str, m: "re.Match") -> Optional[float]:
    """The number as an amount, or None when it is part of a date (28/10/2026, 2026-10-28) or a code (INV-7781)."""
    before = text_line[max(0, m.start() - 2):m.start()]
    after = text_line[m.end():m.end() + 2]
    if before.endswith("/") or after.startswith("/") or (after[:1] == "-" and after[1:2].isdigit()):
        return None
    if before[-1:].isalpha() or (before[-1:] == "-" and (before[:1].isalnum())) or after[:1].isalpha():
        return None
    token = m.group(0).rstrip(".,")
    if token.isdigit() and 1900 <= int(token) <= 2100:
        return None  # a year, as in "October 2026"
    token = token.replace(".", "").replace(",", ".") if _EUROPEAN.fullmatch(token) else token.replace(",", "")
    try:
        return abs(float(token))
    except ValueError:
        return None
