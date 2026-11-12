"""
Replaces personal data with placeholders such as [PERSON-A] or [IBAN-A]. The
same value always gets the same placeholder within one validation, so the model
can still see that two documents name the same person without seeing the name.
Placeholders hold no digits, so they are never taken for amounts.

Found by rules, in this order:
  1. fixed formats: email, IBAN (checksum), card number (Luhn), US SSN, UK NI
     number, phone (international, or after Tel/Phone/Mobile/Fax), sort code
     and bank account number (after their labels), street address, UK postcode
  2. names already known from the JE (preparer, approver)
  3. names after a label: "Approved by:", "Contact:", "Attn:", "Signed:", ...
  4. names in a table column headed Approver, Name, Employee, Contact, ...
  5. a title or an initial before a surname: "Mr Smith", "M. Jones"
A name with none of these around it isn't found; minimise.py then drops its
line unless the line also holds a value a check needs.
"""

import re
from typing import Callable, Dict, Iterable

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
_CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_NI = re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2} ?\d{2} ?\d{2} ?\d{2} ?[A-D]\b")
_PHONE = re.compile(r"(?:(?i:\b(?:tel|telephone|phone|mobile|mob|fax)\b)\.?:?\s*|(?<![\w+])(?=\+\d))"
                    r"(\+?\(?\d[\d ().-]{6,}\d)")
_SORT_CODE = re.compile(r"(?i:sort[ -]?code)\s*:?\s*(\d{2}[- ]?\d{2}[- ]?\d{2})")
_ACCOUNT_NO = re.compile(r"(?i:\b(?:account|acct|a/c)\s*(?:no\.?|number|#))\s*:?\s*(\d{6,12})")
_STREET = re.compile(r"\b\d{1,4}[A-Za-z]? (?:[A-Z][a-z]+ ){1,3}(?:Street|St|Road|Rd|Lane|Ln|Avenue|Ave|Way|Drive|Dr|"
                     r"Close|Court|Place|Square|Crescent|Terrace|Gardens|Park|Hill|Row|Boulevard|Blvd)\b\.?")
_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]? \d[A-Z]{2}\b")

_NAME = r"(?:(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?[ \t]+)?[A-Z][A-Za-z'’-]*\.?(?:[ \t]+[A-Z][A-Za-z'’-]*\.?){0,3}"
_LABELLED = re.compile(r"(?i:\b(?:(?:approved|prepared|authori[sz]ed|signed|reviewed|requested|submitted|"
                       r"entered|checked|raised)[ \t]+by|approver|contact(?:[ \t]+person)?|attn|attention|"
                       r"signatory|signed|account[ \t]+manager|account[ \t]+holder|payee|employee|claimant|dear)\b)"
                       r"[ \t]*[:\-]?[ \t]*(" + _NAME + ")")
_TITLED = re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?[ \t]+[A-Z][A-Za-z'’-]+(?:[ \t]+[A-Z][A-Za-z'’-]+)?")
_INITIALLED = re.compile(r"\b[A-Z]\.[ \t]?(?:[A-Z]\.[ \t]?)?[A-Z][a-z][A-Za-z'’-]+")
_NAME_COLUMNS = {"approver", "approved by", "name", "full name", "employee", "employee name", "contact",
                 "prepared by", "signatory", "signed by", "requested by", "reviewed by", "claimant", "payee"}


class Masker:
    """One per validation: it holds the value -> placeholder table, which is never stored or sent."""

    def __init__(self, known_names: Iterable[str] = ()):
        self._tokens: Dict[str, Dict[str, str]] = {}
        self._known = [n for n in {" ".join(n.split()) for n in known_names if n and n.strip()} if len(n) > 2]
        self._known_re = (re.compile("|".join(re.escape(n).replace(r"\ ", r"\s+")
                                                for n in sorted(self._known, key=len, reverse=True)), re.I)
                          if self._known else None)

    def token(self, kind: str, value: str) -> str:
        table = self._tokens.setdefault(kind, {})
        key = " ".join(value.split()).casefold()
        if key not in table:
            table[key] = f"[{kind}-{_letters(len(table))}]"
        return table[key]

    def text(self, text: str) -> str:
        lines, name_columns = [], set()
        for line in text.splitlines():
            line, name_columns = self._table_row(line, name_columns)
            lines.append(self._line(line))
        return "\n".join(lines)

    def _line(self, s: str) -> str:
        s = _EMAIL.sub(lambda m: self.token("EMAIL", m.group()), s)
        s = _IBAN.sub(lambda m: self.token("IBAN", m.group()) if _iban_ok(m.group()) else m.group(), s)
        s = _CARD.sub(lambda m: self.token("CARD", m.group()) if _luhn_ok(m.group()) else m.group(), s)
        s = _SSN.sub(lambda m: self.token("ID", m.group()), s)
        s = _NI.sub(lambda m: self.token("ID", m.group()), s)
        s = _SORT_CODE.sub(_group(lambda v: self.token("BANK", v)), s)
        s = _ACCOUNT_NO.sub(_group(lambda v: self.token("BANK", v)), s)
        s = _PHONE.sub(_group(lambda v: self.token("PHONE", v)), s)
        s = _STREET.sub(lambda m: self.token("ADDRESS", m.group()), s)
        s = _POSTCODE.sub(lambda m: self.token("ADDRESS", m.group()), s)
        if self._known_re:
            s = self._known_re.sub(lambda m: self.token("PERSON", m.group()), s)
        s = _LABELLED.sub(_group(lambda v: self.token("PERSON", v.rstrip(". "))), s)
        s = _TITLED.sub(lambda m: self.token("PERSON", m.group()), s)
        return _INITIALLED.sub(lambda m: self.token("PERSON", m.group()), s)

    def _table_row(self, line: str, name_columns: set):
        """Rows from the readers are 'a | b | c'. A header row naming a person column starts masking
        that column's cells; any line that isn't a table row ends it."""
        if " | " not in line:
            return line, set()
        cells = line.split(" | ")
        headers = {i for i, c in enumerate(cells) if " ".join(c.split()).casefold().rstrip(":") in _NAME_COLUMNS}
        if headers:
            return line, headers
        for i in name_columns & set(range(len(cells))):
            m = re.match(r"\s*(" + _NAME + ")", cells[i])
            if m and m.group(1).strip():
                name = m.group(1).strip()
                cells[i] = cells[i].replace(name, self.token("PERSON", name), 1)
        return " | ".join(cells), name_columns


def _group(replace: Callable[[str], str]):
    """A re.sub callback that replaces only group 1 of the match, keeping its label."""
    def sub(m):
        start, end = m.span(1) if m.group(1) else (m.end(), m.end())
        if not m.group(1) or not m.group(1).strip() or m.group(1).startswith("["):
            return m.group()
        return m.group()[:start - m.start()] + replace(m.group(1)) + m.group()[end - m.start():]
    return sub


def _letters(n: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA ..."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _iban_ok(value: str) -> bool:
    s = value.replace(" ", "")
    digits = "".join(str(int(ch, 36)) for ch in s[4:] + s[:4])
    return 15 <= len(s) <= 34 and int(digits) % 97 == 1


def _luhn_ok(value: str) -> bool:
    digits = [int(d) for d in value if d.isdigit()]
    total = sum(d if i % 2 == 0 else (d * 2 - 9 if d > 4 else d * 2) for i, d in enumerate(reversed(digits)))
    return total % 10 == 0
