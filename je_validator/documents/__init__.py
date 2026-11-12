"""
Supporting documents as text. Each format is read by its own module, chosen by
the file's suffix; only scanned PDF pages and images go through OCR (je_validator/ocr).
Add a format by writing a module with a read(path) -> (text, methods) and listing it here.
"""

import re
from pathlib import Path
from typing import Any, Dict

from je_validator import limits
from je_validator.documents import excel, image, pdf, text, word

# The lines the readers add to the text: "[Page 1 - text layer]", "[Table 2]", "[Sheet: JE]".
READER_HEADER = re.compile(r"\[(?:Page \d+ - |Table \d+\]|Sheet: )")
ZIP_SUFFIXES = {".xlsx", ".xlsm", ".xlsb", ".docx"}

READERS = {
    ".pdf": pdf.read,
    **dict.fromkeys(image.SUFFIXES, image.read),
    **dict.fromkeys(excel.SUFFIXES, excel.read),
    **dict.fromkeys(text.SUFFIXES, text.read),
    ".docx": word.read,
}
SUPPORTED = "PDF, Word (.docx), Excel (.xlsx/.xls/.xlsm/.xlsb), CSV, TXT, PNG, JPG, WEBP, GIF, TIFF, BMP"


def read_document(path: Path) -> Dict[str, Any]:
    """{file, text, method}: the document's text and what read it."""
    suffix = path.suffix.lower()
    if suffix == ".doc":
        raise ValueError("old Word .doc files are not supported - open it in Word and save it as .docx")
    if suffix not in READERS:
        raise ValueError(f"unsupported file type '{suffix}' - supported: {SUPPORTED}")
    limits.check_file(path)
    if suffix in ZIP_SUFFIXES:
        limits.check_zip(path)
    text, methods = READERS[suffix](path)
    return {"file": path.name, "text": text, "method": ", ".join(dict.fromkeys(methods))}
