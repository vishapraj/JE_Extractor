"""
SHA-256 fingerprints of the files a validation used, recorded in its result.
They show that a result was produced from exactly these files: hashing the
files again later gives the same values only if not a byte has changed. A
fingerprint reveals nothing about the file's content.

    certutil -hashfile invoice.pdf SHA256      (Windows)
    sha256sum invoice.pdf                      (Linux, macOS: shasum -a 256)
"""

import hashlib
from pathlib import Path
from typing import Optional


def sha256_file(path: Path) -> Optional[str]:
    """The file's SHA-256 as hex, or None if it can't be read (that file's own error is reported elsewhere)."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()
