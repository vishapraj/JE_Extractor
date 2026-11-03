"""
Validates a journal entry (JE) Excel against its supporting documents.

The JE is read with a template from the database (je_templates.py) and the
documents by their own libraries or OCR (readers.py); no LLM reads files.
Claude Haiku (or a local model) then compares the JE with the documents'
text, and the arithmetic is checked again in code.

    python validator.py --je JE.xlsx invoice.pdf bank.pdf approval.docx
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import anthropic
import ollama
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from je_templates import read_je
from readers import read_document

load_dotenv(Path(__file__).parent / ".env")

CLAUDE_PLATFORM = os.getenv("CLAUDE_PLATFORM", "anthropic")  # anthropic | vertex | bedrock | local (Ollama)
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL") or (
    "anthropic.claude-haiku-5-5" if CLAUDE_PLATFORM == "bedrock" else "claude-haiku-5-5")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "16000"))
AMOUNT_TOLERANCE = float(os.getenv("AMOUNT_TOLERANCE", "0.01"))

LOCAL = CLAUDE_PLATFORM == "local"
LOCAL_MODEL = os.getenv("LOCAL_MODEL", "qwen2.5:7b")
LOCAL_NUM_CTX = int(os.getenv("LOCAL_NUM_CTX", "32768"))  # Ollama's default context is too small for documents
MODEL = LOCAL_MODEL if LOCAL else CLAUDE_MODEL

RULES = {
    "BALANCED": "Debits equal credits",
    "DOCUMENTS_READ": "Every document read",
    "AMOUNTS": "Line amounts match the documents",
    "VENDOR_REFERENCE": "Vendors and references match",
    "DATES_IN_PERIOD": "Document dates in the JE period",
    "APPROVAL": "Approval covers the JE total",
    "CURRENCY": "Currency consistent",
}
MODEL_RULES = ("VENDOR_REFERENCE", "DATES_IN_PERIOD", "APPROVAL", "CURRENCY")  # judged by the model
Status = Literal["PASS", "FAIL", "CANNOT_VERIFY"]


class DocumentReview(BaseModel):
    file: str
    doc_type: Literal["INVOICE", "BANK_STATEMENT", "CALCULATION", "APPROVAL", "CONTRACT", "OTHER"]
    summary: str


class LineMatch(BaseModel):
    line: int
    status: Literal["SUPPORTED", "MISMATCH", "NOT_FOUND"]
    document: Optional[str]
    document_amount: Optional[float]
    evidence: Optional[str]
    issues: List[str]


class RuleResult(BaseModel):
    rule: Literal[MODEL_RULES]
    status: Status
    details: str


class Review(BaseModel):
    documents: List[DocumentReview]
    lines: List[LineMatch]
    rules: List[RuleResult]
    summary: str


SYSTEM = """\
You check a journal entry (JE) against its supporting documents for an accounting reviewer.

You get the text of each supporting document, then the JE as JSON: its header, its lines (each with a \
"line" number) and its debit and credit totals. The document text was taken from the files by software; \
text from scans and photos came from OCR and may contain misread characters.

Return:
1. documents: for each supporting document, its type and a one-sentence summary.
2. lines: one entry for every JE line. Find the document value that supports the line's amount. A line \
can be supported by one document amount or by the sum of several; say which in evidence. document_amount \
is the figure you matched (the sum, if you added several). status is SUPPORTED when the amounts agree, \
MISMATCH when a document clearly relates to the line but its amount differs, and NOT_FOUND when no \
document supports the line. List any other differences for the line (vendor, reference, date) in issues.
3. rules: one result for each of
   - VENDOR_REFERENCE: vendor names and reference numbers (invoice, PO, JE reference) in the JE agree with the documents.
   - DATES_IN_PERIOD: document dates (invoice date, service period, transaction dates) fall within the JE \
period, or the JE description explains why not (for example an accrual or a prepayment).
   - APPROVAL: an approval document exists, names the approver, and the approved amount covers the JE total.
   - CURRENCY: the documents' currency matches the JE currency.
   status is PASS when the documents show the rule holds, FAIL when they show it doesn't, and \
CANNOT_VERIFY when the documents don't contain what is needed.
4. summary: two or three sentences for the reviewer.

Use only what the JE and the documents show. Never guess or fill in a value that isn't there; say what is \
missing instead. Quote the document values you rely on in evidence and details."""


