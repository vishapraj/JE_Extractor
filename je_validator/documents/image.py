from pathlib import Path
from typing import List, Tuple

from PIL import Image, ImageSequence

from je_validator import limits, ocr

SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp"}


def read(path: Path) -> Tuple[str, List[str]]:
    """Every page of a multi-page TIFF; the first frame of anything else."""
    pages, methods = [], []
    with Image.open(path) as image:  # reads only the header; pixels are decoded on use
        limits.check_pages(getattr(image, "n_frames", 1), path.name)
        frames = list(ImageSequence.Iterator(image)) if image.format == "TIFF" else [image]
        for i, frame in enumerate(frames, 1):
            limits.check_pixels(*frame.size, path.name)
            text, method = ocr.read_image(frame.copy())
            pages.append(f"[Page {i} - {method}]\n{text}" if len(frames) > 1 else text)
            methods.append(method)
    return "\n\n".join(pages), methods
