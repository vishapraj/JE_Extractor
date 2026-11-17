from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageSequence

from je_validator import limits, ocr

SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp"}


def read(path: Path) -> Tuple[str, List[str], List[Dict[str, Any]]]:
    """Every page of a multi-page TIFF; the first frame of anything else."""
    pages, methods, layout = [], [], []
    with Image.open(path) as image:  # reads only the header; pixels are decoded on use
        limits.check_pages(getattr(image, "n_frames", 1), path.name)
        frames = list(ImageSequence.Iterator(image)) if image.format == "TIFF" else [image]
        for i, frame in enumerate(frames, 1):
            limits.check_pixels(*frame.size, path.name)
            page = frame.copy()
            text, method, zones = ocr.read_image(page)
            pages.append(f"[Page {i} - {method}]\n{text}")
            methods.append(method)
            layout.append({"page": i, "size": list(frame.size), "zones": zones, "image": page})
    return "\n\n".join(pages), methods, layout
