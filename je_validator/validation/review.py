"""The checks, and the review a model must return (as a schema every provider enforces)."""

from typing import List, Literal, Optional

from pydantic import BaseModel

RULES = {
    "BALANCED": "Debits equal credits",
    "DOCUMENTS_READ": "Every document read",
    "AMOUNTS": "Line amounts match the documents",
    "VENDOR": "Vendors named in the documents",
    "REFERENCE": "References found in the documents",
    "CURRENCY": "Currency consistent",
    "DATES_IN_PERIOD": "Document dates in the JE period",
    "APPROVAL": "Approval covers the JE total",
}
MODEL_RULES = ("DATES_IN_PERIOD", "APPROVAL")  # judged by the model; the others are checked in code
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
