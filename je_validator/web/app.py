"""
Upload page for the validator: one JE Excel plus its supporting documents,
checked against each other.

    python -m je_validator serve     -> http://127.0.0.1:8000

Set APP_PASSWORD to make the browser ask for a password, whenever the page can
be reached by anyone but you. Serving beyond this machine (APP_HOST) needs a
password and HTTPS (TLS_CERT_FILE, TLS_KEY_FILE).
"""

import ipaddress
import shutil
import tempfile
from pathlib import Path
from typing import List

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from je_validator.config import (APP_HOST, APP_PASSWORD, APP_PORT, MAX_DOCUMENTS, MAX_FILE_MB, TLS_CERT_FILE,
                                 TLS_KEY_FILE)
from je_validator.je.store import delete_template, list_templates, save_template
from je_validator.je.template import JETemplate
from je_validator.web import jobs
from je_validator.web.auth import password_ok

app = FastAPI(title="JE Validator")
INDEX = Path(__file__).parent / "static" / "index.html"


@app.middleware("http")
async def require_password(request: Request, call_next):
    if APP_PASSWORD and not password_ok(request.headers.get("authorization", ""), APP_PASSWORD):
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="JE Validator"'})
    return await call_next(request)


@app.get("/")
def index():
    # no-cache: the browser must re-check the page, so an old copy is never shown.
    return FileResponse(INDEX, headers={"Cache-Control": "no-cache"})


@app.post("/validate")
def validate_upload(je: UploadFile = File(...), documents: List[UploadFile] = File(...), template: str = Form("")):
    """Starts a validation; poll GET /jobs/{job} for the result."""
    if len(documents) > MAX_DOCUMENTS:
        raise HTTPException(status_code=413, detail=f"at most {MAX_DOCUMENTS} documents per validation (MAX_DOCUMENTS)")
    tmp = Path(tempfile.mkdtemp())
    try:
        # The JE gets its own folder, so a document with the same name can't overwrite it.
        je_path = _save(tmp / "je", je)
        doc_paths = [_save(tmp / "documents", f) for f in documents]
    except HTTPException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"job": jobs.submit(tmp, je_path, doc_paths, template or None)}


@app.get("/jobs/{job}")
def get_job(job: str):
    result = jobs.get(job)
    if result is None:
        raise HTTPException(status_code=404, detail="no such job - the server may have restarted")
    return result


def _save(folder: Path, upload: UploadFile) -> Path:
    folder.mkdir(exist_ok=True)
    # Path(...).name drops any directory part, so a name cannot escape the folder.
    path = folder / Path(upload.filename).name
    limit, size = MAX_FILE_MB * 1024 * 1024, 0
    with path.open("wb") as out:
        while chunk := upload.file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise HTTPException(status_code=413, detail=f"{path.name} is larger than {MAX_FILE_MB} MB (MAX_FILE_MB)")
            out.write(chunk)
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


def serve() -> None:
    if not _local(APP_HOST) and not (APP_PASSWORD and TLS_CERT_FILE and TLS_KEY_FILE):
        raise ValueError(f"APP_HOST={APP_HOST} lets other machines reach the page: set APP_PASSWORD, TLS_CERT_FILE "
                         "and TLS_KEY_FILE too, so the password and the documents travel encrypted")
    uvicorn.run(app, host=APP_HOST, port=APP_PORT, ssl_certfile=TLS_CERT_FILE, ssl_keyfile=TLS_KEY_FILE)


def _local(host: str) -> bool:
    try:
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:  # a host name other than localhost
        return False
