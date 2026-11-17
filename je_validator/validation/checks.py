"""One model's review, checked in code: amounts are compared here, not taken from the model."""

import re
from typing import Any, Dict, List, Optional, Tuple

from je_validator.config import AMOUNT_TOLERANCE
from je_validator.validation.amounts import tag_amounts
from je_validator.validation.review import MODEL_RULES, RULES, LineMatch, Review
from je_validator.validation.currency import check_currency
from je_validator.validation.dates import check_dates
from je_validator.validation.references import check_references
from je_validator.validation.vendors import check_vendors


def assess(je: Dict[str, Any], documents: List[Dict[str, Any]], review: Review, model: str) -> Dict[str, Any]:
    """One model's review, checked in code. Its confidence is that of its least confident item."""
    reviewed = {d.file: d for d in review.documents}
    for d in documents:
        if "error" not in d:  # replaces an earlier model's summary, if any
            r = reviewed.get(d["file"])
            d.update(doc_type=r.doc_type if r else None, summary=r.summary if r else None)

    lines = _check_lines(je["lines"], review.lines, tag_amounts([d for d in documents if "error" not in d])[1],
                         {d["file"]: d.get("doc_type") for d in documents if "error" not in d})
    checks = [_balanced(je["totals"])]
    if unread := [d["file"] for d in documents if "error" in d]:
        checks.append(_check("DOCUMENTS_READ", "CANNOT_VERIFY", "Could not read: " + ", ".join(unread), "code"))
    checks.append(_amounts(lines, model))
    readable = [d for d in documents if "error" not in d]
    if vendors := check_vendors(je["lines"], readable):
        status, details, confidence, issues = vendors
        checks.append(_check("VENDOR", status, details, "code", confidence))
        _add_issues(lines, issues, "VENDOR_MISMATCH")
    if references := check_references(je, readable):
        status, details, issues = references
        checks.append(_check("REFERENCE", status, details, "code"))
        _add_issues(lines, issues, "REFERENCE_MISMATCH")
    if currency := check_currency(je, readable):
        checks.append(_check("CURRENCY", *currency, "code"))
    model_rules = {r.rule: r for r in review.rules}
    # Dates are checked in code where code can decide; the model judges what needs judgement (an
    # out-of-period date the JE explains, or no dates at all).
    by_code = {"DATES_IN_PERIOD": check_dates(je, readable)}
    for rule in MODEL_RULES:
        r = model_rules.get(rule)
        if by_code.get(rule):
            checks.append(_check(rule, *by_code[rule], "code"))
        else:
            checks.append(_check(rule, r.status, r.details, model, r.confidence) if r else
                          _check(rule, "CANNOT_VERIFY", "No result for this check.", model, 0))

    statuses = {c["status"] for c in checks}
    overall = "FAIL" if "FAIL" in statuses else "NEEDS_REVIEW" if "CANNOT_VERIFY" in statuses else "PASS"
    confidence = min(c["confidence"] for c in lines + checks)
    return {"model": model, "overall": overall, "confidence": confidence, "summary": review.summary,
            "checks": checks, "journal_entry": je, "lines": lines, "documents": documents}


def _add_issues(lines: List[Dict[str, Any]], issues: Dict[int, str], flag: str) -> None:
    """A line's status is about its amount; a vendor or reference that doesn't match is a flag beside it
    (shown next to the status), and an issue saying what was found."""
    for r in lines:
        if r["line"] in issues:
            r["issues"].append(issues[r["line"]])
            r["flags"].append(flag)


def _check(rule: str, status: str, details: str, checked_by: str, confidence: int = 100) -> Dict[str, Any]:
    """Checks done in code are certain: confidence 100."""
    return {"rule": rule, "name": RULES[rule], "status": status, "details": details, "checked_by": checked_by,
            "confidence": max(0, min(100, confidence))}


def _balanced(totals: Dict[str, float]) -> Dict[str, str]:
    ok = abs(totals["debit"] - totals["credit"]) <= AMOUNT_TOLERANCE
    return _check("BALANCED", "PASS" if ok else "FAIL",
                  f"Debits {totals['debit']:,.2f}, credits {totals['credit']:,.2f}", "code")


