from pathlib import Path
from typing import List, Tuple

import pandas as pd

from je_validator.documents import pictures

SUFFIXES = {".xlsx", ".xls", ".xlsm", ".xlsb"}


def read(path: Path) -> Tuple[str, List[str]]:
    """Every sheet, pipe-delimited: details are often spread over several sheets. Then the text of
    pictures placed in the workbook (OCR); the old .xls format's pictures aren't read."""
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
    methods = ["pandas"]
    if path.suffix.lower() != ".xls":  # .xlsx/.xlsm/.xlsb are zip files with an xl/media folder
        picture_blocks, picture_methods = pictures.office_pictures(path, "xl/media/")
        blocks += picture_blocks
        methods += picture_methods
    return "\n\n".join(blocks), methods
