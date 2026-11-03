"""
Upload page for the validator: one JE Excel plus its supporting documents,
checked against each other.

    python app.py            -> http://127.0.0.1:8000
"""

import tempfile
from pathlib import Path
from typing import List

import anthropic
import ollama
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from je_templates import JETemplate, delete_template, list_templates, save_template
from validator import validate

app = FastAPI(title="JE Validator")
INDEX = Path(__file__).parent / "static" / "index.html"


@app.get("/")
def index():
    # no-cache: the browser must re-check the page, so an old copy is never shown.
    return FileResponse(INDEX, headers={"Cache-Control": "no-cache"})


@app.post("/validate")
def validate_upload(je: UploadFile = File(...), documents: List[UploadFile] = File(...), template: str = Form("")):
    with tempfile.TemporaryDirectory() as tmp:
        # The JE gets its own folder, so a document with the same name can't overwrite it.
        je_path = _save(Path(tmp) / "je", je)
        doc_paths = [_save(Path(tmp) / "documents", f) for f in documents]
        try:
            return validate(je_path, doc_paths, template or None)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except anthropic.APIError as e:
            raise HTTPException(status_code=502, detail=f"Claude API error: {e}")
        except ollama.ResponseError as e:
            raise HTTPException(status_code=502, detail=f"Local model error: {e}")
        except Exception as e:
            # Anything else still reaches the page as a readable message, not a bare 500.
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


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
