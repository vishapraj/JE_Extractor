from pathlib import Path
from typing import List, Tuple

SUFFIXES = {".csv", ".txt"}


def read(path: Path) -> Tuple[str, List[str]]:
    try:
        return path.read_text(encoding="utf-8-sig"), ["text"]
    except UnicodeDecodeError:
        # Saved by Windows tools (e.g. Excel "CSV", Notepad "ANSI"): keeps £ and € intact.
        return path.read_text(encoding="cp1252", errors="replace"), ["text"]
