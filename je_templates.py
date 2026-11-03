"""
JE Excel templates, kept in a SQLite database. Each template says where one
layout of JE Excel keeps its header details and its line columns, so teams with
different layouts can all be read without an LLM.

    python je_templates.py list
    python je_templates.py add sample_templates/standard.json
    python je_templates.py show standard
    python je_templates.py remove standard
"""

import argparse
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel, field_validator, model_validator

load_dotenv(Path(__file__).parent / ".env")

DB_PATH = Path(os.getenv("TEMPLATES_DB", Path(__file__).parent / "templates.db"))

LINE_FIELDS = {"line_no", "account", "account_description", "cost_center", "debit", "credit", "amount",
               "dc_indicator", "currency", "tax_code", "vendor", "description", "reference", "document_date"}
HEADER_FIELDS = {"je_number", "company_code", "entity_name", "je_type", "period", "posting_date",
                 "document_date", "currency", "reference", "description", "prepared_by", "approved_by"}
EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".xlsb"}
HEADER_SEARCH_ROWS = 50  # the line column headers must be within the first 50 rows of a sheet


class HeaderSource(BaseModel):
    """Where one JE header detail is. Give exactly one of cell, label or column."""
    cell: Optional[str] = None    # a fixed cell, e.g. "B2"
    label: Optional[str] = None   # a cell with this text; the value is next to it on the right (or below it)
    column: Optional[str] = None  # a line column; the value on the first line
    sheet: Optional[str] = None   # for cell/label: another sheet than the lines' one

    @model_validator(mode="after")
    def _one_source(self):
        if sum(v is not None for v in (self.cell, self.label, self.column)) != 1:
            raise ValueError("give exactly one of cell, label or column")
        return self


class JETemplate(BaseModel):
    name: str
    description: str = ""
    sheet: Optional[str] = None       # None: the first sheet that has the line columns
    columns: Dict[str, List[str]]     # line field -> its header text(s) in the file, ignoring case and spaces
    header: Dict[str, HeaderSource] = {}
    # With an amount column: dc_indicator values that mean debit. Without a dc_indicator
    # column, a positive amount is a debit and a negative one a credit.
    debit_indicators: List[str] = ["D", "DR", "DEBIT", "S", "40"]

    @field_validator("columns", mode="before")
    @classmethod
    def _listify(cls, columns):
        return {field: [h] if isinstance(h, str) else h for field, h in columns.items()}

    @model_validator(mode="after")
    def _check_fields(self):
        if unknown := set(self.columns) - LINE_FIELDS:
            raise ValueError(f"unknown line fields {sorted(unknown)} - allowed: {sorted(LINE_FIELDS)}")
        if unknown := set(self.header) - HEADER_FIELDS:
            raise ValueError(f"unknown header fields {sorted(unknown)} - allowed: {sorted(HEADER_FIELDS)}")
        if "account" not in self.columns:
            raise ValueError("columns needs account")
        if "amount" not in self.columns and not {"debit", "credit"} <= self.columns.keys():
            raise ValueError("columns needs either amount, or both debit and credit")
        return self

    def key_fields(self) -> set:
        """The columns a sheet must have for this template to match it."""
        return {"account"} | ({"amount"} if "amount" in self.columns else {"debit", "credit"})


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS je_templates (name TEXT PRIMARY KEY, definition TEXT NOT NULL)")
    return con


def list_templates() -> List[JETemplate]:
    with closing(_db()) as con:
        rows = con.execute("SELECT definition FROM je_templates ORDER BY name").fetchall()
    return [JETemplate.model_validate_json(r[0]) for r in rows]


def get_template(name: str) -> JETemplate:
    with closing(_db()) as con:
        row = con.execute("SELECT definition FROM je_templates WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise ValueError(f"no JE template named '{name}'")
    return JETemplate.model_validate_json(row[0])


def save_template(template: JETemplate) -> None:
    with closing(_db()) as con, con:
        con.execute("INSERT OR REPLACE INTO je_templates VALUES (?, ?)", (template.name, template.model_dump_json()))


def delete_template(name: str) -> bool:
    with closing(_db()) as con, con:
        return con.execute("DELETE FROM je_templates WHERE name = ?", (name,)).rowcount > 0


# ---------------------------------------------------------------------------
# Reading a JE Excel
# ---------------------------------------------------------------------------

def read_je(path: Path, template_name: Optional[str] = None) -> Dict[str, Any]:
    """The JE's header and lines, read with the named template or the first that fits."""
    if path.suffix.lower() not in EXCEL_SUFFIXES:
        raise ValueError(f"the JE must be an Excel file (.xlsx, .xls, .xlsm, .xlsb), not '{path.suffix}'")
    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=str, engine="calamine")

    templates = [get_template(template_name)] if template_name else list_templates()
    if not templates:
        raise ValueError("there are no JE templates in the database - add one: "
                         "python je_templates.py add sample_templates/standard.json")
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the JE Excel templates in the database.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list the templates")
    sub.add_parser("show", help="print a template as JSON").add_argument("name")
    sub.add_parser("add", help="add or replace templates from JSON files").add_argument("files", type=Path, nargs="+")
    sub.add_parser("remove", help="delete a template").add_argument("name")
    args = parser.parse_args()

    try:
        if args.command == "list":
            for t in list_templates():
                print(f"{t.name:<24} {t.description}")
        elif args.command == "show":
            print(get_template(args.name).model_dump_json(indent=2, exclude_none=True))
        elif args.command == "add":
            for f in args.files:
                template = JETemplate.model_validate(json.loads(f.read_text(encoding="utf-8")))
                save_template(template)
                print(f"Saved {template.name}")
        elif args.command == "remove":
            print("Removed" if delete_template(args.name) else f"No template named {args.name}")
    except ValueError as e:  # includes pydantic's ValidationError
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
