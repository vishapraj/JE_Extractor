"""
The settings every part of the app shares, read once from the environment and
.env (see .env.example). A model provider reads its own settings in its own
module (je_validator/llm), so nothing here is tied to a vendor or a cloud.
"""

import os
from pathlib import Path

import truststore
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent  # the project folder, which holds .env
load_dotenv(ROOT / ".env")
# Trust the certificates Windows (or macOS/Linux) trusts, not only Python's own list: behind a
# company proxy that inspects HTTPS, model downloads and API calls fail otherwise.
truststore.inject_into_ssl()

# Validation model
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")  # see PROVIDERS in je_validator/llm/__init__.py
LLM_MODEL = os.getenv("LLM_MODEL")  # unset: the provider's default
# Reviews again when the first model's confidence is low; unset: the provider's default, if it has one.
LLM_ESCALATION_MODEL = os.getenv("LLM_ESCALATION_MODEL")
CONFIDENCE_THRESHOLD = int(os.getenv("CONFIDENCE_THRESHOLD", "80"))  # 0-100; below this, escalate
AMOUNT_TOLERANCE = float(os.getenv("AMOUNT_TOLERANCE", "0.01"))

# OCR
OCR_MODE = os.getenv("OCR_MODE", "ppocr")
OCR_DEVICE = os.getenv("OCR_DEVICE", "cpu")  # e.g. gpu:0
# PP-OCRv6 comes in three sizes. On a laptop CPU an A4 page takes about 1 s (tiny),
# 2 s (small) or 35 s (medium); medium is the most accurate and is meant for a GPU.
OCR_MODEL_SIZE = os.getenv("OCR_MODEL_SIZE", "small")
# Finds each page's zones (title, text, table, ...) for reading order. PP-DocLayout_plus-L
# (~8 s a page on a laptop CPU) found far more of an invoice's zones than PP-DocLayout-S (~1 s)
# or PP-DocLayoutV3 (~20 s). Empty: order pages by geometry alone.
LAYOUT_MODEL = os.getenv("LAYOUT_MODEL", "PP-DocLayout_plus-L")
LAYOUT_THRESHOLD = float(os.getenv("LAYOUT_THRESHOLD", "0.3"))  # low: duplicates are cleaned up afterwards

# JE templates
TEMPLATES_DB = Path(os.getenv("TEMPLATES_DB", ROOT / "templates.db"))

# Limits on untrusted files (see limits.py)
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "25"))
MAX_DOCUMENTS = int(os.getenv("MAX_DOCUMENTS", "20"))  # per validation, on the page
MAX_UNZIPPED_MB = int(os.getenv("MAX_UNZIPPED_MB", "200"))  # what an .xlsx/.docx may unpack to
MAX_PAGES = int(os.getenv("MAX_PAGES", "50"))  # PDF pages and TIFF frames
MAX_IMAGE_MEGAPIXELS = int(os.getenv("MAX_IMAGE_MEGAPIXELS", "40"))  # per image or rendered PDF page

# Web page. Ask for a password (any user name) whenever others could reach it; serving beyond
# this machine (APP_HOST not 127.0.0.1) needs both a password and HTTPS.
APP_PASSWORD = os.getenv("APP_PASSWORD")
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
TLS_CERT_FILE = os.getenv("TLS_CERT_FILE")
TLS_KEY_FILE = os.getenv("TLS_KEY_FILE")
JOB_RETENTION_MINUTES = int(os.getenv("JOB_RETENTION_MINUTES", "60"))  # results are deleted after this