def validate(je_path: Path, doc_paths: List[Path], template: Optional[str] = None) -> Dict[str, Any]:
    je = read_je(je_path, template)
    documents = [_read(p) for p in doc_paths]
    readable = [d for d in documents if "error" not in d]
    review = _ask_model(je, readable) if readable else Review(
        documents=[], lines=[], rules=[], summary="None of the supporting documents could be read.")

    reviewed = {d.file: d for d in review.documents}
    for d in readable:
        if d["file"] in reviewed:
            d.update(doc_type=reviewed[d["file"]].doc_type, summary=reviewed[d["file"]].summary)

    lines = _check_lines(je["lines"], review.lines)
    checks = [_balanced(je["totals"])]
    if unread := [d["file"] for d in documents if "error" in d]:
        checks.append(_check("DOCUMENTS_READ", "CANNOT_VERIFY", "Could not read: " + ", ".join(unread), "code"))
    checks.append(_amounts(lines))
    model_rules = {r.rule: r for r in review.rules}
    for rule in MODEL_RULES:
        r = model_rules.get(rule)
        checks.append(_check(rule, r.status, r.details, MODEL) if r else
                      _check(rule, "CANNOT_VERIFY", "No result for this check.", MODEL))

    statuses = {c["status"] for c in checks}
    overall = "FAIL" if "FAIL" in statuses else "NEEDS_REVIEW" if "CANNOT_VERIFY" in statuses else "PASS"
    return {"model": MODEL, "overall": overall, "summary": review.summary, "checks": checks,
            "journal_entry": je, "lines": lines, "documents": documents}


def _read(path: Path) -> Dict[str, Any]:
    try:
        return read_document(path)
    except Exception as e:  # one unreadable file must not stop the others
        return {"file": path.name, "error": str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}"}


def _check(rule: str, status: str, details: str, checked_by: str) -> Dict[str, str]:
    return {"rule": rule, "name": RULES[rule], "status": status, "details": details, "checked_by": checked_by}


def _balanced(totals: Dict[str, float]) -> Dict[str, str]:
    ok = abs(totals["debit"] - totals["credit"]) <= AMOUNT_TOLERANCE
    return _check("BALANCED", "PASS" if ok else "FAIL",
                  f"Debits {totals['debit']:,.2f}, credits {totals['credit']:,.2f}", "code")


def _check_lines(je_lines: List[Dict[str, Any]], matches: List[LineMatch]) -> List[Dict[str, Any]]:
    """The model's match for each line, with its amount comparison redone here."""
    by_line = {m.line: m for m in matches}
    results = []
    for line in je_lines:
        m = by_line.get(line["line"])
        r = m.model_dump() if m else {"line": line["line"], "status": "NOT_FOUND", "document": None,
                                      "document_amount": None, "evidence": None, "issues": ["Not reviewed."]}
        je_amount = abs((line["debit"] or 0) - (line["credit"] or 0))
        if r["status"] == "SUPPORTED":
            if r["document_amount"] is None:
                r["status"] = "NOT_FOUND"
                r["issues"].append("No document amount was given.")
            elif abs(abs(r["document_amount"]) - je_amount) > AMOUNT_TOLERANCE:
                r["status"] = "MISMATCH"
                r["issues"].append(f"The document shows {abs(r['document_amount']):,.2f}, the JE {je_amount:,.2f}.")
        results.append(r)
    return results


def _amounts(lines: List[Dict[str, Any]]) -> Dict[str, str]:
    counts = {s: sum(r["status"] == s for r in lines) for s in ("SUPPORTED", "MISMATCH", "NOT_FOUND")}
    status = "FAIL" if counts["MISMATCH"] else "CANNOT_VERIFY" if counts["NOT_FOUND"] else "PASS"
    details = (f"{counts['SUPPORTED']} of {len(lines)} lines supported, {counts['MISMATCH']} mismatched, "
               f"{counts['NOT_FOUND']} not found")
    return _check("AMOUNTS", status, details, f"{MODEL}, amounts rechecked in code")


# ---------------------------------------------------------------------------
# Model call
# ---------------------------------------------------------------------------

