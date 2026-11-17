from pathlib import Path
from typing import Any, Dict, List, Tuple

import pdfplumber

from je_validator import limits, ocr
from je_validator.config import OCR_MODE
from je_validator.documents import pictures
from je_validator.documents.layout import Line, arrange, join_zones
from je_validator.ocr.vl import ENGINE as VL_ENGINE

MIN_PAGE_TEXT = 20  # a PDF page with less text than this is treated as a scan
PDF_RENDER_DPI = 200


def read(path: Path) -> Tuple[str, List[str], List[Dict[str, Any]]]:
    """Page by page: the text layer where there is one, arranged by the page's layout; OCR where the
    page is a scan. On a page with a text layer, pictures placed on it (a pasted scan or screenshot)
    are read by OCR too."""
    pages, methods, layout = [], [], []
    scale = PDF_RENDER_DPI / 72  # PDF sizes are in points
    with pdfplumber.open(path) as pdf:
        limits.check_pages(len(pdf.pages), path.name)
        read_pictures = 0
        for i, page in enumerate(pdf.pages, 1):
            limits.check_pixels(page.width * scale, page.height * scale, path.name)
            image = page.to_image(resolution=PDF_RENDER_DPI).original
            engines = []  # OCR engines that read pictures on the page
            if len((page.extract_text() or "").strip()) >= MIN_PAGE_TEXT:
                method = "text layer"
                lines, found = _text_layer_lines(page, scale), _zones(image)
                if OCR_MODE == "hybrid" and found:
                    # VL reads the complex zones; kept only where it agrees with the exact text layer
                    found = join_zones(found, lines)  # a table the layout model split is read as one
                    if ocr.read_complex_zones(image, found, lines):
                        engines.append(VL_ENGINE)
                text, zones = arrange(lines, found)
                for n, picture in enumerate(page.images, 1):
                    box = _inside(page, picture)
                    if box is None or not pictures.big_enough((box[2] - box[0]) * scale, (box[3] - box[1]) * scale):
                        continue
                    if len((page.within_bbox(box).extract_text() or "").strip()) >= MIN_PAGE_TEXT:
                        continue  # a searchable scan: its text layer already holds what the picture shows
                    read_pictures += 1
                    limits.check_pages(read_pictures, path.name, "pictures")
                    block, engine = pictures.read_picture(page.crop(box).to_image(resolution=PDF_RENDER_DPI).original,
                                                          n, path.name)
                    text += "\n\n" + block
                    engines.append(engine)
                    # shown on the page as a zone of its own, so the reviewer sees where OCR text came from
                    zones.append({"n": None, "image": n, "label": "picture", "score": None,
                                  "box": [round(v * scale) for v in box]})
            else:
                text, method, zones = ocr.read_image(image)
            pages.append(f"[Page {i} - {method}]\n{text}")
            methods += [method, *engines]
            layout.append({"page": i, "size": list(image.size), "zones": zones, "image": image})
    return "\n\n".join(pages), methods, layout


def _text_layer_lines(page, scale: float) -> List[Line]:
    """The text layer's words, joined into pieces of a line; a wide gap (a column) starts a new piece."""
    words = sorted(page.extract_words(x_tolerance=2, y_tolerance=3), key=lambda w: (round(w["top"]), w["x0"]))
    pieces: List[List[dict]] = []
    for w in words:
        last = pieces[-1][-1] if pieces else None
        height = w["bottom"] - w["top"]
        # A word space is about a quarter of the text's height; a gap of more than 0.6 is a column.
        if last and abs(w["top"] - last["top"]) <= 3 and 0 <= w["x0"] - last["x1"] <= 0.6 * height:
            pieces[-1].append(w)
        else:
            pieces.append([w])
    return [Line(" ".join(w["text"] for w in p),
                 (p[0]["x0"] * scale, min(w["top"] for w in p) * scale, p[-1]["x1"] * scale,
                  max(w["bottom"] for w in p) * scale)) for p in pieces]


def _zones(image):
    """The layout model's zones, or none if PaddleOCR isn't installed: then geometry alone orders the page."""
    try:
        return ocr.detect_zones(image)
    except ValueError:
        return []


def _inside(page, picture):
    """The part of the picture's box that lies on the page, or None."""
    x0, top = max(picture["x0"], page.bbox[0]), max(picture["top"], page.bbox[1])
    x1, bottom = min(picture["x1"], page.bbox[2]), min(picture["bottom"], page.bbox[3])
    return (x0, top, x1, bottom) if x1 > x0 and bottom > top else None
