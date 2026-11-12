"""
Validates a journal entry (JE) Excel against its supporting documents.

The JE is read with a template from the database (je_templates.py) and the
documents by their own libraries or OCR (readers.py); no LLM reads files.
Claude Haiku (or a local model) then compares the JE with the documents'
text, and the arithmetic is checked again in code. Every match and rule gets a
confidence score; when the review's score is below CONFIDENCE_THRESHOLD, Claude
Sonnet reviews it again, and if Sonnet's score is low too, the result is
flagged for human review.

    python validator.py --je JE.xlsx invoice.pdf bank.pdf approval.docx
"""

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import anthropic
import ollama
import truststore
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from je_templates import read_je
from readers import read_document

load_dotenv(Path(__file__).parent / ".env")
# Trust the certificates Windows (or macOS/Linux) trusts, not only Python's own list: behind a
# company proxy that inspects HTTPS, model downloads and the Claude API fail otherwise.
truststore.inject_into_ssl()

CLAUDE_PLATFORM = os.getenv("CLAUDE_PLATFORM", "anthropic")  # anthropic | vertex | bedrock | local (Ollama)
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL") or os.getenv("BEDROCK_MODEL_ID") or (
    "anthropic.claude-haiku-5-5" if CLAUDE_PLATFORM == "bedrock" else "claude-haiku-5-5")
# A Bedrock ARN (e.g. an inference profile) names one model your account was given, so there is
# no default escalation model for it: set ESCALATION_MODEL to a second ARN, or low confidence
# goes straight to human review.
ESCALATION_MODEL = os.getenv("ESCALATION_MODEL") or (
    None if CLAUDE_MODEL.startswith("arn:")
    else "anthropic.claude-sonnet-5-5" if CLAUDE_PLATFORM == "bedrock" else "claude-sonnet-5-5")
CONFIDENCE_THRESHOLD = int(os.getenv("CONFIDENCE_THRESHOLD", "80"))  # 0-100; below this, escalate
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "16000"))
AMOUNT_TOLERANCE = float(os.getenv("AMOUNT_TOLERANCE", "0.01"))

LOCAL = CLAUDE_PLATFORM == "local"
LOCAL_MODEL = os.getenv("LOCAL_MODEL", "qwen2.5:7b")
LOCAL_NUM_CTX = int(os.getenv("LOCAL_NUM_CTX", "32768"))  # Ollama's default context is too small for documents
# Tried in order until one is confident enough; after the last, a human reviews it.
LOCAL_ESCALATION_MODEL = os.getenv("LOCAL_ESCALATION_MODEL")  # optional, e.g. qwen2.5:14b
MODELS = ([LOCAL_MODEL] + ([LOCAL_ESCALATION_MODEL] if LOCAL_ESCALATION_MODEL else [])) if LOCAL \
    else [CLAUDE_MODEL] + ([ESCALATION_MODEL] if ESCALATION_MODEL else [])
# Claude API models that take server-side refusal fallback (Haiku 5.5 does not).
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}

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
    amount_ids: List[int]  # the [#n] tags of the document amounts it picked; code looks up their values
    evidence: Optional[str]
    issues: List[str]
    confidence: int


