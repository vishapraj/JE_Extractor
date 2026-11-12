# JE Validator

Upload a journal entry (JE) Excel and its supporting documents. The tool reads
them, then checks the JE against the documents and reports what is supported,
what doesn't match, and what is missing. It runs on its own; it doesn't use
anything from MJE_Agentic_Solution.

## How it works

```
JE Excel ──template from the database──▶ JE header + lines + totals
Supporting documents:
  Excel ─────────── pandas (every sheet)
  Word .docx ────── python-docx (paragraphs and tables, in order)
  CSV / TXT ─────── read as text
  PDF ───────────── page by page: pdfplumber for pages with a text layer,
                    OCR for scanned pages
  Images ────────── OCR (every page of a multi-page TIFF)
                    OCR = PaddleOCR PP-OCRv6 on this machine
                    (PaddleOCR-VL-1.6 for complex pages is optional, for a GPU machine)
                              │
                              ▼
          Claude Haiku 5.5 (or a local model) compares the JE with the documents' text
                              │
                              ▼
          checks, with amounts and debit/credit balance verified again in code
                              │
                 confidence below the threshold?
                              ▼
          Claude Sonnet 5.5 reviews again ── still below? ──▶ flagged for human review
```

No LLM reads the files; it only sees the text the libraries and OCR produced.
The page shows that text for each document, so a reviewer can see what the
model was given.

## The checks

| Check | Done by |
|---|---|
| Debits equal credits | code |
| Every document could be read | code |
| Each JE line's amount is backed by a document amount | model finds the match, code compares the amounts (`AMOUNT_TOLERANCE`, default 0.01) |
| Vendors and references (invoice, PO, JE reference) match | model |
| Document dates fall in the JE period (or the JE explains why not) | model |
| An approval exists and covers the JE total | model |
| The documents' currency matches the JE | model |

Each check is PASS, FAIL or CANNOT_VERIFY (the documents don't show what's
needed). Each line is SUPPORTED, MISMATCH or NOT_FOUND. The overall result is
FAIL if anything failed, NEEDS_REVIEW if anything couldn't be verified, and PASS
otherwise. The model is told to use only what the files show and never to guess.

## How amounts are matched

The model never writes an amount. Before the documents go to it, code finds
every amount in their text and tags it, for example
`Cloud hosting October 2026   1,800.00 [#4]`. Dates, years and codes such as
`INV-7781` are left untagged. For each JE line, the model only says which tags
belong to it. Code then looks up the real values, adds them up, compares them
with the JE amount, and decides SUPPORTED or MISMATCH. The evidence shown is
the document's own line of text; the model's reason is shown underneath it as
"Model: …".

So a model can't report the JE's figure as the document's. Before this change,
a small local model sometimes did exactly that. It can still pick the wrong
amount, but then the real amount and text are on screen for the reviewer.

## Confidence and escalation

The model gives every line match and every check a confidence from 0 to 100:
how sure it is that the status is right. It is told to score lower for OCR
text, amounts it had to add up or infer, and documents that might belong to
another transaction. Code can also pull the score down to 0:

- the model said a line matched, but the amounts don't agree when code
  compares them;
- the model said a line matched but gave no amount;
- the model pointed at an amount tag that doesn't exist. The line is then
  marked **UNVERIFIED**;
- the model left out a line or a check.

Checks done in code have confidence 100.

**The review's confidence is that of its least confident item**, so one
uncertain line is enough to escalate:

1. Claude Haiku 5.5 reviews first.
2. If its confidence is below `CONFIDENCE_THRESHOLD` (default 80), Claude
   Sonnet 5.5 (`ESCALATION_MODEL`) does a fresh review, and its result
   replaces Haiku's.
3. If Sonnet's confidence is also below the threshold, the result is flagged
   **human review needed**. The items below the threshold are shown in red.

The confidence is about the review, not about the JE. A review can be
confident that a JE fails. The result shows each model's score, for example
`claude-haiku-5-5: 62 → claude-sonnet-5-5: 91`. With a local model, set
`LOCAL_ESCALATION_MODEL` (for example `qwen2.5:14b`) as the second reviewer.
Without it, low confidence goes straight to human review. If none of
the documents can be read, no model runs and the result goes to human review.

A model's own confidence is only a rough signal. Before relying on the
threshold, check it against JEs whose answer you already know, and adjust it.

On the Claude API, Sonnet runs with server-side refusal fallback: if Sonnet
declines, the API retries on another model in the same call, and the result
names the model that answered. Haiku 5.5 doesn't support this.

## JE templates

