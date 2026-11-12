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
          a model (local through Ollama by default, or Claude) compares the JE
          with the documents' text
                              │
                              ▼
          checks, with amounts and debit/credit balance verified again in code
                              │
                 confidence below the threshold?
                              ▼
          a second model reviews again ── still below? ──▶ flagged for human review
```

No LLM reads the files; it only sees the text the libraries and OCR produced.
The page shows that text for each document, so a reviewer can see what the
model was given.

## Code layout

Everything is in the `je_validator` package. Each module does one job, and
nothing in it depends on a cloud, a hyperscaler or a container: by default it
runs entirely on one machine (Python, Ollama, PaddleOCR, a SQLite file).

```
je_validator/
  config.py          every shared setting, read once from .env
  limits.py          size, page and pixel limits on untrusted files
  cli.py             python -m je_validator validate | templates | serve
  report.py          the command line's text report
  je/
    template.py      what a JE template is
    store.py         where templates are kept (SQLite)
    reader.py        reads a JE Excel with a template
  documents/         supporting documents as text, one module per format:
                     pdf.py, image.py, excel.py, word.py, text.py
  ocr/               OCR for scans and photos (paddle.py: PaddleOCR)
  llm/               model providers, behind one interface (base.py):
    ollama.py        default; local, no cloud
    claude.py        optional: the Anthropic API
    vertex.py        optional: Claude on Google Vertex AI
    bedrock.py       optional: Claude on Amazon Bedrock
  privacy/           runs before the model sees anything (see Personal data)
    mask.py          replaces personal data with placeholders
    minimise.py      keeps only the values the checks need
  validation/
    review.py        the checks, and the reply a model must give
    amounts.py       tags every amount in the documents' text
    prompt.py        what the model is told
    checks.py        the model's review, checked in code
    vendors.py       vendor names, checked in code
    pipeline.py      read everything, then ask the models in turn
  web/
    app.py           the upload page and its API
    jobs.py          validations as background jobs
    auth.py          the page's password
    static/index.html
  evaluation/        measures accuracy on a gold set (see Measuring accuracy)
    goldset.py       makes the synthetic gold set
    render.py        writes its files
    evaluate.py      runs and scores it
    stats.py         confidence intervals, McNemar's test
