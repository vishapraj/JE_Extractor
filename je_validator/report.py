"""A validation result as text tables, for the command line."""

from typing import Any, Dict, List


def print_report(result: Dict[str, Any]) -> None:
    je = result["journal_entry"]
    print(f"{je['file']}  (template {je['template']}, sheet {je['sheet']})  checked by {result['model']}")
    tried = " -> ".join(f"{a['model']} {a['confidence']}" for a in result["attempts"]) or "no model"
    print(f"\nOVERALL: {result['overall']}   CONFIDENCE: {result['confidence']} "
          f"({tried}; threshold {result['confidence_threshold']})")
    if result["human_review"]:
        print("  >> HUMAN REVIEW NEEDED: the review's confidence stayed below the threshold")
    print(f"  {result['summary']}\n")
    _print_table([{"check": c["name"], "status": c["status"], "conf": c["confidence"], "details": c["details"]}
                  for c in result["checks"]])
    print()
    matches = {r["line"]: r for r in result["lines"]}
    _print_table([{"line": l["line"], "account": l.get("account"), "debit": l["debit"], "credit": l["credit"],
                   "status": matches[l["line"]]["status"], "conf": matches[l["line"]]["confidence"],
                   "document": matches[l["line"]]["document"],
                   "issues": "; ".join(matches[l["line"]]["issues"])} for l in je["lines"]])
    print()
    for d in result["documents"]:
        print(f"  {d['file']:<30} {d.get('error') or d['method'] + ' - ' + (d.get('summary') or '')}")


def _print_table(rows: List[Dict[str, Any]]) -> None:
    columns = list(rows[0])
    cells = [[_cell(row[col]) for col in columns] for row in rows]
    widths = [max(len(col), *(len(r[i]) for r in cells)) for i, col in enumerate(columns)]
    print("  " + "  ".join(col.upper().ljust(w) for col, w in zip(columns, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for row in cells:
        print("  " + "  ".join(cell.ljust(w) for cell, w in zip(row, widths)))


def _cell(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.2f}"
    s = str(v)
    return s if len(s) <= 60 else s[:57] + "..."
