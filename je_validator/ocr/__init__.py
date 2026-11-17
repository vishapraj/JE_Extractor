"""
OCR and layout for scanned pages and photos, on this machine. The document
readers call read_image(image) -> (text, engine, zones), detect_zones(image)
for the layout of typed PDF pages, and read_complex_zones (OCR_MODE=hybrid) to
have PaddleOCR-VL read a page's tables, seals, charts and formulas; PaddleOCR
(paddle.py, vl.py) is the engine, and another engine would replace these imports.
"""

from je_validator.ocr.paddle import detect_zones, read_complex_zones, read_image

__all__ = ["detect_zones", "read_complex_zones", "read_image"]
