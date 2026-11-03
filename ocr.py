"""
OCR for scanned pages and photos, with PaddleOCR running on this machine:
PP-OCRv6 for ordinary pages, PaddleOCR-VL-1.6 for pages with tables, forms,
stamps or charts, which it returns as Markdown that keeps their structure.

OCR_MODE (in .env) picks the engine:
    auto   - a small layout model looks at each page and picks (default)
    ppocr  - always PP-OCRv6 (fastest; use this on a laptop without a GPU)
    vl     - always PaddleOCR-VL-1.6
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Tuple

import numpy as np
from dotenv import load_dotenv
from PIL import Image

load_dotenv(Path(__file__).parent / ".env")

OCR_MODE = os.getenv("OCR_MODE", "auto")
OCR_DEVICE = os.getenv("OCR_DEVICE", "cpu")  # e.g. gpu:0
COMPLEX_LABELS = {"table", "seal", "chart", "formula"}  # layout regions PP-OCRv6 would flatten

PPOCR = "PP-OCRv6"
VL = "PaddleOCR-VL-1.6"


def read_image(image: Image.Image) -> Tuple[str, str]:
    """(text, engine) for one page image."""
    page = np.array(image.convert("RGB"))[:, :, ::-1]  # PaddleOCR expects BGR, as OpenCV reads it
    if OCR_MODE == "vl" or (OCR_MODE == "auto" and _is_complex(page)):
        return _vl_markdown(page), VL
    return _ppocr_text(page), PPOCR


def _is_complex(page: np.ndarray) -> bool:
    result = next(iter(_model("layout").predict(page)))
    return any(box["label"] in COMPLEX_LABELS for box in result["boxes"])


def _ppocr_text(page: np.ndarray) -> str:
    result = next(iter(_model("ppocr").predict(page)))
    return _lines(result["rec_texts"], result["rec_boxes"])


def _vl_markdown(page: np.ndarray) -> str:
    result = next(iter(_model("vl").predict(page)))
    return result.markdown["markdown_texts"]


def _lines(texts, boxes) -> str:
    """Rebuilds the page's lines from PP-OCRv6's text boxes, left to right, top to bottom."""
    lines = []  # [y centre, height, [(x, text)]]
    for text, (x0, y0, x1, y1) in sorted(zip(texts, boxes), key=lambda tb: tb[1][1] + tb[1][3]):
        y, h = (y0 + y1) / 2, y1 - y0
        if lines and abs(y - lines[-1][0]) < max(h, lines[-1][1]) / 2:
            lines[-1][2].append((x0, text))
        else:
            lines.append([y, h, [(x0, text)]])
    return "\n".join("   ".join(t for _, t in sorted(parts)) for _, _, parts in lines)


@lru_cache(maxsize=None)
def _model(kind: str):
    """Loaded on first use (each takes a while, and downloads its weights the first time)."""
    try:
        from paddleocr import LayoutDetection, PaddleOCR, PaddleOCRVL
    except ImportError:
        raise ValueError("OCR needs PaddleOCR, which isn't installed - see 'OCR setup' in the README") from None
    if kind == "layout":
        return LayoutDetection(model_name="PP-DocLayout-S", device=OCR_DEVICE)
    if kind == "ppocr":
        return PaddleOCR(ocr_version="PP-OCRv6", device=OCR_DEVICE,
                         use_doc_unwarping=False, use_textline_orientation=False)
    return PaddleOCRVL(pipeline_version="v1.6", device=OCR_DEVICE)
