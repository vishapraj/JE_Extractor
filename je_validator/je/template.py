"""
A JE template says where one layout of JE Excel keeps its header details and
its line columns, so teams with different layouts can all be read without an LLM.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, field_validator, model_validator

LINE_FIELDS = {"line_no", "account", "account_description", "cost_center", "debit", "credit", "amount",
               "dc_indicator", "currency", "tax_code", "vendor", "description", "reference", "document_date"}
HEADER_FIELDS = {"je_number", "company_code", "entity_name", "je_type", "period", "posting_date",
                 "document_date", "currency", "reference", "description", "prepared_by", "approved_by"}


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
