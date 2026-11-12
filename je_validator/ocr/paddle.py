"""
OCR with PaddleOCR running on this machine: PP-OCRv6 for ordinary pages,
PaddleOCR-VL-1.6 for pages with tables, forms, stamps or charts, which it
returns as Markdown that keeps their structure.

OCR_MODE (in .env) picks the engine:
    ppocr  - always PP-OCRv6 (default; fast enough for a laptop without a GPU)
    auto   - a small layout model looks at each page and picks
    vl     - always PaddleOCR-VL-1.6
auto and vl need the doc-parser extra (see requirements-ocr.txt) and, in
practice, a GPU.
"""

from functools import lru_cache
from typing import Tuple

import numpy as np
from PIL import Image

from je_validator.config import OCR_DEVICE, OCR_MODE, OCR_MODEL_SIZE

COMPLEX_LABELS = {"table", "seal", "chart", "formula"}  # layout regions PP-OCRv6 would flatten

PPOCR = f"PP-OCRv6 {OCR_MODEL_SIZE}"
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
        import paddleocr
    except ImportError:
        raise ValueError("OCR needs PaddleOCR, which isn't installed - see 'OCR setup' in the README") from None
    if kind == "ppocr":
        return paddleocr.PaddleOCR(text_detection_model_name=f"PP-OCRv6_{OCR_MODEL_SIZE}_det",
                                   text_recognition_model_name=f"PP-OCRv6_{OCR_MODEL_SIZE}_rec", device=OCR_DEVICE,
                                   use_doc_unwarping=False, use_textline_orientation=False)
    if kind == "layout":
        return paddleocr.LayoutDetection(model_name="PP-DocLayout-S", device=OCR_DEVICE)
    return paddleocr.PaddleOCRVL(pipeline_version="v1.6", device=OCR_DEVICE)
