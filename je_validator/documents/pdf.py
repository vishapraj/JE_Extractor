from pathlib import Path
from typing import List, Tuple

import pdfplumber

from je_validator import limits, ocr

MIN_PAGE_TEXT = 20  # a PDF page with less text than this is treated as a scan
PDF_RENDER_DPI = 200


def read(path: Path) -> Tuple[str, List[str]]:
    """Page by page: the text layer where there is one, OCR where the page is a scan."""
    pages, methods = [], []
    with pdfplumber.open(path) as pdf:
        limits.check_pages(len(pdf.pages), path.name)
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if len(text.strip()) >= MIN_PAGE_TEXT:
                method = "text layer"
                for t, table in enumerate(page.extract_tables(), 1):
                    rows = [" | ".join(cell or "" for cell in row) for row in table]
                    text += f"\n\n[Table {t}]\n" + "\n".join(rows)
            else:
                scale = PDF_RENDER_DPI / 72  # PDF sizes are in points
                limits.check_pixels(page.width * scale, page.height * scale, path.name)
                text, method = ocr.read_image(page.to_image(resolution=PDF_RENDER_DPI).original)
            pages.append(f"[Page {i} - {method}]\n{text}")
            methods.append(method)
    return "\n\n".join(pages), methods