```

The validator only talks to a model through `llm/base.py`'s `Provider`
interface, and a provider's module is imported only when `LLM_PROVIDER` names
it, so the Claude, Google and AWS packages aren't needed unless you choose
them. To add a provider (for example another local runtime or an in-house
gateway), subclass `Provider` in a new module under `llm/` and list it in
`PROVIDERS` in `llm/__init__.py`. A new document format is a module with a
`read(path)` function, listed in `documents/__init__.py`.

## The checks

| Check | Done by |
|---|---|
| Debits equal credits | code |
| Every document could be read | code |
| Each JE line's amount is backed by a document amount | model finds the match, code compares the amounts (`AMOUNT_TOLERANCE`, default 0.01) |
| Every vendor in the JE is named in the documents | code (see below) |
| References (invoice, PO, JE reference) match | model |
| Document dates fall in the JE period (or the JE explains why not) | model |
| An approval exists and covers the JE total | model |
| The documents' currency matches the JE | model |

Each check is PASS, FAIL or CANNOT_VERIFY (the documents don't show what's
needed). Each line is SUPPORTED, MISMATCH or NOT_FOUND. The overall result is
FAIL if anything failed, NEEDS_REVIEW if anything couldn't be verified, and PASS
otherwise. The model is told to use only what the files show and never to guess.

## How vendors are checked

Vendor names are compared in code (`validation/vendors.py`), not by the
model: a small model passed "Gbx Ltd" in the JE against "Globex Ltd" on the
invoice. Each vendor the JE names must appear in a document, ignoring case,
punctuation and legal forms (Ltd, Limited, LLP, Inc, GmbH, ...), so "Globex
Ltd" matches "GLOBEX LIMITED". A name that is close but not the same (an OCR
misreading such as "Glob3x Ltd", or "Globex UK Ltd") passes with confidence
70, which sends it for a second look. Anything else fails, and the line's
issues say which name the documents show instead.

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

1. `LLM_MODEL` reviews first.
2. If its confidence is below `CONFIDENCE_THRESHOLD` (default 80),
   `LLM_ESCALATION_MODEL` does a fresh review, and its result replaces the
   first one.
3. If the second model's confidence is also below the threshold, the result is
   flagged **human review needed**. The items below the threshold are shown in
   red.

The confidence is about the review, not about the JE. A review can be
confident that a JE fails. The result shows each model's score, for example
`qwen2.5:7b: 62 → qwen2.5:14b: 91`. With Ollama, set `LLM_ESCALATION_MODEL`
(for example `qwen2.5:14b`) to get a second reviewer. Without it, low
confidence goes straight to human review. The Claude providers escalate from
Claude Haiku 5.5 to Claude Sonnet 5.5 by default. If none of the documents can
be read, no model runs and the result goes to human review.

A model's own confidence is only a rough signal. Before relying on the
threshold, check it against JEs whose answer you already know, and adjust it
(see *Measuring accuracy*).

## Personal data

Supporting documents carry personal data that no check needs. Every
validation passes the JE and the documents' text through `privacy/` before
anything reaches the model or is stored:

1. **Masking** (`privacy/mask.py`) replaces personal data with placeholders:
   emails, IBANs (checksum-verified), card numbers (Luhn-verified), phone
   numbers, sort codes and account numbers, street addresses, postcodes, SSN
   and NI numbers, and people's names. Names are found when the JE names them
   (preparer, approver), after a label ("Approved by:", "Contact:", "Attn:",
   "Signed:"), in a table column headed Approver, Name, Employee, ..., or with
   a title or initial ("Mr Smith", "M. Jones"). The same value always gets the
   same placeholder (`[PERSON-A]`), so the model can still see that two
   documents name the same approver. The placeholder table exists only in
   memory during the validation.
2. **Minimising** (`privacy/minimise.py`) keeps only what the checks use. The
   JE keeps its number, entity, period, dates, currency, reference,
   description and its lines' accounts, texts, vendors, references and
   amounts; preparer, cost centre and tax code are dropped. A document line is
   kept only if it holds an amount, a date, a reference, a currency, the
   vendor or the JE number, or a keyword such as *invoice*, *total* or
   *approved*. Addresses, contacts, bank details and sign-offs are dropped,
   even when a name in them wasn't recognised.

Vendor names stay readable: they are companies, and the vendor check needs
them. The result, the page's "Text the model saw" and the saved
`_validation.json` hold only the protected text. This applies to every
provider, local or cloud.

The gold set plants personal data (contacts, emails, phones, bank details,
addresses, an unlabelled sign-off name, approver and preparer) in every case,
and `eval run` reports how much of it would reach the model. On the 52-case
gold set: **0 of 664** planted items reach the model (95% upper bound 0.6%),
all 201 needed amounts are kept, and the model gets 57% of the extracted text.
Rules can't recognise every name: a name in free text, with no label, title
or initial, is masked only if its line is dropped.

## Security

- The page is served on `127.0.0.1` only. Setting `APP_HOST` to anything else
  is refused unless `APP_PASSWORD`, `TLS_CERT_FILE` and `TLS_KEY_FILE` are set,
  so the password and documents travel over HTTPS.
- Uploads are limited (`MAX_FILE_MB`, `MAX_DOCUMENTS`), and files are checked
  before parsing against decompression bombs: what an Excel or Word file
  unpacks to (`MAX_UNZIPPED_MB`), PDF pages and TIFF frames (`MAX_PAGES`), and
  image and rendered-page size (`MAX_IMAGE_MEGAPIXELS`).
- Results stay in memory only, and are deleted `JOB_RETENTION_MINUTES` (60)
  after they finish. Uploaded files are deleted when the validation ends.
- The page builds everything from text nodes, so document text can't inject
  script; SQL is parameterised; the password check is constant-time.
- Keep `.env` (API keys) out of OneDrive and other synced folders.

## Measuring accuracy

Confidence is what the model says about itself; accuracy is how often it is
actually right. The evaluation measures accuracy on a **gold set**: JEs and
documents whose right answers are known.

```
pip install -r requirements-eval.txt
python -m je_validator eval generate                       # 52 synthetic cases in goldset/
python -m je_validator eval run                            # extraction only, no model, ~1 min
python -m je_validator eval run --model qwen2.5:7b         # extraction and validation
python -m je_validator eval compare eval_results/qwen2.5_7b eval_results/qwen2.5_14b
```

The gold set is made up (invented vendors and figures, the same every time
for the same `--seed`). Each case books one invoice as a JE, in either
template layout, with the invoice as a PDF or a scan. 16 cases are clean, and
the others each have one planted error: a wrong amount, vendor, reference or
date, a missing or too small approval, another currency, an unsupported line,
or unbalanced debits and credits. `expected.json` in each case holds the right
answers.

The report scores each stage separately:

- **Extraction:** JE template and fields read correctly, document amounts
  found (by text layer, Word, CSV, OCR), OCR character error rate.
- **Validation:** line statuses and checks right, JEs with an error stopped,
  clean JEs passed, per error type.
- **Confidence vs accuracy:** for each confidence band, the measured accuracy.
  A model whose 90+ items are right only 70% of the time is overconfident, and
  its confidence can't be used to skip human review.

Every rate has a 95% Wilson interval, which shows how much the sample size
proves (19/20 right is 95%, but only "76% to 99%"). The **gate** approves a
model only if the lower ends of the intervals for model-decided items and for
errors stopped reach `--target` (default 90%). `compare` uses McNemar's test
to say whether one model is really more accurate than another on the same
cases. A run saves each case as it finishes, so it can be stopped and resumed.

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
python -m je_validator templates add sample_templates/standard.json sample_templates/sap_export.json
python -m je_validator templates list
python -m je_validator templates show standard
python -m je_validator templates remove standard
```

