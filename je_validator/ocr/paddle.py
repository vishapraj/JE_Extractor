"""
OCR with PaddleOCR running on this machine:

    PP-DocLayout (LAYOUT_MODEL)  finds a page's zones - title, text, table,
                                 header, footer, seal, ... - with their labels
    PP-OCRv6                     reads the text lines, each with its box and
                                 confidence
    PaddleOCR-VL-1.6             reads a table, seal, chart or formula zone with its
                                 structure (OCR_MODE hybrid, see vl.py), or whole
                                 pages as Markdown (vl, or auto for pages with them)

With ppocr (the default), the lines are arranged by the zones in reading order
(documents/layout.py). LAYOUT_MODEL= (empty) arranges them by geometry alone.
hybrid also reads complex zones with VL, keeping VL's reading of a zone only
where PP-OCR read the same amounts there. hybrid, auto and vl need the
doc-parser packages (see requirements-ocr.txt) and, in practice, a GPU.
"""

from functools import lru_cache
from typing import Any, Dict, List, Tuple

import numpy as np
from PIL import Image

from je_validator.config import LAYOUT_MODEL, LAYOUT_THRESHOLD, OCR_DEVICE, OCR_MODE, OCR_MODEL_SIZE
from je_validator.documents.layout import Line, Zone, arrange, join_zones, looks_tabular
from je_validator.ocr import vl

COMPLEX_LABELS = {"table", "seal", "chart", "formula"}  # zones PP-OCRv6 alone would flatten

PPOCR = f"PP-OCRv6 {OCR_MODEL_SIZE}"
VL = "PaddleOCR-VL-1.6"


def read_image(image: Image.Image) -> Tuple[str, str, List[Dict[str, Any]]]:
    """(text, engine, zones for display) for one page image."""
    zones = detect_zones(image)
    if OCR_MODE == "vl" or (OCR_MODE == "auto" and any(z.label in COMPLEX_LABELS for z in zones)):
        return _vl_markdown(_bgr(image)), VL, []
    lines = read_lines(image)
    engine = PPOCR
    if OCR_MODE == "hybrid":
        zones = join_zones(zones, lines)  # VL reads a table the layout model split as one
        if read_complex_zones(image, zones, lines):
            engine = f"{PPOCR} + {vl.ENGINE}"
    text, shown = arrange(lines, zones)
    return text, engine, shown


def read_complex_zones(image: Image.Image, zones: List[Zone], lines: List[Line]) -> bool:
    """Reads the table, seal, chart and formula zones - and text zones laid out as a grid, which the
    layout model may label 'text' (a VAT table beside the totals) - with PaddleOCR-VL, keeping each
    reading only if PP-OCR read the same amounts in that zone (vl.cross_check). True if VL's reading
    was used anywhere."""
    used = False
    for z in zones:
        if (z.box[2] - z.box[0]) * (z.box[3] - z.box[1]) < 4000:
            continue
        inside = [l for l in lines
                  if z.box[0] <= (l.box[0] + l.box[2]) / 2 <= z.box[2] and z.box[1] <= (l.box[1] + l.box[3]) / 2 <= z.box[3]]
        if z.label not in vl.LABELS and not (inside and looks_tabular(inside)):
            continue
        pad = 8  # a little margin, so letters on the zone's edge aren't cut
        crop = image.crop((max(0, z.box[0] - pad), max(0, z.box[1] - pad),
                           min(image.width, z.box[2] + pad), min(image.height, z.box[3] + pad)))
        rows = vl.read_zone(crop, z.label if z.label in vl.LABELS else "table")
        agreed, why = vl.cross_check(rows, [l.text for l in inside])
        if agreed and rows:
            z.text, z.note = rows, f"read by {vl.ENGINE}; {why}"
            used = True
        else:
            z.note = f"read by {PPOCR}: {vl.ENGINE}'s reading not used ({why or 'it returned nothing'})"
    return used


def read_lines(image: Image.Image) -> List[Line]:
    """PP-OCRv6's text lines, each with its box (pixels) and recognition confidence. PP-OCR may box
    words of neighbouring columns as one line ('Invoice No Date'); such a line is split where the gap
    between two words is wider than a word space, as for a PDF's text layer."""
    result = next(iter(_model("ppocr").predict(_bgr(image), return_word_box=True)))
    lines: List[Line] = []
    for i, (text, box, score) in enumerate(zip(result["rec_texts"], result["rec_boxes"], result["rec_scores"])):
        x0, y0, x1, y1 = (float(v) for v in box)
        words = result["text_word"][i] if "text_word" in result else []
        regions = result["text_word_region"][i] if "text_word_region" in result else []
        pieces = _split_at_gaps(words, regions, y1 - y0) if len(words) == len(regions) and words else []
        if len(pieces) <= 1:
            lines.append(Line(text, (x0, y0, x1, y1), float(score)))
        else:
            lines += [Line(t, (a, y0, b, y1), float(score)) for t, a, b in pieces]
    return lines


COLUMN_GAP = 1.0  # x the line's height. Measured on scans: word spaces 0.5-0.7 (PP-OCR's word positions
                  # are estimates), gaps between columns 1.4-1.7.


def _split_at_gaps(words, regions, height: float) -> List[Tuple[str, float, float]]:
    """(text, x0, x1) of the parts of a line between gaps wider than COLUMN_GAP x its height."""
    spans = [(w, float(np.min(np.array(r).reshape(-1, 2)[:, 0])), float(np.max(np.array(r).reshape(-1, 2)[:, 0])))
             for w, r in zip(words, regions)]
    pieces: List[List] = []
    space = False  # PP-OCR gives the line's spaces as ' ' words; the text keeps exactly those
    for w, a, b in spans:
        if not w.strip():
            space = True
            continue
        if pieces and a - pieces[-1][2] <= COLUMN_GAP * height:
            pieces[-1][0] += (" " if space else "") + w
            pieces[-1][2] = b
        else:
            pieces.append([w, a, b])
        space = False
    return [(t, a, b) for t, a, b in pieces]


def detect_zones(image: Image.Image) -> List[Zone]:
    """The page's zones from the layout model; none when LAYOUT_MODEL is empty."""
    if not LAYOUT_MODEL:
        return []
    result = next(iter(_model("layout").predict(_bgr(image), threshold=LAYOUT_THRESHOLD)))
    return [Zone(b["label"], tuple(float(v) for v in b["coordinate"]), float(b["score"])) for b in result["boxes"]]


def _bgr(image: Image.Image) -> np.ndarray:
    return np.array(image.convert("RGB"))[:, :, ::-1]  # PaddleOCR expects BGR, as OpenCV reads it


def _vl_markdown(page: np.ndarray) -> str:
    result = next(iter(_model("vl").predict(page)))
    return result.markdown["markdown_texts"]


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
        return paddleocr.LayoutDetection(model_name=LAYOUT_MODEL, device=OCR_DEVICE)
    return paddleocr.PaddleOCRVL(pipeline_version="v1.6", device=OCR_DEVICE)
