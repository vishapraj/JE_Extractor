"""JE templates, kept in a SQLite file on this machine (TEMPLATES_DB)."""

import sqlite3
from contextlib import closing
from typing import List

from je_validator.config import TEMPLATES_DB
from je_validator.je.template import JETemplate


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(TEMPLATES_DB)
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