JE Excel layouts differ between teams, so each layout is described by a template
stored in a SQLite database (`templates.db`, or the path in `TEMPLATES_DB`). When
you upload a JE, the tool uses the template you pick or, on "Auto-detect", the
template that finds the most of its columns in the file.

A template is JSON. See [sample_templates/](sample_templates/) for two examples:
one with a label block at the top and Debit/Credit columns, and one SAP-style
export with a signed amount, a D/C indicator, and header details repeated on
every row.

```json
{
  "name": "standard",
  "description": "shown in the template list",
  "sheet": null,
  "columns": {
    "account": ["GL Account", "Account"],
    "debit": "Debit",
    "credit": "Credit",
    "description": ["Line Description", "Description"]
  },
  "header": {
    "je_number": {"label": "JE Number"},
    "period": {"cell": "B4"},
    "currency": {"column": "Currency"}
  }
}
```

- **columns** maps a line field to its column header text in the file. You can
  give several alternatives; case and extra spaces are ignored. The header row
  is found automatically within the first 50 rows. `account` is required, plus
  either `debit` and `credit` or `amount`. With `amount`, add `dc_indicator`
  and `debit_indicators` (default `D, DR, DEBIT, S, 40`) for a D/C column;
  without one, a positive amount is a debit. Amounts like `1,234.50`,
  `(500.00)` and `500.00-` are understood. Rows without an account or amount,
  such as totals and notes, are skipped.
  Line fields: line_no, account, account_description, cost_center, debit,
  credit, amount, dc_indicator, currency, tax_code, vendor, description,
  reference, document_date.
- **header** says where each JE header detail is: `label` is a cell with that
  text, and the value is to its right or below it (or after a colon in the same
  cell). `cell` is a fixed cell like `B4`. `column` takes the value from the
  first line of that column. `label` and `cell` can take `"sheet"` for a
  different sheet.
  Header fields: je_number, company_code, entity_name, je_type, period,
  posting_date, document_date, currency, reference, description, prepared_by,
  approved_by.
- **sheet** limits the search to one sheet; `null` searches all of them.

Manage templates from the command line:

```
python je_templates.py add sample_templates/standard.json sample_templates/sap_export.json
python je_templates.py list
python je_templates.py show standard
python je_templates.py remove standard
```

or in the *JE templates* section of the web page, which lists, uploads (as a
JSON file) and deletes them. The page uses the web API: `GET /templates`,
`POST /templates` (the JSON above), `DELETE /templates/{name}`.

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # then fill in one platform block
.venv\Scripts\python je_templates.py add sample_templates/standard.json sample_templates/sap_export.json
```

### Validation model

Set `CLAUDE_PLATFORM` in `.env`:

- `anthropic`: the Claude API, with `ANTHROPIC_API_KEY`
- `vertex`: Claude on Google Vertex AI
- `bedrock`: Claude on Amazon Bedrock
- `local`: a model on this machine through [Ollama](https://ollama.com). It
  needs no key, and nothing leaves the machine. Install Ollama, then run
  `ollama pull qwen2.5:7b`. It is slower and less accurate than Claude, so
  check its results.

The default model is **Claude Haiku 5.5** (`claude-haiku-5-5`;
`anthropic.claude-haiku-5-5` on Bedrock). It is called with structured outputs,
so every reply matches a fixed schema. One call checks the whole JE. A second
call, to Claude Sonnet 5.5, is made only when Haiku's confidence is low.

### OCR setup

OCR is only needed for scanned PDFs and images; everything else works without
it. PaddleOCR supports **Python 3.9 to 3.13**, so it needs a venv made with one
of those versions, not 3.14. If Python 3.13 isn't installed, the Python install
manager can add it for your user only, with no admin rights:
`pymanager install 3.13`.

```
py -3.13 -m venv .venv       # or: %LOCALAPPDATA%\Python\pythoncore-3.13-64\python.exe -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install paddlepaddle==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
.venv\Scripts\python -m pip install -r requirements-ocr.txt
```

If the project is in OneDrive, consider creating the venv outside it (for
example `%USERPROFILE%\.venvs\je-validator`, and use that folder's
`Scripts\python` below): PaddlePaddle is large, and OneDrive would sync
thousands of library files.

PP-OCRv6 downloads its models (to `%USERPROFILE%\.paddlex`) the first time a
scan is read, so that first scan is slow. On a company network that inspects
HTTPS, Python is made to trust the Windows certificate store (the `truststore`
package); without that, the download fails with "No available model hosting
platforms detected". `OCR_MODE` in `.env` chooses the engine:

- `ppocr` (default): always PP-OCRv6. `OCR_MODEL_SIZE` picks its size:
  `tiny` (about 1 s per A4 page on a laptop CPU), `small` (default, about 2 s)
  or `medium` (most accurate, about 35 s on a CPU; use it on a GPU).
- `auto`: a small layout model looks at each page and sends pages with tables,
  forms, stamps or charts to PaddleOCR-VL-1.6, and everything else to PP-OCRv6.
- `vl`: always PaddleOCR-VL-1.6.

`auto` and `vl` also need `pip install "paddleocr[doc-parser]>=3.7.0"`. On a CPU,
PaddleOCR-VL-1.6 (0.9B parameters) can take minutes per page, so use them once
a GPU machine is available, with `OCR_DEVICE=gpu:0`. PP-OCRv6 returns plain
lines of text, so table columns in a scan come through as text spaced across
each line, not as a table.

## Run

Web page:

```
.venv\Scripts\python app.py         # open http://127.0.0.1:8000
```

Drop the JE Excel in the first box and the supporting documents in the second,
then click Validate.

Command line:

```
.venv\Scripts\python validator.py --je JE.xlsx invoice.pdf bank.pdf approval.docx
```

This prints the report and writes `<JE name>_validation.json` next to the JE.
Add `--template NAME` to skip auto-detection.

## Running on Kaggle (free GPU)

[kaggle/je_validator_kaggle.ipynb](kaggle/je_validator_kaggle.ipynb) runs the
web page on Kaggle's free GPUs, with PaddleOCR on one T4 and Ollama on the
other. You open the page in your own browser and upload JEs, documents and
templates there, the same as on your laptop. Kaggle doesn't open ports, so the
notebook connects the page to a free Cloudflare tunnel. That gives it a random
`https://….trycloudflare.com` address, and a password keeps others out. Kaggle
gives about 30 GPU hours a week.

