"""
OCR for scanned pages and photos, on this machine. The document readers only
call read_image(image) -> (text, engine name); PaddleOCR (paddle.py) is the
engine, and another engine would replace this import.
"""

from je_validator.ocr.paddle import read_image

__all__ = ["read_image"]
