"""
Upload page for the validator: one JE Excel plus its supporting documents,
checked against each other.

    python app.py            -> http://127.0.0.1:8000

A validation runs as a background job that the page polls, because OCR and the
model can take minutes and a tunnel (as on Kaggle) cuts off long requests. Set
APP_PASSWORD to make the browser ask for a password, whenever the page can be
reached by anyone but you.
"""

import base64
import binascii
import os
import secrets
import shutil
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

import anthropic
import ollama
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from je_templates import JETemplate, delete_template, list_templates, save_template
from validator import validate

app = FastAPI(title="JE Validator")
INDEX = Path(__file__).parent / "static" / "index.html"
APP_PASSWORD = os.getenv("APP_PASSWORD")  # read after the imports above have loaded .env

_jobs: Dict[str, Dict[str, Any]] = {}  # kept in memory until the server stops
_worker = ThreadPoolExecutor(max_workers=1)  # one validation at a time: OCR and the model share the machine


@app.middleware("http")
async def require_password(request: Request, call_next):
    if APP_PASSWORD and not _password_ok(request.headers.get("authorization", "")):
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="JE Validator"'})
    return await call_next(request)


def _password_ok(header: str) -> bool:
    """HTTP Basic auth: any user name, the APP_PASSWORD password."""
    if not header.startswith("Basic "):
        return False
    try:
        password = base64.b64decode(header[6:]).decode("utf-8").partition(":")[2]
    except (binascii.Error, UnicodeDecodeError):
        return False
    return secrets.compare_digest(password.encode(), APP_PASSWORD.encode())


@app.get("/")
def index():
    # no-cache: the browser must re-check the page, so an old copy is never shown.
    return FileResponse(INDEX, headers={"Cache-Control": "no-cache"})


@app.post("/validate")
def validate_upload(je: UploadFile = File(...), documents: List[UploadFile] = File(...), template: str = Form("")):
    """Starts a validation; poll GET /jobs/{job} for the result."""
    tmp = Path(tempfile.mkdtemp())
    # The JE gets its own folder, so a document with the same name can't overwrite it.
    je_path = _save(tmp / "je", je)
    doc_paths = [_save(tmp / "documents", f) for f in documents]
    job = uuid.uuid4().hex
    _jobs[job] = {"status": "queued"}
    _worker.submit(_run, job, tmp, je_path, doc_paths, template or None)
    return {"job": job}


@app.get("/jobs/{job}")
def get_job(job: str):
    if job not in _jobs:
        raise HTTPException(status_code=404, detail="no such job - the server may have restarted")
    return _jobs[job]


def _run(job: str, tmp: Path, je_path: Path, doc_paths: List[Path], template: Optional[str]) -> None:
    _jobs[job] = {"status": "running"}
    try:
        _jobs[job] = {"status": "done", "result": validate(je_path, doc_paths, template)}
    except ValueError as e:
        _jobs[job] = {"status": "error", "detail": str(e)}
    except anthropic.APIError as e:
        _jobs[job] = {"status": "error", "detail": f"Claude API error: {e}"}
    except ollama.ResponseError as e:
        _jobs[job] = {"status": "error", "detail": f"Local model error: {e}"}
    except Exception as e:
        # Anything else still reaches the page as a readable message.
        _jobs[job] = {"status": "error", "detail": f"{type(e).__name__}: {e}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _save(folder: Path, upload: UploadFile) -> Path:
    folder.mkdir(exist_ok=True)
    # Path(...).name drops any directory part, so a name cannot escape the folder.
    path = folder / Path(upload.filename).name
    path.write_bytes(upload.file.read())
    return path


@app.get("/templates")
def get_templates():
    return [t.model_dump(exclude_none=True) for t in list_templates()]


@app.post("/templates")
def add_template(template: JETemplate):
    save_template(template)
    return {"saved": template.name}


@app.delete("/templates/{name}")
def remove_template(name: str):
    if not delete_template(name):
        raise HTTPException(status_code=404, detail=f"no JE template named '{name}'")
    return {"removed": name}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
