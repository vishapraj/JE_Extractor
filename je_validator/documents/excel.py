from pathlib import Path
from typing import List, Tuple

import pandas as pd

SUFFIXES = {".xlsx", ".xls", ".xlsm", ".xlsb"}


def read(path: Path) -> Tuple[str, List[str]]:
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
    return "\n\n".join(blocks), ["pandas"]