or in the *JE templates* section of the web page, which lists, uploads (as a
JSON file) and deletes them. The page uses the web API: `GET /templates`,
`POST /templates` (the JSON above), `DELETE /templates/{name}`.

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # then adjust the settings you need
.venv\Scripts\python -m je_validator templates add sample_templates/standard.json sample_templates/sap_export.json
```

### Validation model

Set `LLM_PROVIDER` in `.env`:

- `ollama` (default): a model on this machine, or on your own server
  (`OLLAMA_HOST`), through [Ollama](https://ollama.com). It needs no key, and
  nothing leaves your machines. Install Ollama, then run
  `ollama pull qwen2.5:7b`. It is slower and less accurate than Claude, so
  check its results.
- `anthropic`: the Claude API, with `ANTHROPIC_API_KEY`
- `vertex`: Claude on Google Vertex AI
- `bedrock`: Claude on Amazon Bedrock

The three Claude providers are optional: install them with
`pip install -r requirements-claude.txt` (see that file for the Vertex and
Bedrock extras). `LLM_MODEL` and `LLM_ESCALATION_MODEL` choose the models; each
provider has defaults: `qwen2.5:7b` with no second reviewer for Ollama, and
Claude Haiku 5.5 escalating to Claude Sonnet 5.5 for Claude
(`anthropic.claude-…` on Bedrock; with an inference profile ARN as
`LLM_MODEL`, set a second ARN as `LLM_ESCALATION_MODEL`). Every provider is
called with a fixed reply schema, so every reply matches it. One call checks
the whole JE. A second call is made only when the first model's confidence is
low.

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
.venv\Scripts\python -m je_validator serve         # open http://127.0.0.1:8000
```

Drop the JE Excel in the first box and the supporting documents in the second,
then click Validate.

Command line:

```
.venv\Scripts\python -m je_validator validate --je JE.xlsx invoice.pdf bank.pdf approval.docx
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
   tar.exe -a -c -f $env:USERPROFILE\Downloads\je-validator-code.zip --exclude=__pycache__ je_validator requirements.txt requirements-ocr.txt sample_templates
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
  use `LLM_PROVIDER=ollama` (the default) to keep everything on the machine.
- The JE must be Excel. Old Word `.doc` files are not read; save them as
  `.docx`.
- The whole JE and every document go to the model in one call. Very long
  documents may need a larger `OLLAMA_NUM_CTX` for Ollama, or `MAX_TOKENS` for
  Claude.
- OCR quality decides what the model can match. Check the "Text the model saw"
  section for scanned documents.
