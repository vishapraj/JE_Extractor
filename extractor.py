import argparse
import base64
import io
import json
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple
from xml.etree import ElementTree

import anthropic
import ollama
import pandas as pd
import pypdfium2 as pdfium
from dotenv import load_dotenv
from PIL import Image, ImageSequence, UnidentifiedImageError
from pydantic import BaseModel, ValidationError

load_dotenv(Path(__file__).parent / ".env")

CLAUDE_PLATFORM = os.getenv("CLAUDE_PLATFORM", "anthropic")  # anthropic | vertex | bedrock | local (Ollama)
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "16000"))
PARALLEL_FILES = int(os.getenv("PARALLEL_FILES", "4"))

LOCAL = CLAUDE_PLATFORM == "local"
LOCAL_MODEL = os.getenv("LOCAL_MODEL", "qwen2.5vl:7b")
LOCAL_NUM_CTX = int(os.getenv("LOCAL_NUM_CTX", "16384"))  # Ollama's default context is too small for a document
MAX_IMAGE_SIDE = 1600  # longest side, in pixels, of images this tool converts; bigger only slows a model down
MODEL = LOCAL_MODEL if LOCAL else CLAUDE_MODEL

FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5" ,"Haiku-5.5"}

EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".xlsb"}
TEXT_SUFFIXES = {".csv", ".txt"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp", ".gif": "image/gif"}
# Images Claude can't take as they are: sent as PNG (every page of a TIFF).
CONVERTED_IMAGES = {".tif", ".tiff", ".bmp"}
SUPPORTED = "PDF, Word (.docx), Excel (.xlsx/.xls/.xlsm/.xlsb), CSV, TXT, PNG, JPG, WEBP, GIF, TIFF, BMP"


class JELine(BaseModel):
    line_no: Optional[int]
    account: Optional[str]
    account_description: Optional[str]
    cost_center: Optional[str]
    debit: Optional[float]
    credit: Optional[float]
    tax_code: Optional[str]
    vendor: Optional[str]
    description: Optional[str]
    reference: Optional[str]


class JournalEntry(BaseModel):
    company_code: Optional[str]
    entity_name: Optional[str]
    je_type: Optional[str]
    period: Optional[str]
    posting_date: Optional[str]
    document_date: Optional[str]
    currency: Optional[str]
    reference: Optional[str]
    description: Optional[str]
    prepared_by: Optional[str]
    lines: List[JELine]


class InvoiceLine(BaseModel):
    description: Optional[str]
    quantity: Optional[float]
    unit_price: Optional[float]
    amount: Optional[float]


class Invoice(BaseModel):
    invoice_number: Optional[str]
    vendor_name: Optional[str]
    bill_to: Optional[str]
    invoice_date: Optional[str]
    due_date: Optional[str]
    po_number: Optional[str]
    currency: Optional[str]
    net_amount: Optional[float]
    tax_amount: Optional[float]
    gross_amount: Optional[float]
    line_items: List[InvoiceLine]


class Transaction(BaseModel):
    date: Optional[str]
    description: Optional[str]
    reference: Optional[str]
    debit: Optional[float]
    credit: Optional[float]
    balance: Optional[float]


class BankStatement(BaseModel):
    bank_name: Optional[str]
    account_holder: Optional[str]
    account_number: Optional[str]
    currency: Optional[str]
    period_start: Optional[str]
    period_end: Optional[str]
    opening_balance: Optional[float]
    closing_balance: Optional[float]
    transactions: List[Transaction]


class CalculationRow(BaseModel):
    label: Optional[str]
    amount: Optional[float]


class Calculation(BaseModel):
    title: Optional[str]
    period: Optional[str]
    currency: Optional[str]
    total_amount: Optional[float]
    rows: List[CalculationRow]


class Approval(BaseModel):
    approver: Optional[str]
    approval_date: Optional[str]
    approved_amount: Optional[float]
    currency: Optional[str]
    reference: Optional[str]
    comments: Optional[str]


class Detail(BaseModel):
    label: str
    value: str


class DocumentDetails(BaseModel):
    doc_type: Literal["JOURNAL_ENTRY", "INVOICE", "BANK_STATEMENT", "CALCULATION", "APPROVAL", "OTHER"]
    summary: str
    journal_entry: Optional[JournalEntry]
    invoice: Optional[Invoice]
    bank_statement: Optional[BankStatement]
    calculation: Optional[Calculation]
    approval: Optional[Approval]
    other_details: List[Detail]


def load_content(path: Path) -> List[Dict[str, Any]]:
    """The file as user-message content blocks."""
    suffix = path.suffix.lower()
    label = {"type": "text", "text": f"Document: {path.name}"}

    if suffix == ".pdf":
        return [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": _b64(path)}},
            label,
        ]
    if suffix in IMAGE_TYPES:
        return [
            {"type": "image", "source": {"type": "base64", "media_type": IMAGE_TYPES[suffix], "data": _b64(path)}},
            label,
        ]
    if suffix in CONVERTED_IMAGES:
        pages = [base64.standard_b64encode(png).decode("utf-8") for png in _image_pages(path)]
        return [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": p}}
                for p in pages] + [label]

    return [{"type": "text", "text": f"Document: {path.name}\n\n{_file_text(path)}"}]


def load_local_content(path: Path) -> Tuple[str, List[bytes]]:
    """The file as (text, PNG images) for a local model, which can't read PDFs itself."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pdf = pdfium.PdfDocument(path)
        pages = [pdf[i].get_textpage().get_text_range() for i in range(len(pdf))]
        if all(len(p.strip()) > 20 for p in pages):
            return "\n\n".join(f"[Page {i + 1}]\n{p}" for i, p in enumerate(pages)), []
        # A scan: no text layer, so the model reads the page images.
        return "", [_png(pdf[i].render(scale=2).to_pil()) for i in range(len(pdf))]
    if suffix in IMAGE_TYPES or suffix in CONVERTED_IMAGES:
        return "", _image_pages(path)
    return _file_text(path), []


def _image_pages(path: Path) -> List[bytes]:
    """The image as PNG; a TIFF scan can hold several pages."""
    with Image.open(path) as image:
        frames = ImageSequence.Iterator(image) if image.format == "TIFF" else [image]
        return [_png(frame.copy()) for frame in frames]


def _png(image: Image.Image) -> bytes:
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _file_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in EXCEL_SUFFIXES:
        return _excel_to_text(path)
    if suffix in TEXT_SUFFIXES:
        try:
            return path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            # Saved by Windows tools (e.g. Excel "CSV", Notepad "ANSI"): keeps £ and € intact.
            return path.read_text(encoding="cp1252", errors="replace")
    if suffix == ".docx":
        return _docx_to_text(path)
    if suffix == ".doc":
        raise ValueError("old Word .doc files are not supported - open it in Word and save it as .docx")
    raise ValueError(f"unsupported file type '{suffix}' - supported: {SUPPORTED}")


def _b64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode("utf-8")


def _excel_to_text(path: Path) -> str:
    """Every sheet, pipe-delimited: details are often spread over several sheets."""
    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=str, engine="calamine")
    blocks = []
    for name, df in sheets.items():
        rows = [
            " | ".join("" if pd.isna(v) else str(v) for v in row)
            for row in df.itertuples(index=False)
        ]
        rows = [r for r in rows if r.strip(" |")]
        if rows:
            blocks.append(f"[Sheet: {name}]\n" + "\n".join(rows))
    return "\n\n".join(blocks)


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_to_text(path: Path) -> str:
    """Paragraphs and table rows of word/document.xml, in document order."""
    with zipfile.ZipFile(path) as z:
        body = ElementTree.fromstring(z.read("word/document.xml")).find(f"{_W}body")
    lines = []
    for el in body:
        if el.tag == f"{_W}p":
            lines.append(_xml_text(el))
        elif el.tag == f"{_W}tbl":
            for row in el.iter(f"{_W}tr"):
                lines.append(" | ".join(_xml_text(cell) for cell in row.findall(f"{_W}tc")))
    return "\n".join(line for line in lines if line.strip(" |"))


def _xml_text(el) -> str:
    return "".join(t.text or "" for t in el.iter(f"{_W}t"))


# ---------------------------------------------------------------------------
# Claude extraction
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
        # bedrock-runtime InvokeModel: the same endpoint and AWS credentials as boto3
        return anthropic.AnthropicBedrock(aws_region=os.environ["AWS_REGION"])
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        raise ValueError(
            "No Claude credentials: copy .env.example to .env and set ANTHROPIC_API_KEY, "
            "or set CLAUDE_PLATFORM=vertex / bedrock with that platform's settings."
        )
    return anthropic.Anthropic()


_client = None


def extract_files(paths: List[Path]) -> Dict[str, Any]:
    global _client
    if _client is None:
        _client = make_client()  # before the pool, so missing credentials fail once, not per file
    # A local model runs on this machine's CPU: one file at a time.
    with ThreadPoolExecutor(1 if LOCAL else PARALLEL_FILES) as pool:
        documents = list(pool.map(extract_file, paths))
    return {"model": MODEL, "documents": documents}


def extract_file(path: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {"file": path.name}
    try:
        parsed = _ask_local(path) if LOCAL else _ask_claude(path)
    except (ValueError, anthropic.APIError, ollama.ResponseError, ConnectionError,
            pdfium.PdfiumError, UnidentifiedImageError) as e:
        result["error"] = str(e)
        return result
    section = None if parsed.doc_type == "OTHER" else getattr(parsed, parsed.doc_type.lower())
    result.update(
        doc_type=parsed.doc_type,
        summary=parsed.summary,
        details=section.model_dump() if section else None,
        other_details=[d.model_dump() for d in parsed.other_details],
    )
    return result


def _ask_claude(path: Path) -> DocumentDetails:
    extra: Dict[str, Any] = {}
    if CLAUDE_PLATFORM == "anthropic" and CLAUDE_MODEL in FALLBACK_MODELS:
        # If the model declines the file, the API retries it on a fallback
        # model inside the same call instead of returning nothing.
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    elif CLAUDE_PLATFORM == "bedrock":
        # Bedrock takes the SDK's default API version only for Claude 4.7 and later
        # models (not Sonnet 4.5), so send the version every Bedrock model accepts.
        extra = {"extra_body": {"anthropic_version": "bedrock-2023-05-31"}}

    response = _client.beta.messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        messages=[{"role": "user", "content": load_content(path)}],
        output_format=DocumentDetails,
        **extra,
    )
    if response.stop_reason == "refusal":
        raise ValueError(f"Claude declined to read {path.name}")
    if response.stop_reason == "max_tokens":
        raise ValueError(f"{path.name} is too long for one call - the output was cut off (raise MAX_TOKENS)")
    return response.parsed_output


def _ask_local(path: Path) -> DocumentDetails:
    text, images = load_local_content(path)
    response = _client.chat(
        model=LOCAL_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Document: {path.name}\n\n{text}".strip(), "images": images},
        ],
        format=DocumentDetails.model_json_schema(),  # Ollama constrains the reply to the schema
        options={"temperature": 0, "num_ctx": LOCAL_NUM_CTX},
    )
    if response.done_reason == "length":
        raise ValueError(f"{path.name} is too long for the local model - the output was cut off (raise LOCAL_NUM_CTX)")
    try:
        return DocumentDetails.model_validate_json(response.message.content)
    except ValidationError:
        raise ValueError(f"The local model's reply for {path.name} did not match the expected format - try again") from None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_document(doc: Dict[str, Any]) -> None:
    print(f"\n=== {doc['file']}")
    if doc.get("error"):
        print(f"  ERROR: {doc['error']}")
        return
    print(f"  {'type':<20} {doc['doc_type']}")
    print(f"  {'summary':<20} {doc['summary']}")
    tables = {}
    for key, value in (doc["details"] or {}).items():
        if isinstance(value, list):
            tables[key] = value
        else:
            print(f"  {key.replace('_', ' '):<20} {_cell(value)}")
    for detail in doc["other_details"]:
        print(f"  {detail['label']:<20} {_cell(detail['value'])}")
    for key, rows in tables.items():
        print(f"\n  {key.replace('_', ' ').upper()}")
        _print_table(rows)


def _print_table(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        print("  (none)")
        return
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
    return s if len(s) <= 40 else s[:37] + "..."


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract the key details of a JE or supporting documents with Claude or a local model.")
    parser.add_argument("files", type=Path, nargs="+", help=f"JE and/or supporting documents: {SUPPORTED}")
    args = parser.parse_args()

    try:
        result = extract_files(args.files)
    except (ValueError, anthropic.APIError) as e:
        raise SystemExit(str(e))

    print(f"Read by {result['model']}")
    for path, doc in zip(args.files, result["documents"]):
        print_document(doc)
        out = path.with_name(f"{path.stem}_extracted.json")
        out.write_text(json.dumps({"model": result["model"], **doc}, indent=2), encoding="utf-8")
        print(f"\n  Saved {out}")


if __name__ == "__main__":
    main()
