"""
Upload page for the extractor: drop a JE and/or supporting documents, get back
the key details of each.

    python app.py            -> http://127.0.0.1:8000
"""

import base64
import tempfile
from pathlib import Path
from typing import List

import anthropic
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from extractor import extract_files

app = FastAPI(title="Document Extractor")
INDEX = Path(__file__).parent / "static" / "index.html"


class UploadedFile(BaseModel):
    name: str
    content: str  # base64


class ExtractRequest(BaseModel):
    files: List[UploadedFile]


@app.get("/")
def index():
    # no-cache: the browser must re-check the page, so an old copy is never shown.
    return FileResponse(INDEX, headers={"Cache-Control": "no-cache"})


@app.post("/extract")
def extract(request: ExtractRequest):
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for f in request.files:
            # Path(...).name drops any directory part, so a name cannot escape tmp.
            path = Path(tmp) / Path(f.name).name
            path.write_bytes(base64.b64decode(f.content))
            paths.append(path)
        try:
            return extract_files(paths)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except anthropic.APIError as e:
            raise HTTPException(status_code=502, detail=f"Claude API error: {e}")
        except Exception as e:
            # Anything else still reaches the page as a readable message, not a bare 500.
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