class RuleResult(BaseModel):
    rule: Literal[MODEL_RULES]
    status: Status
    details: str
    confidence: int


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
2. lines: one entry for every JE line. In the document text, every amount is followed by a tag such as \
[#12]. amount_ids lists the tags of the document amounts that belong to the line. Usually that is exactly \
one tag: the amount for the same item. List several only when the JE line books several separate document \
amounts together, and never add a total or subtotal (Net, Total, Gross) to the item amounts it already \
contains: a line for one invoice item gets that item's amount, a line for the whole invoice gets its total. \
Choose amounts by what they are for (description, vendor, reference, period), not by whether they equal \
the JE amount: if the invoice's cloud hosting amount differs from the JE's cloud hosting line, still pick \
it, and the status is MISMATCH. Never write amounts yourself; only use tags that are in the documents. \
status is SUPPORTED when the amounts agree, MISMATCH when they differ, and NOT_FOUND when no document has \
an amount for the line (then amount_ids is empty). evidence is one sentence on why those amounts belong to \
the line. List any other differences for the line (vendor, reference, date) in issues.
3. rules: one result for each of
   - VENDOR_REFERENCE: vendor names and reference numbers (invoice, PO, JE reference) in the JE agree with the documents.
   - DATES_IN_PERIOD: document dates (invoice date, service period, transaction dates) fall within the JE \
period, or the JE description explains why not (for example an accrual or a prepayment).
   - APPROVAL: an approval document exists, names the approver, and the approved amount covers the JE total.
   - CURRENCY: the documents' currency matches the JE currency.
   status is PASS when the documents show the rule holds, FAIL when they show it doesn't, and \
CANNOT_VERIFY when the documents don't contain what is needed.
4. confidence, on every line and rule: 0 to 100, how sure you are that its status is right. Give 90 or \
more only when the evidence is explicit and unambiguous, such as an exact amount and reference in clearly \
read text. Give less for OCR text that may be misread, matches you had to sum or infer, documents that may \
belong to another transaction, or values you couldn't find clearly. A NOT_FOUND or CANNOT_VERIFY can have \
high confidence when the documents plainly don't contain the value.
5. summary: two or three sentences for the reviewer.

Use only what the JE and the documents show. Never guess or fill in a value that isn't there; say what is \
missing instead. Quote the document text you rely on in details."""


def validate(je_path: Path, doc_paths: List[Path], template: Optional[str] = None) -> Dict[str, Any]:
    je = read_je(je_path, template)
    documents = [_read(p) for p in doc_paths]
    readable = [d for d in documents if "error" not in d]
    attempts = []
    if not readable:
        result = _assess(je, documents, Review(documents=[], lines=[], rules=[],
                                               summary="None of the supporting documents could be read."), "none")
    else:
        for model in MODELS:
            review, answered_by = _ask_model(model, je, readable)
            result = _assess(je, documents, review, answered_by)
            attempts.append({"model": answered_by, "confidence": result["confidence"]})
            if result["confidence"] >= CONFIDENCE_THRESHOLD:
                break
    result.update(attempts=attempts, confidence_threshold=CONFIDENCE_THRESHOLD,
                  human_review=result["confidence"] < CONFIDENCE_THRESHOLD)
    return result


def _assess(je: Dict[str, Any], documents: List[Dict[str, Any]], review: Review, model: str) -> Dict[str, Any]:
    """One model's review, checked in code. Its confidence is that of its least confident item."""
    reviewed = {d.file: d for d in review.documents}
    for d in documents:
        if "error" not in d:  # replaces an earlier model's summary, if any
            r = reviewed.get(d["file"])
            d.update(doc_type=r.doc_type if r else None, summary=r.summary if r else None)

    lines = _check_lines(je["lines"], review.lines, _tag_amounts([d for d in documents if "error" not in d])[1])
    checks = [_balanced(je["totals"])]
    if unread := [d["file"] for d in documents if "error" in d]:
        checks.append(_check("DOCUMENTS_READ", "CANNOT_VERIFY", "Could not read: " + ", ".join(unread), "code"))
    checks.append(_amounts(lines, model))
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


def _read(path: Path) -> Dict[str, Any]:
    try:
        return read_document(path)
    except Exception as e:  # one unreadable file must not stop the others
        return {"file": path.name, "error": str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}"}


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


_NUMBER = re.compile(r"\d[\d,.]*")
_EUROPEAN = re.compile(r"\d{1,3}(\.\d{3})+(,\d+)?")  # 1.234,50


def _tag_amounts(documents: List[Dict[str, Any]]) -> Tuple[Dict[str, str], Dict[int, Dict[str, Any]]]:
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
            out.append(text_line if text_line.startswith("[") else _NUMBER.sub(tag, text_line))
        tagged[d["file"]] = "\n".join(out)
    return tagged, amounts


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


# ---------------------------------------------------------------------------
# Model call
# ---------------------------------------------------------------------------

def make_client():
    if LOCAL:
        client = ollama.Client()  # http://127.0.0.1:11434, or OLLAMA_HOST
        for model in MODELS:
            try:
                client.show(model)
            except ConnectionError:
                raise ValueError("Ollama is not running: install it from ollama.com and start it.") from None
            except ollama.ResponseError:
                raise ValueError(f"Local model {model} is not downloaded: run  ollama pull {model}") from None
        return client
    if CLAUDE_PLATFORM == "vertex":
        return anthropic.AnthropicVertex(
            project_id=os.environ["VERTEX_PROJECT_ID"], region=os.getenv("VERTEX_REGION", "global")
        )
    if CLAUDE_PLATFORM == "bedrock":
        if CLAUDE_MODEL.startswith("arn:"):
            # ARNs are served by Bedrock's InvokeModel endpoint, in the ARN's own region
            # (arn:aws:bedrock:<region>:<account>:...).
            return anthropic.AnthropicBedrock(aws_region=os.getenv("AWS_REGION") or CLAUDE_MODEL.split(":")[3])
        return anthropic.AnthropicBedrockMantle(aws_region=os.environ["AWS_REGION"])
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        raise ValueError(
            "No Claude credentials: copy .env.example to .env and set ANTHROPIC_API_KEY, "
            "or set CLAUDE_PLATFORM=vertex / bedrock / local with that platform's settings."
        )
    return anthropic.Anthropic()


_client = None


def _ask_model(model: str, je: Dict[str, Any], documents: List[Dict[str, Any]]) -> Tuple[Review, str]:
    """(review, the model that answered)."""
    global _client
    if _client is None:
        _client = make_client()
    prompt = _prompt(je, documents)

    if LOCAL:
        response = _client.chat(
            model=model,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            format=Review.model_json_schema(),  # Ollama constrains the reply to the schema
            options={"temperature": 0, "num_ctx": LOCAL_NUM_CTX},
        )
        if response.done_reason == "length":
            raise ValueError("The documents are too long for the local model - the reply was cut off (raise LOCAL_NUM_CTX)")
        try:
            return Review.model_validate_json(response.message.content), model
        except ValidationError:
            raise ValueError("The local model's reply did not match the expected format - try again") from None

    extra: Dict[str, Any] = {}
    if CLAUDE_PLATFORM == "anthropic" and model in FALLBACK_MODELS:
        # If the model declines, the API retries on a fallback model inside the same call.
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    if model.startswith("arn:"):
        # Bedrock accepts the SDK's default API version (2026-10-21) only for Claude 4.7 and
        # later; the older Bedrock version works for every model, e.g. Sonnet 4.5 profiles.
        extra = {"extra_body": {"anthropic_version": "bedrock-2023-05-31"}}
    response = _client.beta.messages.parse(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=Review,
        **extra,
    )
    if response.stop_reason == "refusal":
        raise ValueError(f"{model} declined to review these documents")
    if response.stop_reason == "max_tokens":
        raise ValueError(f"The review by {model} was cut off - raise MAX_TOKENS")
    return response.parsed_output, response.model


def _prompt(je: Dict[str, Any], documents: List[Dict[str, Any]]) -> str:
    tagged = _tag_amounts(documents)[0]
    parts = [f'<document name="{d["file"]}" read_by="{d["method"]}">\n{tagged[d["file"]]}\n</document>'
             for d in documents]
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
    tried = " -> ".join(f"{a['model']} {a['confidence']}" for a in result["attempts"]) or "no model"
    print(f"\nOVERALL: {result['overall']}   CONFIDENCE: {result['confidence']} "
          f"({tried}; threshold {result['confidence_threshold']})")
    if result["human_review"]:
        print("  >> HUMAN REVIEW NEEDED: the review's confidence stayed below the threshold")
    print(f"  {result['summary']}\n")
    _print_table([{"check": c["name"], "status": c["status"], "conf": c["confidence"], "details": c["details"]}
                  for c in result["checks"]])
    print()
    matches = {r["line"]: r for r in result["lines"]}
    _print_table([{"line": l["line"], "account": l.get("account"), "debit": l["debit"], "credit": l["credit"],
                   "status": matches[l["line"]]["status"], "conf": matches[l["line"]]["confidence"],
                   "document": matches[l["line"]]["document"],
                   "issues": "; ".join(matches[l["line"]]["issues"])} for l in je["lines"]])
    print()
    for d in result["documents"]:
        print(f"  {d['file']:<30} {d.get('error') or d['method'] + ' - ' + (d.get('summary') or '')}")


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
