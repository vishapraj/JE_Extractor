"""
Supporting documents as text. Each format is read by its own library; only
scanned PDF pages and images go through OCR (see ocr.py).
"""

from pathlib import Path
from typing import Any, Dict, List

import docx
import pandas as pd
import pdfplumber
from docx.table import Table
from PIL import Image, ImageSequence

import ocr

EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".xlsb"}
TEXT_SUFFIXES = {".csv", ".txt"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp"}
SUPPORTED = "PDF, Word (.docx), Excel (.xlsx/.xls/.xlsm/.xlsb), CSV, TXT, PNG, JPG, WEBP, GIF, TIFF, BMP"
MIN_PAGE_TEXT = 20  # a PDF page with less text than this is treated as a scan
PDF_RENDER_DPI = 200


def read_document(path: Path) -> Dict[str, Any]:
    """{file, text, method}: the document's text and what read it."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text, methods = _pdf(path)
    elif suffix in IMAGE_SUFFIXES:
        text, methods = _image(path)
    elif suffix in EXCEL_SUFFIXES:
        text, methods = _excel_to_text(path), ["pandas"]
    elif suffix in TEXT_SUFFIXES:
        text, methods = _read_text(path), ["text"]
    elif suffix == ".docx":
        text, methods = _docx_to_text(path), ["python-docx"]
    elif suffix == ".doc":
        raise ValueError("old Word .doc files are not supported - open it in Word and save it as .docx")
    else:
        raise ValueError(f"unsupported file type '{suffix}' - supported: {SUPPORTED}")
    return {"file": path.name, "text": text, "method": ", ".join(dict.fromkeys(methods))}


def _pdf(path: Path):
    """Page by page: the text layer where there is one, OCR where the page is a scan."""
    pages, methods = [], []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if len(text.strip()) >= MIN_PAGE_TEXT:
                method = "text layer"
                for t, table in enumerate(page.extract_tables(), 1):
                    rows = [" | ".join(cell or "" for cell in row) for row in table]
                    text += f"\n\n[Table {t}]\n" + "\n".join(rows)
            else:
                text, method = ocr.read_image(page.to_image(resolution=PDF_RENDER_DPI).original)
            pages.append(f"[Page {i} - {method}]\n{text}")
            methods.append(method)
    return "\n\n".join(pages), methods


def _image(path: Path):
    """Every page of a multi-page TIFF; the first frame of anything else."""
    pages, methods = [], []
    with Image.open(path) as image:
        frames = list(ImageSequence.Iterator(image)) if image.format == "TIFF" else [image]
        for i, frame in enumerate(frames, 1):
            text, method = ocr.read_image(frame.copy())
            pages.append(f"[Page {i} - {method}]\n{text}" if len(frames) > 1 else text)
            methods.append(method)
    return "\n\n".join(pages), methods


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        # Saved by Windows tools (e.g. Excel "CSV", Notepad "ANSI"): keeps £ and € intact.
        return path.read_text(encoding="cp1252", errors="replace")


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


def _docx_to_text(path: Path) -> str:
    """Paragraphs and table rows, in document order."""
    lines: List[str] = []
    for block in docx.Document(str(path)).iter_inner_content():
        if isinstance(block, Table):
            lines.extend(" | ".join(cell.text for cell in row.cells) for row in block.rows)
        else:
            lines.append(block.text)
    return "\n".join(line for line in lines if line.strip(" |"))
