"""
Checks in code that the documents' dates fall in the JE's period - the check a
small model got wrong most often (it passed invoices dated four months early).

Every date in the documents is read: 28/10/2026, 28.10.2026, 2026-10-28,
28 Oct 2026, October 28, 2026, and service months such as "July 2026". Slash
and dot dates are read day first (05/06/2026 is 5 June), as on UK and
European documents.

  - A date before the period starts is out of period.
  - A date after it ends is allowed for up to 62 days: payments and approvals
    usually follow the period. Lines about a payment or an approval are not
    checked at all.
  - Out-of-period dates fail the check, unless the JE says why (accrual,
    prepayment, deferral, prior period, ...): then the model judges, as that
    needs judgement. If no document gives a date, the model judges too.
"""

import calendar
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

MONTHS = {m.casefold(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.casefold(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_PATTERNS = [
    ("dmy", re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")),
    ("ymd", re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")),
    ("d_mon_y", re.compile(r"\b(\d{1,2})\s+" + _MONTH + r"\.?\s+(\d{4})\b", re.IGNORECASE)),
    ("mon_d_y", re.compile(r"\b" + _MONTH + r"\.?\s+(\d{1,2}),\s*(\d{4})\b", re.IGNORECASE)),
    ("mon_y", re.compile(r"\b" + _MONTH + r"\.?\s+(\d{4})\b", re.IGNORECASE)),
]
_SKIP_LINE = re.compile(r"approv|authori[sz]|\bpaid\b|payment|remittance|\bdue\b|\bterms\b", re.IGNORECASE)
_EXPLAINED = re.compile(r"accru|prepa(?:id|y)|deferr|prior[ -]period|previous period|true[ -]?up|revers|catch[ -]?up",
                        re.IGNORECASE)
GRACE = timedelta(days=62)


def check_dates(je: Dict[str, Any], documents: List[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """(status, details), or None when the model should judge (no period, no dates, or the JE explains
    out-of-period dates)."""
    period = _period(je)
    if period is None:
        return None
    start, end = period
    found: List[Tuple[str, str, date, date]] = []  # (file, as written, first day, last day)
    for d in documents:
        for line in (d.get("text") or "").splitlines():
            if not _SKIP_LINE.search(line):
                found += [(d["file"], *x) for x in _dates(line)]
    if not found:
        return None
    outside = [(f, s) for f, s, first, last in found if last < start or first > end + GRACE]
    span = f"{start:%d/%m/%Y}-{end:%d/%m/%Y}"
    if not outside:
        return "PASS", f"All {len(found)} document dates fall in the JE period {span}"
    if _EXPLAINED.search(_je_text(je)):
        return None
    listed = "; ".join(f"{s} ({f})" for f, s in dict.fromkeys(outside))
    return "FAIL", f"Outside the JE period {span}, and the JE doesn't say why: {listed}"


def _period(je: Dict[str, Any]) -> Optional[Tuple[date, date]]:
    """The JE's month: from a YYYY-MM period, else from the posting date."""
    header = je.get("header", {})
    m = re.fullmatch(r"\s*(\d{4})[-/.](\d{1,2})\s*", str(header.get("period") or ""))
    if m:
        year, month = int(m.group(1)), int(m.group(2))
    else:
        posted = _dates(str(header.get("posting_date") or ""))
        if not posted:
            return None
        year, month = posted[0][1].year, posted[0][1].month
    if not 1 <= month <= 12:
        return None
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _dates(text: str) -> List[Tuple[str, date, date]]:
    """(as written, first day, last day) of each date in the text; a month alone covers the month."""
    out, taken = [], []
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue  # "28 Oct 2026" is not also "Oct 2026"
            g = m.groups()
            try:
                if kind == "dmy":
                    day = date(int(g[2]), int(g[1]), int(g[0]))
                elif kind == "ymd":
                    day = date(int(g[0]), int(g[1]), int(g[2]))
                elif kind == "d_mon_y":
                    day = date(int(g[2]), _month(g[1]), int(g[0]))
                elif kind == "mon_d_y":
                    day = date(int(g[2]), _month(g[0]), int(g[1]))
                else:
                    year, month = int(g[1]), _month(g[0])
                    out.append((m.group(), date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])))
                    taken.append(m.span())
                    continue
            except (ValueError, KeyError):
                continue  # not a real date (31/02/2026, or a month name that isn't one)
            out.append((m.group(), day, day))
            taken.append(m.span())
    return out


def _month(name: str) -> int:
    """1-12 for a month's name or abbreviation (Sept included)."""
    name = name.casefold()
    return MONTHS[name] if name in MONTHS else MONTHS[name[:3]]


def _je_text(je: Dict[str, Any]) -> str:
    header = " ".join(str(v) for v in je.get("header", {}).values() if v)
    lines = " ".join(str(l.get("description") or "") for l in je.get("lines", []))
    return f"{header} {lines}"
