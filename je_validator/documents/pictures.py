"""
Pictures inside otherwise typed documents - an invoice pasted into a Word memo,
a scan placed on a PDF page that also has typed text, a screenshot in an Excel
sheet - read by OCR. Each picture's text is added as its own block,
"[Image 2 - PP-OCRv6 small]". Small pictures (logos, icons, signatures) are
skipped: they carry no figures worth reading.
"""

import io
import re
import zipfile
from pathlib import Path
from typing import List, Tuple

from PIL import Image

from je_validator import limits, ocr

MIN_PICTURE_PIXELS = 40_000  # below about 200 x 200 pixels: a logo, icon or signature


def big_enough(width: float, height: float) -> bool:
    return width * height >= MIN_PICTURE_PIXELS


def read_picture(image: Image.Image, number: int, name: str) -> Tuple[str, str]:
    """(the picture's text block, the OCR engine)."""
    limits.check_pixels(*image.size, name)
    if image.mode in ("RGBA", "LA", "P"):  # transparent parts would turn black; put them on white paper
        image = image.convert("RGBA")
        paper = Image.new("RGB", image.size, "white")
        paper.paste(image, mask=image.getchannel("A"))
        image = paper
    text, engine, _ = ocr.read_image(image)
    return f"[Image {number} - {engine}]\n{text}", engine


def office_pictures(path: Path, folder: str) -> Tuple[List[str], List[str]]:
    """The pictures an Office file stores in its media folder (word/media/, xl/media/), in file order."""
    blocks, methods = [], []
    with zipfile.ZipFile(path) as z:  # its unpacked size was checked before reading (limits.check_zip)
        names = sorted((n for n in z.namelist() if n.startswith(folder)), key=_natural)
        for number, member in enumerate(names, 1):
            try:
                image = Image.open(io.BytesIO(z.read(member)))
                size = image.size
            except Exception:  # e.g. vector drawings (.emf, .wmf, .svg)
                blocks.append(f"[Image {number} - not read: {Path(member).suffix} pictures aren't supported]")
                continue
            if not big_enough(*size):
                continue
            limits.check_pages(len(methods) + 1, path.name, "pictures")
            block, engine = read_picture(image, number, path.name)
            blocks.append(block)
            methods.append(engine)
    return blocks, methods


def _natural(name: str):
    """image2 before image10."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name)]
