"""
Validates a journal entry (JE) Excel against its supporting documents.

The JE is read with a template (je_validator/je) and the documents by their own
libraries or OCR (je_validator/documents); no LLM reads files. A model then
compares the JE with the documents' text (prompt.py, review.py), and the
arithmetic is checked again in code (amounts.py, checks.py). Every match and
rule gets a confidence score; when the review's score is below
CONFIDENCE_THRESHOLD, a second model reviews it again, and if its score is low
too, the result is flagged for human review (pipeline.py).
"""
