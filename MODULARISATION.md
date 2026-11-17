# Modularisation and independence from hyperscalers

This note explains how the JE Validator code is organised, and why it doesn't
depend on any hyperscaler (AWS, Google Cloud, Azure), container platform or
cloud service. For setup and use, see [README.md](README.md).

## Goals

1. **Modular:** each module does one job and can be read, tested or replaced
   on its own.
2. **Independent:** the core runs on one ordinary machine with Python. It needs
   no cloud account, container, Kubernetes or managed service. Cloud model
   providers are optional plug-ins: the core never imports them.

## Package layout

All code is in one Python package, `je_validator/`:

| Module | Job |
|---|---|
| `config.py` | Every shared setting, read once from `.env` |
| `cli.py`, `__main__.py` | One entry point: `python -m je_validator validate \| templates \| serve` |
| `report.py` | The command line's text report |
| `je/template.py` | What a JE template is (column and header mapping) |
| `je/store.py` | Where templates are kept: a SQLite file on disk |
| `je/reader.py` | Reads a JE Excel with a template |
| `documents/` | Supporting documents as text, one module per format: `pdf.py`, `image.py`, `excel.py`, `word.py`, `text.py`; `pictures.py` reads pictures inside typed documents by OCR |
| `ocr/` | OCR for scans and photos; `paddle.py` runs PaddleOCR on the machine |
| `llm/base.py` | The `Provider` interface every model provider implements |
| `llm/__init__.py` | The provider registry: loads the provider `LLM_PROVIDER` names |
| `llm/ollama.py` | Default provider: a local model through Ollama |
| `llm/claude.py`, `vertex.py`, `bedrock.py` | Optional providers: Claude on the Anthropic API, Google Vertex AI and Amazon Bedrock |
| `ocr/vl.py` | PaddleOCR-VL for table, seal, chart and formula zones; its reading kept only where PP-OCR read the same amounts |
| `documents/layout.py` | Orders a page's text by its zones (XY-cut) and keeps headings with their values; pure geometry |
| `markers.py` | The header lines readers add to a document's text (`[Page 1 - ...]`, `[Zone 3 - table]`) |
| `limits.py` | Size, page and pixel limits on untrusted files |
| `fingerprint.py` | SHA-256 fingerprints of the files a result is based on |
| `privacy/mask.py` | Replaces personal data with consistent placeholders |
| `privacy/minimise.py` | Keeps only the JE fields and document lines the checks need |
| `validation/review.py` | The checks, and the reply schema a model must follow |
| `validation/amounts.py` | Tags every amount in the documents' text |
| `validation/prompt.py` | What the model is told |
| `validation/checks.py` | Checks the model's review in code |
| `validation/vendors.py` | Checks in code that the JE's vendors are named in the documents |
| `validation/references.py` | Checks in code that the JE's references are in the documents |
| `validation/currency.py` | Checks in code that the documents are in the JE's currency |
| `validation/dates.py` | Checks in code that the documents' dates fall in the JE period |
| `validation/pipeline.py` | Reads everything, then asks the models in turn |
| `web/app.py` | The upload page and its API |
| `web/jobs.py` | Runs validations as background jobs; holds their page images for the viewer until they expire |
| `web/auth.py` | The page's password |
| `evaluation/` | Measures accuracy on a synthetic gold set: `goldset.py`, `render.py`, `evaluate.py`, `stats.py` |

## How the modules depend on each other

Each layer depends only on the layers below it:

```
cli / web            entry points
   │
validation           pipeline → prompt, checks, amounts, review
   │
   ├── privacy       mask, minimise (before anything reaches the model)
   ├── je            reader → store → template
   ├── documents     pdf, image → ocr       (limits checked first)
   └── llm           Provider interface + registry
                         │  (imported only when chosen)
                         ├── ollama.py   ── ollama client (local)
                         └── claude.py   ── anthropic SDK  (optional)
                               ├── vertex.py   (Google Cloud, optional)
                               └── bedrock.py  (AWS, optional)
config               read by every layer
```

Rules the code follows:

- **The validation code calls no vendor's API.** `validation/pipeline.py` sees
  only the `Provider` interface: `prepare(models)` and
  `complete(model, system, prompt, schema)`.
- **Vendor libraries are imported in one place only**, inside that vendor's
  provider module, and that module is imported only when `LLM_PROVIDER` names
  it. If `anthropic`, `google-auth` or `boto3` isn't installed, nothing else
  is affected.
- **Errors are vendor-neutral.** Each provider turns its SDK's errors into a
  plain `ValueError` with a readable message. The web page and the command
  line handle only that, so they don't need to know which provider ran.
