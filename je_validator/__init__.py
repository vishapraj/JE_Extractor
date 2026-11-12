"""
JE Validator: checks a journal entry (JE) Excel against its supporting documents.

    je/          JE templates and reading a JE Excel
    documents/   supporting documents as text, one module per format
    ocr/         OCR for scans and photos
    llm/         model providers, loaded by name (LLM_PROVIDER)
    validation/  the review: amount tagging, prompt, checks, escalation
    web/         the upload page and its API
    cli.py       python -m je_validator validate | templates | serve

Only llm/ talks to a model, and only through the Provider interface, so no
vendor or cloud SDK is imported unless its provider is chosen.
"""
