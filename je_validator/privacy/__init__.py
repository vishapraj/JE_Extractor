"""
Protects personal data before anything reaches a model or is stored: every
validation passes the JE and the documents' text through protect().

    minimise.py  keeps only the values the checks need
    mask.py      replaces personal data with placeholders such as [PERSON-A]

Masking runs first, so a line that held only personal data is then dropped by
minimising. The result shows the protected text: what the model saw.
"""

from typing import Any, Dict, List, Tuple

from je_validator.privacy.mask import Masker
from je_validator.privacy.minimise import minimise_je, minimise_text


def protect(je: Dict[str, Any], documents: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    needed = [je["header"].get("je_number"), je["header"].get("reference")] + [
        line.get(k) for line in je["lines"] for k in ("account", "reference", "vendor")]
    masker = Masker(known_names=[je["header"].get("prepared_by"), je["header"].get("approved_by")], keep=needed)
    je = minimise_je(je)
    je["file"] = masker.text(je["file"])
    je["header"] = {k: masker.text(v) if isinstance(v, str) else v for k, v in je["header"].items()}
    je["lines"] = [{k: masker.text(v) if isinstance(v, str) and k != "account" else v for k, v in line.items()}
                   for line in je["lines"]]
    keep = {line.get("vendor") for line in je["lines"]} | {je["header"].get("je_number")}
    protected = []
    for d in documents:
        d = {**d, "file": masker.text(d["file"])}
        if "text" in d:
            d["text"] = minimise_text(masker.text(d["text"]), keep)
        protected.append(d)
    return je, protected