def make_client():
    if LOCAL:
        client = ollama.Client()  # http://127.0.0.1:11434, or OLLAMA_HOST
        try:
            client.show(LOCAL_MODEL)
        except ConnectionError:
            raise ValueError("Ollama is not running: install it from ollama.com and start it.") from None
        except ollama.ResponseError:
            raise ValueError(f"Local model {LOCAL_MODEL} is not downloaded: run  ollama pull {LOCAL_MODEL}") from None
        return client
    if CLAUDE_PLATFORM == "vertex":
        return anthropic.AnthropicVertex(
            project_id=os.environ["VERTEX_PROJECT_ID"], region=os.getenv("VERTEX_REGION", "global")
        )
    if CLAUDE_PLATFORM == "bedrock":
        return anthropic.AnthropicBedrockMantle(aws_region=os.environ["AWS_REGION"])
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        raise ValueError(
            "No Claude credentials: copy .env.example to .env and set ANTHROPIC_API_KEY, "
            "or set CLAUDE_PLATFORM=vertex / bedrock / local with that platform's settings."
        )
    return anthropic.Anthropic()


_client = None


def _ask_model(je: Dict[str, Any], documents: List[Dict[str, Any]]) -> Review:
    global _client
    if _client is None:
        _client = make_client()
    prompt = _prompt(je, documents)

    if LOCAL:
        response = _client.chat(
            model=LOCAL_MODEL,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            format=Review.model_json_schema(),  # Ollama constrains the reply to the schema
            options={"temperature": 0, "num_ctx": LOCAL_NUM_CTX},
        )
        if response.done_reason == "length":
            raise ValueError("The documents are too long for the local model - the reply was cut off (raise LOCAL_NUM_CTX)")
        try:
            return Review.model_validate_json(response.message.content)
        except ValidationError:
            raise ValueError("The local model's reply did not match the expected format - try again") from None

    response = _client.messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=Review,
    )
    if response.stop_reason == "refusal":
        raise ValueError("Claude declined to review these documents")
    if response.stop_reason == "max_tokens":
        raise ValueError("The review was cut off - raise MAX_TOKENS")
    return response.parsed_output


def _prompt(je: Dict[str, Any], documents: List[Dict[str, Any]]) -> str:
    parts = [f'<document name="{d["file"]}" read_by="{d["method"]}">\n{d["text"]}\n</document>' for d in documents]
    je_view = {k: je[k] for k in ("header", "totals", "lines")}
    parts.append(f"<journal_entry>\n{json.dumps(je_view, indent=1)}\n</journal_entry>")
    parts.append("Check the journal entry against the supporting documents.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_report(result: Dict[str, Any]) -> None:
    je = result["journal_entry"]
    print(f"{je['file']}  (template {je['template']}, sheet {je['sheet']})  checked by {result['model']}")
    print(f"\nOVERALL: {result['overall']}\n  {result['summary']}\n")
    _print_table([{"check": c["name"], "status": c["status"], "details": c["details"]} for c in result["checks"]])
    print()
    matches = {r["line"]: r for r in result["lines"]}
    _print_table([{"line": l["line"], "account": l.get("account"), "debit": l["debit"], "credit": l["credit"],
                   "status": matches[l["line"]]["status"], "document": matches[l["line"]]["document"],
                   "issues": "; ".join(matches[l["line"]]["issues"])} for l in je["lines"]])
    print()
    for d in result["documents"]:
        print(f"  {d['file']:<30} {d.get('error') or d['method'] + ' - ' + d.get('summary', '')}")


def _print_table(rows: List[Dict[str, Any]]) -> None:
    columns = list(rows[0])
    cells = [[_cell(row[col]) for col in columns] for row in rows]
    widths = [max(len(col), *(len(r[i]) for r in cells)) for i, col in enumerate(columns)]
    print("  " + "  ".join(col.upper().ljust(w) for col, w in zip(columns, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for row in cells:
        print("  " + "  ".join(cell.ljust(w) for cell, w in zip(row, widths)))


def _cell(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.2f}"
    s = str(v)
    return s if len(s) <= 60 else s[:57] + "..."


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a JE Excel against its supporting documents.")
    parser.add_argument("--je", type=Path, required=True, help="the JE Excel file")
    parser.add_argument("--template", help="JE template name (default: the one that fits the file)")
    parser.add_argument("documents", type=Path, nargs="+", help="supporting documents")
    args = parser.parse_args()

    try:
        result = validate(args.je, args.documents, args.template)
    except (ValueError, anthropic.APIError, ollama.ResponseError) as e:
        raise SystemExit(str(e))

    print_report(result)
    out = args.je.with_name(f"{args.je.stem}_validation.json")
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