**Use sample or made-up files only:** Kaggle and Cloudflare are outside
Genpact.

1. **Zip the code.** In PowerShell, in this folder (`tar` keeps the folder
   paths that Kaggle's Linux needs; `Compress-Archive` doesn't):
   ```
   tar.exe -a -c -f $env:USERPROFILE\Downloads\je-validator-code.zip app.py je_templates.py ocr.py readers.py validator.py requirements.txt requirements-ocr.txt sample_templates static
   ```
2. **Upload it as a private dataset.** On kaggle.com go to *Datasets → New
   Dataset*, upload the zip (Kaggle unzips it), name it `je-validator-code`,
   and leave it **Private**. Repeat this whenever the code changes. Once the
   branch is on GitHub, you can set `GIT_URL` in the notebook instead.
3. **Import the notebook.** Go to *Code → New Notebook → File → Import
   Notebook*, choose `kaggle/je_validator_kaggle.ipynb`, then *Add Input* the
   dataset.
4. **Notebook settings:** *Accelerator: GPU T4 x2* and *Internet: On*. The
   P100 is too old for PaddlePaddle, and turning Internet on needs a
   phone-verified account.
5. **Run All.** The first start takes about 10–15 minutes to install packages
   and download the models. The fifth section prints the page's address and
   password. Any user name works.
6. **Leave the notebook tab open** while you use the page. The last cell keeps
   the server running and shows its log. When you're done, stop that cell and
   then the session, so it doesn't use up GPU hours.

The settings cell at the top chooses the models (default `qwen2.5:7b`,
escalating to `qwen2.5:14b`), the OCR mode (default `auto`, so
PaddleOCR-VL-1.6 handles pages with tables) and the password. Leave the
password empty to get a new random one each run.

Everything on the Kaggle machine is lost when the session ends, including
templates you added on the page. Keep your template JSON files on your own
machine and upload them again next time.

### Password and background jobs

These work the same on your laptop. Set `APP_PASSWORD` in `.env` and the
browser asks for it, which is useful whenever the page can be reached by
anyone but you. A validation runs as a background job that the page checks
every two seconds, so a run that takes minutes doesn't hit a timeout. Jobs run
one at a time, and results are kept in memory until the server stops.

## Limits (proof of concept)

- No PII masking. Don't send production data outside an approved environment;
  use `CLAUDE_PLATFORM=local` to keep everything on the machine.
- The JE must be Excel. Old Word `.doc` files are not read; save them as
  `.docx`.
- The whole JE and every document go to the model in one call. Very long
  documents may need a larger `MAX_TOKENS`, or `LOCAL_NUM_CTX` for a local
  model.
- OCR quality decides what the model can match. Check the "Text the model saw"
  section for scanned documents.
