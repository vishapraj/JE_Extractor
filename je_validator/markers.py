"""Lines the document readers add to a document's text, which are not the document's own text."""

import re

# "[Page 1 - text layer]", "[Table 2]", "[Sheet: JE]", "[Image 3 - PP-OCRv6 small]", "[Zone 4 - table]"
READER_HEADER = re.compile(r"\[(?:Page \d+ - |Table \d+\]|Sheet: |Image \d+ - |Zone \d+ - )")
# The ones that start a document part (as opposed to a zone within a page)
PART_HEADER = re.compile(r"\[(?:Page \d+ - |Table \d+\]|Sheet: |Image \d+ - )")
