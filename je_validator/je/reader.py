"""Reads a JE Excel's header, lines and totals with a template from the store."""

import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from je_validator import limits
from je_validator.je.store import get_template, list_templates
from je_validator.je.template import JETemplate

EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".xlsb"}
HEADER_SEARCH_ROWS = 50  # the line column headers must be within the first 50 rows of a sheet


def read_je(path: Path, template_name: Optional[str] = None) -> Dict[str, Any]:
    """The JE's header and lines, read with the named template or the first that fits."""
    if path.suffix.lower() not in EXCEL_SUFFIXES:
        raise ValueError(f"the JE must be an Excel file (.xlsx, .xls, .xlsm, .xlsb), not '{path.suffix}'")
    limits.check_file(path)
    if path.suffix.lower() != ".xls":  # the old .xls format isn't a zip archive
        limits.check_zip(path)
    try:
        sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=str, engine="calamine")
    except PermissionError:
        raise ValueError(f"{path.name} can't be opened - it is probably open in Excel; close it and try again") from None

    templates = [get_template(template_name)] if template_name else list_templates()
    if not templates:
        raise ValueError("there are no JE templates in the database - add one: "
                         "python -m je_validator templates add sample_templates/standard.json")
    # The template that finds the most of its columns wins.
    matches = [(t, found) for t in templates if (found := _find_table(t, sheets))]
    if not matches:
        tried = ", ".join(t.name for t in templates)
        raise ValueError(f"{path.name} doesn't match any JE template ({tried}): no sheet has their account "
                         "and amount column headers. Add a template for this layout.")
    template, (sheet, header_row, cols) = max(matches, key=lambda m: len(m[1][2]))
    df = sheets[sheet]

    lines = []
    for r in range(header_row + 1, len(df)):
        values = {field: _clean(df.iat[r, i]) for field, i in cols.items()}
        if not values.get("account"):
            continue  # blank, total or note rows
        debit, credit = _amounts(template, values, r + 1)
        if debit is None and credit is None:
            continue
        line = {"line": len(lines) + 1, "row": r + 1}
        line.update({f: v for f, v in values.items() if f not in ("debit", "credit", "amount", "dc_indicator")})
        line.update(debit=debit, credit=credit)
        lines.append(line)
    if not lines:
        raise ValueError(f"{path.name}: template '{template.name}' found the columns on sheet '{sheet}' but no lines below them")

    header_cells = [_norm(v) for v in df.iloc[header_row]]
    header = {}
    for field, src in template.header.items():
        if src.column:
            i = header_cells.index(_norm(src.column)) if _norm(src.column) in header_cells else None
            header[field] = None if i is None else _clean(df.iat[lines[0]["row"] - 1, i])
        else:
            src_df = sheets.get(src.sheet or sheet)
            if src_df is None:
                header[field] = None
            else:
                header[field] = _cell_value(src_df, src.cell) if src.cell else _label_value(src_df, src.label)

    return {
        "file": path.name,
        "template": template.name,
        "sheet": sheet,
        "header": header,
        "totals": {"debit": round(sum(l["debit"] or 0 for l in lines), 2),
                   "credit": round(sum(l["credit"] or 0 for l in lines), 2)},
        "lines": lines,
    }


def _find_table(template: JETemplate, sheets: Dict[str, pd.DataFrame]) -> Optional[Tuple[str, int, Dict[str, int]]]:
    """(sheet, header row, field -> column index) of the first row with the template's key columns."""
    for name in [template.sheet] if template.sheet else list(sheets):
        df = sheets.get(name)
        if df is None:
            continue
        for r in range(min(len(df), HEADER_SEARCH_ROWS)):
            cells = [_norm(v) for v in df.iloc[r]]
            cols = {}
            for field, headers in template.columns.items():
                wanted = {_norm(h) for h in headers}
                i = next((i for i, c in enumerate(cells) if c in wanted), None)
                if i is not None:
                    cols[field] = i
            if template.key_fields() <= cols.keys():
                return name, r, cols
    return None


def _amounts(template: JETemplate, values: Dict[str, Any], excel_row: int) -> Tuple[Optional[float], Optional[float]]:
    if "amount" not in template.columns:
        return _number(values.get("debit"), excel_row), _number(values.get("credit"), excel_row)
    amount = _number(values.get("amount"), excel_row)
    if amount is None:
        return None, None
    if "dc_indicator" in template.columns:
        indicators = {_norm(d) for d in template.debit_indicators}
        is_debit = _norm(values.get("dc_indicator")) in indicators
    else:
        is_debit = amount >= 0
    return (abs(amount), None) if is_debit else (None, abs(amount))


def _number(value: Optional[str], excel_row: int) -> Optional[float]:
    """1,234.50 / (500.00) / 500.00- / €1,000 -> a float."""
    if value is None:
        return None
    s = value.replace(",", "").replace(" ", "")
    negative = (s.startswith("(") and s.endswith(")")) or s.endswith("-")
    s = re.sub(r"[^\d.\-]", "", s.strip("()").rstrip("-"))
    if s in ("", "-", "."):
        return None
    try:
        n = float(s)
    except ValueError:
        raise ValueError(f"row {excel_row}: amount '{value}' is not a number") from None
    return -abs(n) if negative else n


def _cell_value(df: pd.DataFrame, ref: str) -> Optional[str]:
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", ref.strip())
    if not m:
        raise ValueError(f"'{ref}' is not a cell reference like B2")
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + ord(ch) - ord("A") + 1
    row, col = int(m.group(2)) - 1, col - 1
    return _clean(df.iat[row, col]) if row < len(df) and col < len(df.columns) else None


def _label_value(df: pd.DataFrame, label: str) -> Optional[str]:
    wanted = _norm(label).rstrip(":").strip()
    for r in range(len(df)):
        for c in range(len(df.columns)):
            cell = _norm(df.iat[r, c])
            if cell.startswith(wanted + ":") and cell.rstrip(":").strip() != wanted:
                # "Company Code: 1000" in one cell
                return _clean(str(df.iat[r, c]).split(":", 1)[1])
            if cell.rstrip(":").strip() == wanted:
                right = [_clean(v) for v in df.iloc[r, c + 1:]]
                below = _clean(df.iat[r + 1, c]) if r + 1 < len(df) else None
                return next((v for v in right if v), below)
    return None


def _norm(value: Any) -> str:
    return "" if value is None or pd.isna(value) else " ".join(str(value).split()).casefold()


def _clean(value: Any) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    s = str(value).strip()
    if s.endswith(" 00:00:00"):  # Excel dates come back as date-times
        s = s[:-9]
    return s or None
