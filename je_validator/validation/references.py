"""
Checks in code that every reference the JE gives (invoice number, PO, ...)
appears in the documents, exactly: a transposed digit (INV-7781 against
INV-7718) is the kind of slip this check is for. Case and separators don't
matter, so INV-7781, INV 7781 and inv7781 are the same reference.
"""

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

_REFERENCE_LIKE = re.compile(r"\b[A-Za-z]{0,5}[-/ ]?\d[\d/-]{2,}\b")  # candidates for the "closest" hint


def check_references(je: Dict[str, Any], documents: List[Dict[str, Any]]
                     ) -> Optional[Tuple[str, str, Dict[int, str]]]:
    """(status, details, line number -> issue), or None when the JE gives no reference."""
    references: Dict[str, List[Any]] = {}
    if je["header"].get("reference"):
        references.setdefault(je["header"]["reference"], []).append("header")
    for line in je["lines"]:
        if line.get("reference"):
            references.setdefault(line["reference"], []).append(line["line"])
    texts = [(d["file"], d["text"], "OCR" in d.get("method", "")) for d in documents if d.get("text")]
    status, notes, issues = "PASS", [], {}
    for reference, where in references.items():
        pattern = _pattern(reference)
        if pattern is None:
            continue
        found = [f for f, text, _ in texts if pattern.search(text)]
        if found:
            notes.append(f"{reference} is in {', '.join(found)}")
            continue
        status = "FAIL"
        hint = _closest(reference, texts)
        at = ", ".join(w if w == "header" else f"line {w}" for w in where)
        notes.append(f"{reference} ({at}) isn't in any document{hint}")
        for w in where:
            if w != "header":
                issues[w] = f"Reference {reference} isn't in the documents{hint}."
    if not notes:
        return None
    return status, "; ".join(notes), issues


def _pattern(reference: str) -> Optional["re.Pattern"]:
    chars = [c for c in reference if c.isalnum()]
    if len(chars) < 3:  # too short to tell apart from any other number
        return None
    return re.compile(r"(?<![A-Za-z0-9])" + r"[-/. ]?".join(re.escape(c) for c in chars) + r"(?![A-Za-z0-9])",
                      re.IGNORECASE)


def _closest(reference: str, texts) -> str:
    target = "".join(c for c in reference.casefold() if c.isalnum())
    best = (0.0, "", "", False)
    for file, text, by_ocr in texts:
        for m in _REFERENCE_LIKE.finditer(text):
            candidate = "".join(c for c in m.group().casefold() if c.isalnum())
            score = SequenceMatcher(None, target, candidate).ratio()
            if score > best[0]:
                best = (score, m.group(), file, by_ocr)
    if best[0] < 0.6:
        return ""
    return f"; the documents have {best[1]} ({best[2]}{', read by OCR' if best[3] else ''})"
