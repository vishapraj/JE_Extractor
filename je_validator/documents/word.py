from pathlib import Path
from typing import List, Tuple

import docx
from docx.table import Table

from je_validator.documents import pictures


def read(path: Path) -> Tuple[str, List[str]]:
    """Paragraphs and table rows, in document order, then the text of its pictures (OCR)."""
    lines: List[str] = []
    for block in docx.Document(str(path)).iter_inner_content():
        if isinstance(block, Table):
            lines.extend(" | ".join(cell.text for cell in row.cells) for row in block.rows)
        else:
            lines.append(block.text)
    text = "\n".join(line for line in lines if line.strip(" |"))
    blocks, methods = pictures.office_pictures(path, "word/media/")
    return "\n\n".join([text, *blocks]), ["python-docx", *methods]
