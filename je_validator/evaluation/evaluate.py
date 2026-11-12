"""
Runs the validator on a gold set and scores it against the known answers, for
each stage separately:

    extraction  the JE read with its template, and the documents' text and
                amounts (text layer, Word, CSV, OCR) - no model involved
    validation  the model's review after the code's checks: each line's status,
                each check's status, and whether a JE with an error is stopped

Every rate comes with a 95% Wilson interval. The model's confidence is set
against its measured accuracy, per confidence band, so a model that is sure but
often wrong shows up as such. The gate approves a model only if the lower ends
of its intervals clear the target.

Each case's result is saved as soon as it is done, so a stopped run carries on
where it left off. Delete a case's file to run it again.
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from je_validator.documents import read_document
from je_validator.evaluation.stats import mcnemar, wilson
from je_validator.je.reader import read_je
from je_validator.privacy import protect
from je_validator.validation.amounts import tag_amounts
from je_validator.validation.review import MODEL_RULES

BANDS = [(90, 100), (80, 89), (50, 79), (0, 49)]  # confidence bands for the calibration table
MODEL_ITEMS = {"line", "AMOUNTS", *MODEL_RULES}  # items the model decides (BALANCED is pure code)


def run(gold: Path, out: Path, model: Optional[str], limit: Optional[int], target: float) -> Dict[str, Any]:
    """model None: extraction only."""
    cases = sorted(p.parent for p in gold.glob("*/expected.json"))[:limit]
    if not cases:
        raise ValueError(f"no gold-set cases in {gold} - make them with: python -m je_validator eval generate")
    out.mkdir(parents=True, exist_ok=True)
    if model:
        from je_validator import config
        from je_validator.llm import load_provider
        from je_validator.validation.pipeline import validate
        load_provider(config.LLM_PROVIDER).prepare([model])  # stop now if the model can't be used

    for i, case in enumerate(cases, 1):
        saved = out / f"{case.name}.json"
        if saved.exists():
            continue
        exp = _expected(case)
        record: Dict[str, Any] = {"extraction": _extract(case, exp)}
        if model:
            start = time.monotonic()
            try:
                record["validation"] = validate(case / exp["je_file"], [case / f for f in exp["documents"]],
                                                models=[model])
            except ValueError as e:  # the model failed: counted as wrong, and the JE as stopped
                record["validation"] = {"error": str(e)}
            record["seconds"] = round(time.monotonic() - start, 1)
        saved.write_text(json.dumps(record, default=str), encoding="utf-8")
        v = record.get("validation", {})
        print(f"[{i}/{len(cases)}] {case.name} {exp['error']:<17}"
              + (f" -> {v.get('overall', 'ERROR: ' + v.get('error', ''))[:60]}  {record['seconds']} s" if model else ""),
              flush=True)

    report = score(cases, out, model, target)
    (out / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print_report(report)
    return report


def _expected(case: Path) -> Dict[str, Any]:
    return json.loads((case / "expected.json").read_text(encoding="utf-8"))


def _extract(case: Path, exp: Dict[str, Any]) -> Dict[str, Any]:
    try:
        je = read_je(case / exp["je_file"])
    except ValueError as e:
        je = {"error": str(e)}
    documents = []
    for f in exp["documents"]:
        try:
            documents.append(read_document(case / f))
        except Exception as e:
            documents.append({"file": f, "error": f"{type(e).__name__}: {e}"})
    record = {"journal_entry": je, "documents": documents}
    if "error" not in je:  # what the model would be given
        record["protected_je"], record["protected_documents"] = protect(je, documents)
    return record


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score(cases: List[Path], out: Path, model: Optional[str], target: float) -> Dict[str, Any]:
    ext = {"je_template": [0, 0], "je_fields": [0, 0], "document_amounts": {}, "ocr_cer": [],
           "pii_leaked": [0, 0], "leaks": [], "amounts_kept": [0, 0], "chars": [0, 0]}
    items: List[Dict[str, Any]] = []
    case_rows: List[Dict[str, Any]] = []
    seconds = []
    for case in cases:
        saved = out / f"{case.name}.json"
        if not saved.exists():
            continue
        exp, record = _expected(case), json.loads(saved.read_text(encoding="utf-8"))
        _score_extraction(exp, record["extraction"], ext)
        if "validation" in record:
            case_rows.append(_score_validation(exp, record["validation"], items))
            seconds.append(record["seconds"])

    report: Dict[str, Any] = {"model": model, "target": target, "cases": len(case_rows) or None,
                              "extraction": {"je_template": ext["je_template"], "je_fields": ext["je_fields"],
                                             "document_amounts": ext["document_amounts"],
                                             "ocr_documents": len(ext["ocr_cer"]),
                                             "ocr_cer": round(sum(ext["ocr_cer"]) / len(ext["ocr_cer"]), 4)
                                             if ext["ocr_cer"] else None},
                              "privacy": {"pii_leaked": ext["pii_leaked"], "leaks": ext["leaks"],
                                          "amounts_kept": ext["amounts_kept"],
                                          "text_kept": round(ext["chars"][1] / ext["chars"][0], 3)
                                          if ext["chars"][0] else None}}
    if case_rows:
        report["validation"] = _validation_report(items, case_rows, target)
        report["validation"]["seconds_per_case"] = round(sum(seconds) / len(seconds), 1)
        report["items"], report["case_results"] = items, case_rows
    return report


def _score_extraction(exp: Dict[str, Any], ext: Dict[str, Any], tally: Dict[str, Any]) -> None:
    je = ext["journal_entry"]
    tally["je_template"][0] += je.get("template") == exp["template"]
    tally["je_template"][1] += 1
    header, lines = je.get("header", {}), je.get("lines", [])
    pairs = [(header.get(f), v) for f, v in exp["je"]["header"].items()]
    for i, gold_line in enumerate(exp["je"]["lines"]):
        got = lines[i] if i < len(lines) else {}
        pairs += [(got.get(f), v) for f, v in gold_line.items()]
    tally["je_fields"][0] += sum(_same(got, want) for got, want in pairs)
    tally["je_fields"][1] += len(pairs)

    documents = {d["file"]: d for d in ext["documents"]}
    for name, gold in exp["document_gold"].items():
        d = documents.get(name, {"error": "missing"})
        method = "unreadable" if "error" in d else "OCR" if "OCR" in d["method"] else d["method"]
        values = [a["value"] for a in tag_amounts([d])[1].values()] if "error" not in d else []
        k_n = tally["document_amounts"].setdefault(method, [0, 0])
        k_n[0] += sum(any(abs(v - a) < 0.005 for v in values) for a in gold["amounts"])
        k_n[1] += len(gold["amounts"])
        if method == "OCR":
            tally["ocr_cer"].append(_cer(gold["text"], d["text"]))

    if "protected_documents" in ext:
        _score_privacy(exp, ext, tally)


def _score_privacy(exp: Dict[str, Any], ext: Dict[str, Any], tally: Dict[str, Any]) -> None:
    """Planted personal data that would still reach the model, and needed amounts that would not."""
    seen = ext["protected_documents"]
    model_text = json.dumps(ext["protected_je"], ensure_ascii=False) + "\n" + "\n".join(
        d["file"] + "\n" + d.get("text", "") for d in seen)
    flat = " ".join(model_text.split()).casefold()
    planted = list(exp.get("je_pii", [])) + [p for g in exp["document_gold"].values() for p in g.get("pii", [])]
    for value in planted:
        v = " ".join(value.split()).casefold()
        leaked = v in flat or v.replace(" ", "") in flat.replace(" ", "")
        tally["pii_leaked"][0] += leaked
        tally["pii_leaked"][1] += 1
        if leaked:
            tally["leaks"].append(f"{exp['case']}: {value}")
    by_file = {d["file"]: d for d in seen}
    for name, gold in exp["document_gold"].items():
        d = by_file.get(name)
        values = [a["value"] for a in tag_amounts([d])[1].values()] if d and "text" in d else []
        tally["amounts_kept"][0] += sum(any(abs(v - a) < 0.005 for v in values) for a in gold["amounts"])
        tally["amounts_kept"][1] += len(gold["amounts"])
    tally["chars"][0] += sum(len(d.get("text", "")) for d in ext["documents"])
    tally["chars"][1] += sum(len(d.get("text", "")) for d in seen)


def _score_validation(exp: Dict[str, Any], v: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
    failed = "error" in v
    lines = {r["line"]: r for r in v.get("lines", [])}
    checks = {c["rule"]: c for c in v.get("checks", [])}
    for no, right in exp["lines"].items():
        r = lines.get(int(no))
        items.append({"case": exp["case"], "item": f"line {no}", "kind": "line", "expected": right,
                      "got": r["status"] if r else None, "confidence": r["confidence"] if r else None,
                      "correct": bool(r) and r["status"] in right})
    for rule, right in exp["checks"].items():
        if right is None:
            continue
        c = checks.get(rule)
        items.append({"case": exp["case"], "item": rule, "kind": rule, "expected": right,
                      "got": c["status"] if c else None, "confidence": c["confidence"] if c else None,
                      "correct": bool(c) and c["status"] in right})
    return {"case": exp["case"], "error": exp["error"], "should_flag": exp["flagged"],
            "flagged": failed or v["overall"] != "PASS", "overall": "ERROR" if failed else v["overall"],
            "confidence": v.get("confidence"), "human_review": failed or v["human_review"]}


def _validation_report(items: List[Dict[str, Any]], rows: List[Dict[str, Any]], target: float) -> Dict[str, Any]:
    def k_n(xs):
        return [sum(1 for x in xs if x), len(xs)]

    model_items = [i for i in items if i["kind"] in MODEL_ITEMS]
    flagged_errors = [r["flagged"] for r in rows if r["should_flag"]]
    passed_clean = [not r["flagged"] for r in rows if not r["should_flag"]]
    by_error = {}
    for r in rows:
        by_error.setdefault(r["error"], []).append(r["flagged"] == r["should_flag"])

    calibration = []
    scored = [i for i in model_items if i["confidence"] is not None]
    for lo, hi in BANDS:
        band = [i for i in scored if lo <= i["confidence"] <= hi]
        k, n = k_n([i["correct"] for i in band])
        calibration.append({"band": f"{lo}-{hi}", "items": n,
                            "mean_confidence": round(sum(i["confidence"] for i in band) / n, 1) if n else None,
                            "accuracy": round(k / n, 3) if n else None, "lower": round(wilson(k, n)[0], 3)})
    # Expected calibration error: how far stated confidence is from measured accuracy, on average.
    ece = sum(b["items"] * abs(b["mean_confidence"] / 100 - b["accuracy"]) for b in calibration if b["items"])
    accuracy, caught = k_n([i["correct"] for i in model_items]), k_n(flagged_errors)
    gate = {"model_items_lower": round(wilson(*accuracy)[0], 3), "errors_caught_lower": round(wilson(*caught)[0], 3)}
    gate["approved"] = gate["model_items_lower"] >= target and gate["errors_caught_lower"] >= target
    return {
        "lines": k_n([i["correct"] for i in items if i["kind"] == "line"]),
        "checks": {kind: k_n([i["correct"] for i in items if i["kind"] == kind])
                   for kind in dict.fromkeys(i["kind"] for i in items if i["kind"] != "line")},
        "model_items": accuracy, "errors_caught": caught, "clean_passed": k_n(passed_clean),
        "by_error": {e: k_n(xs) for e, xs in by_error.items()},
        "calibration": calibration, "ece": round(ece / len(scored), 3) if scored else None, "gate": gate,
    }


def _same(got: Any, want: Any) -> bool:
    if isinstance(want, float) or isinstance(got, float):
        return got is not None and want is not None and abs(float(got) - float(want)) < 0.005
    return got == want


def _cer(gold: str, text: str) -> float:
    """Character error rate: edits to turn the OCR text into the true text, per true character
    (whitespace runs count as one space, so line layout doesn't count)."""
    a, b = " ".join(gold.split()), " ".join(text.split())
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / max(1, len(a))


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _rate(k_n: List[int]) -> str:
    k, n = k_n
    if not n:
        return "-"
    lo, hi = wilson(k, n)
    return f"{k:>4}/{n:<4} {k / n:6.1%}   95% CI {lo:6.1%} - {hi:6.1%}"


def print_report(r: Dict[str, Any]) -> None:
    e = r["extraction"]
    print("\nEXTRACTION (no model)")
    print(f"  JE template recognised            {_rate(e['je_template'])}")
    print(f"  JE fields read correctly          {_rate(e['je_fields'])}")
    for method, k_n in e["document_amounts"].items():
        print(f"  Document amounts found ({method:<10}) {_rate(k_n)}")
    if e["ocr_cer"] is not None:
        print(f"  OCR character error rate          {e['ocr_cer']:.1%} on average, {e['ocr_documents']} scans")
    p = r.get("privacy")
    if p and p["pii_leaked"][1]:
        k, n = p["pii_leaked"]
        print("\nPRIVACY (what the model is given, no model needed)")
        print(f"  Planted personal data reaching the model  {k}/{n}  ({k / n:.1%}; 95% upper bound "
              f"{wilson(k, n)[1]:.1%})")
        for leak in p["leaks"][:15]:
            print(f"    leaked: {leak}")
        print(f"  Needed amounts still there        {_rate(p['amounts_kept'])}")
        print(f"  Document text kept                {p['text_kept']:.0%} of the extracted characters")
    v = r.get("validation")
    if not v:
        return
    print(f"\nVALIDATION  {r['model']}  ({r['cases']} cases, {v['seconds_per_case']} s each)")
    print(f"  Line statuses right               {_rate(v['lines'])}")
    for kind, k_n in v["checks"].items():
        print(f"  {kind + ' right':<33} {_rate(k_n)}")
    print(f"  All model-decided items right     {_rate(v['model_items'])}")
    print(f"  JEs with an error stopped         {_rate(v['errors_caught'])}")
    print(f"  Clean JEs passed                  {_rate(v['clean_passed'])}")
    print("\n  By case type (right outcome: stopped, or passed if clean)")
    for error, k_n in v["by_error"].items():
        print(f"    {error:<20} {k_n[0]}/{k_n[1]}")
    print("\n  CONFIDENCE vs MEASURED ACCURACY (model-decided items)")
    print("    band     items  mean confidence  accuracy  95% lower bound")
    for b in v["calibration"]:
        if b["items"]:
            print(f"    {b['band']:<8} {b['items']:>5}  {b['mean_confidence']:>15}  {b['accuracy']:>8.1%}  {b['lower']:>15.1%}")
    print(f"    Expected calibration error: {v['ece']:.1%} (0% = confidence matches accuracy)")
    g = v["gate"]
    print(f"\n  GATE (95% lower bounds must reach {r['target']:.0%}): items {g['model_items_lower']:.1%}, "
          f"errors stopped {g['errors_caught_lower']:.1%}  ->  {'APPROVED' if g['approved'] else 'REJECTED'}")


def compare(a: Path, b: Path) -> None:
    """McNemar's test on the items and JEs both runs scored."""
    ra, rb = (json.loads((p / "report.json").read_text(encoding="utf-8")) for p in (a, b))
    for label, key, pick in (("model-decided items", "items", lambda i: i["kind"] in MODEL_ITEMS),
                             ("JEs with an error stopped", "case_results", lambda c: c["should_flag"])):
        def right(x):
            return x["correct"] if key == "items" else x["flagged"]

        xa = {(x["case"], x.get("item")): right(x) for x in ra[key] if pick(x)}
        xb = {(x["case"], x.get("item")): right(x) for x in rb[key] if pick(x)}
        both = xa.keys() & xb.keys()
        only_a = sum(xa[k] and not xb[k] for k in both)
        only_b = sum(xb[k] and not xa[k] for k in both)
        print(f"{label}: {ra['model']} {sum(xa[k] for k in both)}/{len(both)}, "
              f"{rb['model']} {sum(xb[k] for k in both)}/{len(both)}; only {ra['model']} right: {only_a}, "
              f"only {rb['model']} right: {only_b}; McNemar p = {mcnemar(only_a, only_b):.3f}")
