"""What the model is told: the instructions, then the tagged documents and the JE."""

import json
from typing import Any, Dict, List

from je_validator.validation.amounts import tag_amounts

SYSTEM = """\
You check a journal entry (JE) against its supporting documents for an accounting reviewer.

You get the text of each supporting document, then the JE as JSON: its header, its lines (each with a \
"line" number) and its debit and credit totals. The document text was taken from the files by software; \
text from scans and photos came from OCR and may contain misread characters. Personal data has been replaced \
by placeholders such as [PERSON-A] or [EMAIL-A]: the same placeholder always stands for the same value, and a \
placeholder counts as that value being present (an approval naming [PERSON-A] names an approver). Lines that \
held nothing the checks need were removed.

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
   - REFERENCE: reference numbers (invoice number, PO, JE reference) in the JE agree with the documents.
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


def build_prompt(je: Dict[str, Any], documents: List[Dict[str, Any]]) -> str:
    tagged = tag_amounts(documents)[0]
    parts = [f'<document name="{d["file"]}" read_by="{d["method"]}">\n{tagged[d["file"]]}\n</document>'
             for d in documents]
    je_view = {k: je[k] for k in ("header", "totals", "lines")}
    parts.append(f"<journal_entry>\n{json.dumps(je_view, indent=1)}\n</journal_entry>")
    parts.append("Check the journal entry against the supporting documents.")
    return "\n\n".join(parts)
