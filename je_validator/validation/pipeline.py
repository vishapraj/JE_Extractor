"""Reads the JE and documents, then asks the models in turn until one is confident enough."""

from pathlib import Path
from typing import Any, Dict, List, Optional

from je_validator import config
from je_validator.documents import read_document
from je_validator.je.reader import read_je
from je_validator.llm import Provider, load_provider
from je_validator.privacy import protect
from je_validator.validation.checks import assess
from je_validator.validation.prompt import SYSTEM, build_prompt
from je_validator.validation.review import Review

_provider: Optional[Provider] = None  # made on the first review, then reused


def validate(je_path: Path, doc_paths: List[Path], template: Optional[str] = None,
             models: Optional[List[str]] = None) -> Dict[str, Any]:
    """models: the models to try in turn; default LLM_MODEL, then LLM_ESCALATION_MODEL."""
    # From here on only the protected JE and text exist: masked, and reduced to what the checks need.
    je, documents = protect(read_je(je_path, template), [_read(p) for p in doc_paths])
    readable = [d for d in documents if "error" not in d]
    attempts = []
    if not readable:
        result = assess(je, documents, Review(documents=[], lines=[], rules=[],
                                              summary="None of the supporting documents could be read."), "none")
    else:
        provider = _get_provider()
        prompt = build_prompt(je, readable)
        # Tried in order until one is confident enough; after the last, a human reviews it.
        for model in models or _models(provider):
            review, answered_by = provider.complete(model, SYSTEM, prompt, Review)
            result = assess(je, documents, review, answered_by)
            attempts.append({"model": answered_by, "confidence": result["confidence"]})
            if result["confidence"] >= config.CONFIDENCE_THRESHOLD:
                break
    result.update(attempts=attempts, confidence_threshold=config.CONFIDENCE_THRESHOLD,
                  human_review=result["confidence"] < config.CONFIDENCE_THRESHOLD)
    return result


def _models(provider: Provider) -> List[str]:
    first = config.LLM_MODEL or provider.default_model
    second = config.LLM_ESCALATION_MODEL or provider.escalation_default(first)
    return [first] + ([second] if second else [])


def _get_provider() -> Provider:
    global _provider
    if _provider is None:
        provider = load_provider(config.LLM_PROVIDER)
        provider.prepare(_models(provider))
        _provider = provider
    return _provider


def _read(path: Path) -> Dict[str, Any]:
    try:
        return read_document(path)
    except Exception as e:  # one unreadable file must not stop the others
        return {"file": path.name, "error": str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}"}
