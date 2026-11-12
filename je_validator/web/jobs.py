"""
Validations as background jobs that the page polls, because OCR and the model
can take minutes and a tunnel or proxy may cut off long requests. A finished
job's result is deleted JOB_RETENTION_MINUTES after it ends.
"""

import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from je_validator.config import JOB_RETENTION_MINUTES
from je_validator.validation.pipeline import validate

_jobs: Dict[str, Dict[str, Any]] = {}  # kept in memory only
_finished: Dict[str, float] = {}  # job -> when it ended
_worker = ThreadPoolExecutor(max_workers=1)  # one validation at a time: OCR and the model share the machine


def submit(tmp: Path, je_path: Path, doc_paths: List[Path], template: Optional[str]) -> str:
    """Queues a validation of files in tmp, which is deleted when it ends; returns the job's id."""
    _expire()
    job = uuid.uuid4().hex
    _jobs[job] = {"status": "queued"}
    _worker.submit(_run, job, tmp, je_path, doc_paths, template)
    return job


def get(job: str) -> Optional[Dict[str, Any]]:
    _expire()
    return _jobs.get(job)


def _expire() -> None:
    cutoff = time.monotonic() - JOB_RETENTION_MINUTES * 60
    for job in [j for j, t in _finished.items() if t < cutoff]:
        _jobs.pop(job, None)
        _finished.pop(job, None)


def _run(job: str, tmp: Path, je_path: Path, doc_paths: List[Path], template: Optional[str]) -> None:
    _jobs[job] = {"status": "running"}
    try:
        _jobs[job] = {"status": "done", "result": validate(je_path, doc_paths, template)}
    except ValueError as e:  # includes the model providers' errors
        _jobs[job] = {"status": "error", "detail": str(e)}
    except Exception as e:
        # Anything else still reaches the page as a readable message.
        _jobs[job] = {"status": "error", "detail": f"{type(e).__name__}: {e}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        _finished[job] = time.monotonic()