- **Each provider reads its own settings** (`OLLAMA_NUM_CTX`, `MAX_TOKENS`,
  `VERTEX_PROJECT_ID`, `AWS_REGION`). `config.py` holds only shared settings
  and names no cloud.
- **The command line imports only what a command needs**, so
  `templates list` doesn't load the web server or the validation code.

## Independence from hyperscalers and the cloud

| Concern | What the code uses | Cloud needed? |
|---|---|---|
| Validation model | Ollama, on this machine or your own server (`OLLAMA_HOST`) | No |
| OCR | PaddleOCR, on this machine (CPU or GPU) | No |
| JE templates | A SQLite file (`templates.db`) | No |
| Web page | FastAPI and Uvicorn, on `127.0.0.1:8000` | No |
| Background jobs | A thread in the same process | No; no queue service |
| Configuration | A `.env` file | No; no secrets manager |
| Packaging | A Python venv and `requirements.txt` | No; no container image |

`requirements.txt` holds only what the core needs. The Anthropic, Google and
AWS packages are in a separate, optional `requirements-claude.txt`.

**Internet access is needed only once**, to download the Ollama model
(`ollama pull`) and the PaddleOCR model weights (on the first scan). After
that, the validator runs offline.

### Optional cloud providers

Claude is still available as an opt-in. To use it, set `LLM_PROVIDER` and
install that provider's packages:

| `LLM_PROVIDER` | Runs on | Install |
|---|---|---|
| `ollama` (default) | Your machine or server | included |
| `anthropic` | Anthropic's Claude API | `requirements-claude.txt` |
| `vertex` | Google Vertex AI | `anthropic[vertex]` |
| `bedrock` | Amazon Bedrock | `anthropic[bedrock]` |

Removing a cloud provider completely means deleting its module and its line in
`PROVIDERS` in `llm/__init__.py`. Nothing else refers to it.

### Kaggle notebook

`kaggle/je_validator_kaggle.ipynb` runs the app on Kaggle's free GPUs behind a
Cloudflare tunnel. It is an optional way to run the app, kept outside the
package; the code doesn't depend on it. It uses only sample or made-up files.

## Extending the code

**A new model provider** (for example another local runtime or an in-house
model gateway):

1. Create `je_validator/llm/<name>.py` with a subclass of `Provider`. Set
   `default_model` (and, if wanted, `default_escalation_model`), and implement:
   - `prepare(models)`: connect, and raise `ValueError` if a model can't be
     used;
   - `complete(model, system, prompt, schema)`: return a reply that matches
     `schema` and the name of the model that answered, raising `ValueError`
     on failure.
2. Add it to `PROVIDERS` in `je_validator/llm/__init__.py`.
3. Select it with `LLM_PROVIDER=<name>`.

**A new document format:** create a module in `je_validator/documents/` with
`read(path) -> (text, methods)`, and list its suffix in `READERS` in
`documents/__init__.py`.

**Another OCR engine:** implement `read_image(image) -> (text, engine name)`
and import it in `je_validator/ocr/__init__.py` instead of PaddleOCR's.

**Another template store:** replace the four functions in `je/store.py`
(`list_templates`, `get_template`, `save_template`, `delete_template`).

## How the refactor was checked

The refactor didn't change the results:

- A regression script ran the old and new code on the same sample files and
  compared the full output; it was identical. It covered reading both sample
  JEs, every document type (including an OCR'd scan), amount tagging, the
  full validation with a stand-in model (with and without escalation), and
  the web API (templates, jobs, password, errors).
- Separate checks confirmed that the core loads no Anthropic, Google or AWS
  library, that each provider keeps its previous default models and call
  options, and that a missing optional package gives a clear install message.
- Not checked: live calls to Ollama or Claude.

## Changes for existing users

| Before | Now |
|---|---|
| `python app.py` | `python -m je_validator serve` |
| `python validator.py --je …` | `python -m je_validator validate --je …` |
| `python je_templates.py add …` | `python -m je_validator templates add …` |
| `CLAUDE_PLATFORM=local \| anthropic \| vertex \| bedrock` | `LLM_PROVIDER=ollama \| anthropic \| vertex \| bedrock` |
| `CLAUDE_MODEL`, `BEDROCK_MODEL_ID`, `LOCAL_MODEL` | `LLM_MODEL` |
| `ESCALATION_MODEL`, `LOCAL_ESCALATION_MODEL` | `LLM_ESCALATION_MODEL` |
| `LOCAL_NUM_CTX` | `OLLAMA_NUM_CTX` |
| Default model: Claude API | Default model: Ollama (local) |
