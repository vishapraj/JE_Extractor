"""
Supporting documents as text. Each format is read by its own module, chosen by
the file's suffix; only scanned PDF pages and images go through OCR (je_validator/ocr).
Add a format by writing a module with a read(path) -> (text, methods) and listing it here.
"""

from pathlib import Path
from typing import Any, Dict

from je_validator import limits
from je_validator.documents import excel, image, pdf, text, word
from je_validator.markers import READER_HEADER  # noqa: F401 - re-exported for the other packages

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
    """{file, text, method[, pages, page_images]}: the document's text, what read it, and for PDFs and
    images each page's size and zones (label, box, reading order) and its image, for display."""
    suffix = path.suffix.lower()
    if suffix == ".doc":
        raise ValueError("old Word .doc files are not supported - open it in Word and save it as .docx")
    if suffix not in READERS:
        raise ValueError(f"unsupported file type '{suffix}' - supported: {SUPPORTED}")
    limits.check_file(path)
    if suffix in ZIP_SUFFIXES:
        limits.check_zip(path)
    text, methods, *layout = READERS[suffix](path)  # PDFs and images also return their pages' zones
    document = {"file": path.name, "text": text, "method": ", ".join(dict.fromkeys(methods))}
    if layout:
        # The page images (PIL) are kept apart from the rest: they're for the viewer only, never stored
        # in a result or given to a model.
        document["page_images"] = [p.pop("image") for p in layout[0]]
        document["pages"] = layout[0]
    return document
