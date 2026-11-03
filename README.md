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
                    OCR = PaddleOCR on this machine:
                      PP-OCRv6 for ordinary pages,
                      PaddleOCR-VL-1.6 for pages with tables, forms, stamps or charts
                              │
                              ▼
          Claude Haiku 5.5 (or a local model) compares the JE with the documents' text
                              │
                              ▼
          checks, with amounts and debit/credit balance verified again in code
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

or through the web API: `GET /templates`, `POST /templates` (the JSON above),
`DELETE /templates/{name}`.

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
so every reply matches a fixed schema. One call checks the whole JE.

### OCR setup

OCR is only needed for scanned PDFs and images; everything else works without
it. PaddleOCR supports **Python 3.9 to 3.13**, so it needs a venv made with one
of those versions, not 3.14:

```
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install paddlepaddle==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
.venv\Scripts\pip install -r requirements-ocr.txt
```

The models download on first use. `OCR_MODE` in `.env` chooses the engine:

- `auto` (default): a small layout model looks at each page and sends pages
  with tables, forms, stamps or charts to PaddleOCR-VL-1.6, and everything else
  to PP-OCRv6.
- `ppocr`: always PP-OCRv6. This is much faster; use it on a laptop without a
  GPU.
- `vl`: always PaddleOCR-VL-1.6.

On CPU, PaddleOCR-VL-1.6 (0.9B parameters) can take minutes per page. Set
`OCR_DEVICE=gpu:0` once a GPU machine is available.

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
