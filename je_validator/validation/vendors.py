"""
Checks in code that every vendor the JE names is named in the documents. A
model can overlook a difference such as "Gbx Ltd" against "Globex Ltd".

Names are compared ignoring case, punctuation and legal forms (Ltd, Limited,
LLP, Inc, GmbH, ...), so "Globex Ltd" and "Globex Limited" match. A name that
is close but not the same (e.g. an OCR misread "Glob3x Ltd") passes with
lowered confidence, so it gets a second look; anything else fails.
"""

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from je_validator.validation.amounts import CURRENCY_CODES

LEGAL_FORMS = {"ltd", "limited", "llp", "lp", "plc", "inc", "incorporated", "corp", "corporation", "co", "company",
               "llc", "gmbh", "ag", "bv", "nv", "sa", "sas", "sarl", "srl", "spa", "pvt", "private", "pte", "pty",
               "the"}
_CURRENCIES = {c.casefold() for c in CURRENCY_CODES}
_PLACEHOLDER = re.compile(r"\[[A-Z]+-[A-Z]+\]")  # [PERSON-A], from privacy/mask.py
NEAR = 0.8  # similarity at or above which a different name is taken as a likely misreading
NEAR_CONFIDENCE = 70


def check_vendors(je_lines: List[Dict[str, Any]], documents: List[Dict[str, Any]]
                  ) -> Optional[Tuple[str, str, int, Dict[int, str]]]:
    """(status, details, confidence, line number -> issue), or None when the JE names no vendor."""
    vendors: Dict[str, List[int]] = {}
    for line in je_lines:
        if line.get("vendor"):
            vendors.setdefault(line["vendor"], []).append(line["line"])
    if not vendors:
        return None
    texts = [(d["file"], d["text"]) for d in documents if d.get("text")]
    status, confidence, notes, issues = "PASS", 100, [], {}
    for vendor, line_numbers in vendors.items():
        wanted = _words(vendor)
        if not wanted:
            continue
        found = [f for f, text in texts if _contains(_words(text), wanted)]
        if found:
            notes.append(f"'{vendor}' is named in {', '.join(found)}")
            continue
        score, closest, where = _closest(wanted, texts)
        at = ", ".join(f"line {n}" for n in line_numbers)
        if score >= NEAR:
            confidence = min(confidence, NEAR_CONFIDENCE)
            notes.append(f"'{vendor}' ({at}) nearly matches '{closest}' in {where}: check it (OCR may have misread it)")
            continue
        status = "FAIL"
        hint = f"; the closest is '{closest}' in {where}" if score >= 0.6 else ""
        notes.append(f"'{vendor}' ({at}) isn't named in any document{hint}")
        for n in line_numbers:
            issues[n] = f"Vendor '{vendor}' isn't named in the documents{hint}."
    return status, "; ".join(notes), confidence, issues


def _words(text: str) -> List[str]:
    """The words that can be part of a vendor name: not legal forms, currency codes or masked placeholders."""
    text = _PLACEHOLDER.sub(" ", text).casefold().replace("&", " and ")
    return [w for w in re.findall(r"[^\W_]+", text) if w not in LEGAL_FORMS and w not in _CURRENCIES]


def _contains(words: List[str], wanted: List[str]) -> bool:
    n = len(wanted)
    return any(words[i:i + n] == wanted for i in range(len(words) - n + 1))


def _closest(wanted: List[str], texts: List[Tuple[str, str]]) -> Tuple[float, Optional[str], Optional[str]]:
    """The most similar run of words in the documents, as written there (with its legal form, 'Globex
    Ltd'): (similarity, those words, their file)."""
    target, best, at = " ".join(wanted), (0.0, None, None), None
    for file, text in texts:
        for line in text.splitlines():
            tokens = re.findall(r"[^\W_]+(?:[&'.][^\W_]+)*", line)  # the line's words as written
            for size in range(max(1, len(wanted) - 1), len(wanted) + 2):
                for i in range(max(1, len(tokens) - size + 1)):
                    words = _words(" ".join(tokens[i:i + size]))
                    if not words:
                        continue
                    score = SequenceMatcher(None, target, " ".join(words)).ratio()
                    if score > best[0]:
                        best, at = (score, None, file), (tokens, i, size)
    if at:
        tokens, i, size = at
        while i + size < len(tokens) and tokens[i + size].casefold() in LEGAL_FORMS:  # "Globex" + "Ltd"
            size += 1
        best = (best[0], " ".join(tokens[i:i + size]), best[2])
    return best
