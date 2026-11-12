"""
Measures how accurate the validator really is, as distinct from how confident it says it is.

    goldset.py   makes a synthetic gold set: JEs and documents with known answers
    render.py    writes a case's files (JE Excel, invoice PDF or scan, approval, bank CSV)
    evaluate.py  runs the validator on the gold set and scores each stage against the answers
    stats.py     confidence intervals and the paired test for comparing two models

    python -m je_validator eval generate
    python -m je_validator eval run --model qwen2.5:7b
    python -m je_validator eval compare eval_results/qwen2.5_7b eval_results/qwen2.5_14b

Needs requirements-eval.txt (only for generate).
"""
