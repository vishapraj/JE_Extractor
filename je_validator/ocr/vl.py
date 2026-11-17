"""
PaddleOCR-VL for the zones line-by-line OCR flattens (OCR_MODE=hybrid): tables,
seals, charts and formulas. It reads a zone with its structure - which cell is
under which heading - where PP-OCR only sees lines of text.

PaddleOCR-VL is generative, so it can write a number that isn't on the page, or
leave one out. Its reading of a zone is therefore checked against PP-OCR's
reading of the same zone, and used only if the two read exactly the same
amounts. Otherwise the zone keeps PP-OCR's reading, and the reason is recorded
(shown in the viewer). So VL can add structure, never a figure.
"""

import html.parser
import re
from collections import Counter
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from je_validator.config import OCR_DEVICE
from je_validator.validation.amounts import amount_values

MODEL = "PaddleOCR-VL-1.6-0.9B"
ENGINE = "PaddleOCR-VL-1.6"
PROMPTS = {"table": "Table Recognition:", "seal": "Seal Recognition:", "chart": "Chart Recognition:",
           "formula": "Formula Recognition:"}
LABELS = set(PROMPTS)  # zones VL reads; every other zone keeps PP-OCR's lines
MAX_NEW_TOKENS = 2048


def read_zone(image: Image.Image, label: str) -> List[str]:
    """VL's reading of one zone image, as rows ('cell | cell')."""
    result = next(iter(_model().predict([{"image": np.array(image.convert("RGB")), "query": PROMPTS[label]}],
                                        skip_special_tokens=True, max_new_tokens=MAX_NEW_TOKENS, use_cache=True)))
    return to_rows(_text_of(result))


def cross_check(vl_rows: Sequence[str], ocr_texts: Sequence[str]) -> Tuple[bool, str]:
    """(use VL's reading?, why). Both readings must hold exactly the same amounts, each as often."""
    vl, ocr = _amounts(vl_rows), _amounts(ocr_texts)
    invented, missing = vl - ocr, ocr - vl
    if not vl and not ocr:
        return True, "no amounts in the zone"
    if invented or missing:
        parts = []
        if invented:
            parts.append("VL wrote amounts PP-OCR didn't read: " + ", ".join(_show(invented)))
        if missing:
            parts.append("VL left out amounts PP-OCR read: " + ", ".join(_show(missing)))
        return False, "; ".join(parts)
    return True, f"all {sum(vl.values())} amounts confirmed by PP-OCR"


def to_rows(text: str) -> List[str]:
    """VL's output as rows: OTSL or HTML tables become 'cell | cell' rows, Markdown tables lose their
    rule lines, anything else stays line by line."""
    if re.search(r"<(fcel|ecel|lcel|ucel|xcel|nl)>", text):
        from paddlex.inference.pipelines.paddleocr_vl.uilts import convert_otsl_to_html  # sic: their module name
        text = convert_otsl_to_html(text)
    if "<td" in text or "<th" in text:
        return _html_rows(text)
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or re.fullmatch(r"\|?[\s:\-|]+\|?", line):  # blank, or a Markdown table's rule line
            continue
        rows.append(" | ".join(c.strip() for c in line.strip("|").split("|")) if "|" in line else line)
    return rows


def _amounts(texts: Sequence[str]) -> Counter:
    return Counter(round(v, 2) for t in texts for v in amount_values(t))


def _show(c: Counter) -> List[str]:
    return [f"{v:,.2f}" for v in sorted(c.elements())][:6]


def _text_of(result) -> str:
    for key in ("result", "rec_text", "text"):
        try:
            value = result[key]
        except (KeyError, TypeError):
            continue
        if isinstance(value, str):
            return value
    return str(result)


class _Rows(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: List[List[str]] = []
        self.cell: Optional[List[str]] = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.rows:
            self.rows[-1].append(" ".join("".join(self.cell).split()))
            self.cell = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def _html_rows(text: str) -> List[str]:
    parser = _Rows()
    parser.feed(text)
    return [" | ".join(r) for r in parser.rows if any(c for c in r)]


@lru_cache(maxsize=None)
def _model():
    """Loaded on first use: about 2 GB of weights, so only when a zone needs it."""
    try:
        import paddleocr
    except ImportError:
        raise ValueError("OCR needs PaddleOCR, which isn't installed - see 'OCR setup' in the README") from None
    return paddleocr.DocVLM(model_name=MODEL, device=OCR_DEVICE)
