"""
Limits on untrusted files, checked before a parser does the expensive work: a
small upload can unpack, or render, into gigabytes (a "decompression bomb").
"""

import zipfile
from pathlib import Path

from je_validator.config import MAX_FILE_MB, MAX_IMAGE_MEGAPIXELS, MAX_PAGES, MAX_UNZIPPED_MB


def check_file(path: Path) -> None:
    if path.stat().st_size > MAX_FILE_MB * 1024 * 1024:
        raise ValueError(f"{path.name} is larger than {MAX_FILE_MB} MB (MAX_FILE_MB)")


def check_zip(path: Path) -> None:
    """Excel (.xlsx/.xlsm/.xlsb) and Word (.docx) files are zip archives."""
    try:
        with zipfile.ZipFile(path) as z:
            entries = z.infolist()
    except zipfile.BadZipFile:
        raise ValueError(f"{path.name} is damaged or not really a {path.suffix} file") from None
    if len(entries) > 10_000 or sum(e.file_size for e in entries) > MAX_UNZIPPED_MB * 1024 * 1024:
        raise ValueError(f"{path.name} unpacks to more than {MAX_UNZIPPED_MB} MB (MAX_UNZIPPED_MB)")


def check_pages(count: int, name: str, what: str = "pages") -> None:
    if count > MAX_PAGES:
        raise ValueError(f"{name} has {count} {what}; at most {MAX_PAGES} are read (MAX_PAGES)")


def check_pixels(width: float, height: float, name: str) -> None:
    if width * height > MAX_IMAGE_MEGAPIXELS * 1_000_000:
        raise ValueError(f"{name}: an image or page of {width:.0f} x {height:.0f} pixels is larger than "
                         f"{MAX_IMAGE_MEGAPIXELS} megapixels (MAX_IMAGE_MEGAPIXELS)")