def _check_lines(je_lines: List[Dict[str, Any]], matches: List[LineMatch],
                 amounts: Dict[int, Dict[str, Any]], doc_types: Optional[Dict[str, Optional[str]]] = None
                 ) -> List[Dict[str, Any]]:
    """The model's match for each line, decided here: the model only says which tagged document amounts
    belong to the line, so the values, the document and the evidence text all come from the documents.
    Where the model was wrong or silent, its confidence counts as 0."""
    by_line = {m.line: m for m in matches}
    results = []
    for line in je_lines:
        m = by_line.get(line["line"])
        r = m.model_dump() if m else {"line": line["line"], "status": "NOT_FOUND", "amount_ids": [],
                                      "evidence": None, "issues": ["Not reviewed."], "confidence": 0}
        ids = list(dict.fromkeys(r.pop("amount_ids")))
        if not ids and (cited := [int(n) for n in re.findall(r"\[#(\d+)\]", r.get("evidence") or "")]):
            # The model cited amounts in its reason but left its answer's amounts empty. Use them: the values
            # still come from the documents and are compared below, but the answer was inconsistent, so the
            # line is held below the review threshold.
            ids = list(dict.fromkeys(cited))
            r["issues"].append(f"The model cited {', '.join(f'#{i}' for i in ids)} in its reason but not in its "
                               "answer; those amounts were used.")
            r["confidence"] = min(r["confidence"], CITED_ONLY_CONFIDENCE)
        picked, row_notes = _one_per_row([amounts[i] for i in ids if i in amounts])
        picked, notes = _one_total(picked)
        r["issues"] += row_notes + notes
        je_amount = abs((line["debit"] or 0) - (line["credit"] or 0))
        counted, note = _confirmed_once(picked, doc_types or {})
        if note:
            r["issues"].append(note)
        r["note"] = r.pop("evidence")  # the model's reason; the evidence is the documents' own text
        r["flags"] = []  # problems other than the amount, found in code (vendor, reference)
        r.update(document=", ".join(dict.fromkeys(a["document"] for a in picked)) or None,
                 document_amount=round(sum(a["value"] for a in counted), 2) if picked else None,
                 evidence=" | ".join(dict.fromkeys(a["text"] for a in picked)) or None,
                 # where the evidence is on the documents' pages, for the viewer to highlight
                 sources=[dict(s) for s in dict.fromkeys(
                     tuple((k, a.get(k)) for k in ("document", "page", "zone", "image")) for a in picked
                     if a.get("page"))])
        if unknown := [i for i in ids if i not in amounts]:
            r["issues"].append(f"The model pointed at amounts that don't exist ({', '.join(f'#{i}' for i in unknown)}). "
                               "Check this line by hand.")
            r.update(status="UNVERIFIED", confidence=0)
        elif not picked:
            if r["status"] != "NOT_FOUND":
                r["issues"].append(f"The model said {r['status']} but picked no document amount.")
                r["confidence"] = 0
            r["status"] = "NOT_FOUND"
        elif abs(r["document_amount"] - je_amount) <= AMOUNT_TOLERANCE:
            r["status"] = "SUPPORTED"  # the values agree, whatever the model called it
        else:
            if r["status"] == "SUPPORTED":
                r["confidence"] = 0  # it called different amounts a match
            r["status"] = "MISMATCH"
            r["issues"].append(f"The document shows {r['document_amount']:,.2f}, the JE {je_amount:,.2f}.")
        r["confidence"] = max(0, min(100, r["confidence"]))
        results.append(r)
    return results


CITED_ONLY_CONFIDENCE = 70  # below the default threshold (80): an inconsistent answer gets a second look

_TOTAL_LINE = re.compile(r"\b(?:sub-?total|total|net|gross|amount due|balance due)\b", re.IGNORECASE)
_RATE = re.compile(r"\d\s?%")
_ITEM_ROW = re.compile(r"\b(?:description|qty|quantity|item)\b", re.IGNORECASE)


