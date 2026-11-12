"""One model's review, checked in code: amounts are compared here, not taken from the model."""

from typing import Any, Dict, List

from je_validator.config import AMOUNT_TOLERANCE
from je_validator.validation.amounts import tag_amounts
from je_validator.validation.review import MODEL_RULES, RULES, LineMatch, Review
from je_validator.validation.vendors import check_vendors


def assess(je: Dict[str, Any], documents: List[Dict[str, Any]], review: Review, model: str) -> Dict[str, Any]:
    """One model's review, checked in code. Its confidence is that of its least confident item."""
    reviewed = {d.file: d for d in review.documents}
    for d in documents:
        if "error" not in d:  # replaces an earlier model's summary, if any
            r = reviewed.get(d["file"])
            d.update(doc_type=r.doc_type if r else None, summary=r.summary if r else None)

    lines = _check_lines(je["lines"], review.lines, tag_amounts([d for d in documents if "error" not in d])[1])
    checks = [_balanced(je["totals"])]
    if unread := [d["file"] for d in documents if "error" in d]:
        checks.append(_check("DOCUMENTS_READ", "CANNOT_VERIFY", "Could not read: " + ", ".join(unread), "code"))
    checks.append(_amounts(lines, model))
    if vendors := check_vendors(je["lines"], [d for d in documents if "error" not in d]):
        status, details, confidence, issues = vendors
        checks.append(_check("VENDOR", status, details, "code", confidence))
        for r in lines:
            if r["line"] in issues:
                r["issues"].append(issues[r["line"]])
    model_rules = {r.rule: r for r in review.rules}
    for rule in MODEL_RULES:
        r = model_rules.get(rule)
        checks.append(_check(rule, r.status, r.details, model, r.confidence) if r else
                      _check(rule, "CANNOT_VERIFY", "No result for this check.", model, 0))

    statuses = {c["status"] for c in checks}
    overall = "FAIL" if "FAIL" in statuses else "NEEDS_REVIEW" if "CANNOT_VERIFY" in statuses else "PASS"
    confidence = min(c["confidence"] for c in lines + checks)
    return {"model": model, "overall": overall, "confidence": confidence, "summary": review.summary,
            "checks": checks, "journal_entry": je, "lines": lines, "documents": documents}


def _check(rule: str, status: str, details: str, checked_by: str, confidence: int = 100) -> Dict[str, Any]:
    """Checks done in code are certain: confidence 100."""
    return {"rule": rule, "name": RULES[rule], "status": status, "details": details, "checked_by": checked_by,
            "confidence": max(0, min(100, confidence))}


def _balanced(totals: Dict[str, float]) -> Dict[str, str]:
    ok = abs(totals["debit"] - totals["credit"]) <= AMOUNT_TOLERANCE
    return _check("BALANCED", "PASS" if ok else "FAIL",
                  f"Debits {totals['debit']:,.2f}, credits {totals['credit']:,.2f}", "code")


def _check_lines(je_lines: List[Dict[str, Any]], matches: List[LineMatch],
                 amounts: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
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
        picked = [amounts[i] for i in ids if i in amounts]
        r["note"] = r.pop("evidence")  # the model's reason; the evidence is the documents' own text
        r.update(document=", ".join(dict.fromkeys(a["document"] for a in picked)) or None,
                 document_amount=round(sum(a["value"] for a in picked), 2) if picked else None,
                 evidence=" | ".join(dict.fromkeys(a["text"] for a in picked)) or None)
        je_amount = abs((line["debit"] or 0) - (line["credit"] or 0))
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
