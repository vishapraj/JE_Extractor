"""
    python -m je_validator validate --je JE.xlsx invoice.pdf bank.pdf approval.docx
    python -m je_validator templates list | show NAME | add FILE... | remove NAME
    python -m je_validator serve
    python -m je_validator eval generate | run --model NAME | compare RUN1 RUN2
"""

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m je_validator",
                                     description="Validate JE Excels against their supporting documents.")
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="validate a JE Excel against its supporting documents")
    v.add_argument("--je", type=Path, required=True, help="the JE Excel file")
    v.add_argument("--template", help="JE template name (default: the one that fits the file)")
    v.add_argument("documents", type=Path, nargs="+", help="supporting documents")

    t = sub.add_parser("templates", help="manage the JE Excel templates in the database").add_subparsers(
        dest="action", required=True)
    t.add_parser("list", help="list the templates")
    t.add_parser("show", help="print a template as JSON").add_argument("name")
    t.add_parser("add", help="add or replace templates from JSON files").add_argument("files", type=Path, nargs="+")
    t.add_parser("remove", help="delete a template").add_argument("name")

    sub.add_parser("serve", help="start the upload page on http://127.0.0.1:8000")

    e = sub.add_parser("eval", help="measure accuracy on a gold set with known answers").add_subparsers(
        dest="action", required=True)
    g = e.add_parser("generate", help="make a synthetic gold set (needs requirements-eval.txt)")
    g.add_argument("--out", type=Path, default=Path("goldset"))
    g.add_argument("--cases", type=int, default=52)
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--complex", type=float, default=0.0, help="share of invoices laid out in blocks and tables (0-1)")
    g.add_argument("--layouts", type=int, choices=(1, 2), default=1,
                   help="block layouts to alternate between (2 adds a second, differently worded layout)")
    r = e.add_parser("run", help="validate every gold-set case and score it")
    r.add_argument("--gold", type=Path, default=Path("goldset"))
    r.add_argument("--model", help="the one model to test, without escalation; leave out to score extraction only")
    r.add_argument("--out", type=Path, help="default: eval_results/<model>")
    r.add_argument("--limit", type=int, help="only the first N cases")
    r.add_argument("--target", type=float, default=0.90, help="accuracy the 95%% lower bounds must reach")
    c = e.add_parser("compare", help="McNemar's test between two runs on the same gold set")
    c.add_argument("first", type=Path)
    c.add_argument("second", type=Path)

    args = parser.parse_args()
    try:
        {"validate": _validate, "templates": _templates, "serve": _serve, "eval": _eval}[args.command](args)
    except ValueError as e:  # includes pydantic's ValidationError and the model providers' errors
        raise SystemExit(str(e))


def _validate(args) -> None:
    from je_validator.report import print_report
    from je_validator.validation.pipeline import validate

    result = validate(args.je, args.documents, args.template)
    print_report(result)
    out = args.je.with_name(f"{args.je.stem}_validation.json")
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nSaved {out}")


def _templates(args) -> None:
    from je_validator.je.store import delete_template, get_template, list_templates, save_template
    from je_validator.je.template import JETemplate

    if args.action == "list":
        for t in list_templates():
            print(f"{t.name:<24} {t.description}")
    elif args.action == "show":
        print(get_template(args.name).model_dump_json(indent=2, exclude_none=True))
    elif args.action == "add":
        for f in args.files:
            template = JETemplate.model_validate(json.loads(f.read_text(encoding="utf-8")))
            save_template(template)
            print(f"Saved {template.name}")
    elif args.action == "remove":
        print("Removed" if delete_template(args.name) else f"No template named {args.name}")


def _serve(args) -> None:
    from je_validator.web.app import serve

    serve()


def _eval(args) -> None:
    if args.action == "generate":
        from je_validator.evaluation.goldset import generate

        generate(args.out, args.cases, args.seed, args.complex, args.layouts)
    elif args.action == "run":
        from je_validator.evaluation.evaluate import run

        out = args.out or Path("eval_results") / (args.model or "extraction").replace(":", "_").replace("/", "_")
        run(args.gold, out, args.model, args.limit, args.target)
    else:
        from je_validator.evaluation.evaluate import compare

        compare(args.first, args.second)