def _one_total(picked: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """(the amounts without restated totals, notes). A model sometimes picks both 'Subtotal 3,000.00' and
    'Total 3,000.00' for one JE line: one figure, stated twice. Equal amounts from total lines of the same
    document count once; equal amounts from item lines (two licences at 500.00) still add up. It also
    picks an item with the total that contains it ('Printing 3,000.00' and 'Subtotal 3,000.00' on a
    one-item invoice): a total equal to the sum of the items picked from its document restates them, so
    it isn't added again. A total that differs from them (VAT on top) is kept: the line then mismatches."""
    seen, kept, notes = set(), [], []
    for a in picked:
        if _is_total(a):
            if (a["document"], a["value"]) in seen:
                if not notes:
                    notes.append("The model picked the same total twice (e.g. Subtotal and Total); it is counted once.")
                continue
            seen.add((a["document"], a["value"]))
        kept.append(a)
    for document in dict.fromkeys(a["document"] for a in kept):
        items = [a for a in kept if a["document"] == document and not _is_total(a)]
        restated = [a for a in kept if a["document"] == document and _is_total(a) and items
                    and abs(a["value"] - sum(i["value"] for i in items)) <= AMOUNT_TOLERANCE]
        if restated:
            kept = [a for a in kept if not any(a is t for t in restated)]
            notes.append(f"The model picked {document}'s items and the total that contains them "
                         f"({restated[0]['value']:,.2f}); the total is not added again.")
    return kept, notes


def _is_total(amount: Dict[str, Any]) -> bool:
    """Is the amount a total? In a row of a table with headings ('Description: Licence | Total: 500.00')
    the row's first cell says what it is, so items in a table with a 'Total' column are still items.
    Elsewhere the label is what is written just before the amount - the cell before it, or the start of
    its own cell - so 'Subtotal | 3,384.76' is a total even when the row also holds the cells of a block
    beside it ('Rate | Net | VAT | Gross | Subtotal | 3,384.76'). A row with a VAT rate that isn't an item
    row ('Rate: 0% | Net: 1,827.00 | VAT: 0.00 | Gross: 1,827.00') is the VAT summary: its figures are
    totals of the items above it - as is every figure in a zone whose words made it the VAT summary
    (documents/roles.py), whether or not its row shows the rate."""
    if amount.get("role") == "vat_summary":
        return True
    text = amount["text"]
    if _RATE.search(text) and not _ITEM_ROW.search(text):
        return True
    cells = text.split(" | ")
    if sum(": " in c for c in cells) >= 2:
        label = cells[0]
    else:
        before = text[:amount["at"]]
        label = (before[:-3] if before.endswith(" | ") else before).rsplit(" | ", 1)[-1]
    return bool(_TOTAL_LINE.search(label))


def _one_per_row(picked: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """(the amounts with one per row, notes). A table row is one item, but a model sometimes picks
    several of its numbers - 'Qty: 1 | Unit price: 3,037.00 | Amount: 3,037.00' gave 1 + 3,037 + 3,037.
    Of the numbers picked from one row only the rightmost counts: the row's Amount column. Which one counts
    is fixed by its place, not by whether it equals the JE, so a JE that books the unit price instead of
    the amount still mismatches. Two rows with the same text are still two rows."""
    rows: Dict[Tuple[Any, Any], List[Dict[str, Any]]] = {}
    for a in picked:
        rows.setdefault((a["document"], a["row"]), []).append(a)
    kept, notes = [], []
    for (document, _), same in rows.items():
        last = max(same, key=lambda a: a["at"])
        kept.append(last)
        if len(same) > 1:
            values = ", ".join(format(a["value"], ",.2f") for a in same)
            notes.append(f"The model picked {len(same)} numbers from one row of {document} ({values}); a row is "
                         f"one item, so only its amount, {last['value']:,.2f} (the rightmost), counts.")
    return kept, notes


def _confirmed_once(picked: List[Dict[str, Any]], doc_types: Dict[str, Optional[str]]
                    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """(the amounts to add up, a note). A model sometimes picks one figure twice from two kinds of
    document - an invoice's total and the bank payment of it. That is not two costs, so equal amounts from
    documents of different types (as the review typed them) always count once: adding them would let a
    JE that books the amount twice look supported. Two invoices of the same amount are the same type:
    they still add up. (Several numbers from one row are handled by _one_per_row.)"""
    if not picked:
        return picked, None
    counted, notes = [], []
    for value in dict.fromkeys(round(a["value"], 2) for a in picked):
        same = [a for a in picked if round(a["value"], 2) == value]
        docs = list(dict.fromkeys(a["document"] for a in same))
        types = [doc_types.get(d) for d in docs]
        if len(docs) >= 2 and None not in types and len(set(types)) == len(docs) and len(same) == len(docs):
            counted.append(same[0])
            notes.append(f"{value:,.2f} is in {' and '.join(docs)}: the same amount confirmed by both, counted once.")
        else:
            counted += same
    return counted, " ".join(notes) or None


def _amounts(lines: List[Dict[str, Any]], model: str) -> Dict[str, Any]:
    counts = {s: sum(r["status"] == s for r in lines) for s in ("SUPPORTED", "MISMATCH", "NOT_FOUND", "UNVERIFIED")}
    status = ("FAIL" if counts["MISMATCH"] else
              "CANNOT_VERIFY" if counts["NOT_FOUND"] or counts["UNVERIFIED"] else "PASS")
    details = (f"{counts['SUPPORTED']} of {len(lines)} lines supported, {counts['MISMATCH']} mismatched, "
               f"{counts['NOT_FOUND']} not found")
    if counts["UNVERIFIED"]:
        details += f", {counts['UNVERIFIED']} where the model pointed at amounts that don't exist"
    return _check("AMOUNTS", status, details, f"{model}, amounts rechecked in code",
                  min((r["confidence"] for r in lines), default=100))
