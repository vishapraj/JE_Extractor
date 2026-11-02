# Document Extractor

Upload a journal entry (JE) and/or its supporting documents, and Claude
Sonnet extracts the key details from each file. It runs on its own; it doesn't
use anything from MJE_Agentic_Solution.

Accepted formats: PDF, Word (.docx), Excel (.xlsx, .xls, .xlsm, .xlsb), CSV,
TXT, and images (PNG, JPG, WEBP, GIF, TIFF, BMP). TIFF and BMP are converted
to PNG before sending, and every page of a multi-page TIFF is read. Old Word
`.doc` files are not supported; save them as `.docx`. You can upload
one file or several at once; each is read separately.

## What it extracts

Claude first decides what each file is, then fills that type's fields. A file
whose name starts with `JE_` is treated as the JE.

| Type | Details |
|---|---|
| Journal entry | company code, entity, JE type, period, posting/document date, currency, reference, description, prepared by; every line (account, description, cost centre, debit, credit, tax code, vendor, description, reference) |
| Invoice | invoice number, vendor, bill-to, invoice/due date, PO number, currency, net/tax/gross amount, line items |
| Bank statement | bank, account holder, account number, currency, period, opening/closing balance, every transaction (date, description, reference, debit, credit, balance) |
| Calculation | title, period, currency, total, each row |
| Approval | approver, date, approved amount, currency, reference, comments |
| Other | a summary and the important details as label/value pairs |

Every file also gets a one-sentence summary. Any other important details
Claude finds, such as payment terms or signatures, are listed as extra
label/value pairs. Anything the file doesn't show comes back empty, because
Claude is told never to guess.

Claude is called through Anthropic's official Python SDK with structured
outputs, so every reply matches a fixed schema.

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # then fill in one platform block
```

This is already done in this folder. You only need to fill in `.env`.

The tool works with the Claude API (needs an `ANTHROPIC_API_KEY`), Google
Vertex AI, or Amazon Bedrock; set `CLAUDE_PLATFORM` accordingly. The default
model is **Claude Sonnet 5.5** (`claude-sonnet-5-5`). The model must support
structured outputs, which Claude Sonnet 4.6 does not.

On the Claude API, server-side refusal fallback is switched on
(`fallbacks: "default"`). If Claude declines a file, the API retries it on a
fallback model inside the same call. On Vertex and Bedrock a declined file
shows an error instead.

## Running fully locally (no key)

Instead of Claude, the tool can use an open-source model running on this
machine through [Ollama](https://ollama.com). It doesn't need a key, costs
nothing, and files never leave the machine.

1. Install Ollama from ollama.com. If your laptop blocks installs, ask IT.
2. Download a model that can read images (about 6 GB):
   ```
   ollama pull qwen2.5vl:7b
   ```
3. In `.env`, set `CLAUDE_PLATFORM=local`. `LOCAL_MODEL` and `LOCAL_NUM_CTX`
   are optional; see `.env.example`.

Local models can't read PDFs directly. A PDF with a text layer is sent as text;
a scanned PDF is sent as page images. Large images are shrunk to 1600 px on the
longest side. Files are read one at a time.

Expect it to be slower and less accurate than Claude. On a laptop without a
graphics card, a scanned page or photo can take several minutes. Check the
values before relying on them.

## Run

Web page:

```
.venv\Scripts\python app.py         # open http://127.0.0.1:8000
```

Command line:

```
.venv\Scripts\python extractor.py <file> [<file> ...]   # prints the details, writes <name>_extracted.json next to each file
```

## Limits (proof of concept)

- No PII masking. Don't send production data outside an approved environment.
- One Claude call per file. A very long bank statement may exceed `MAX_TOKENS`;
  that file then shows an error saying so.
- Image size is limited by the Claude API (a few MB per image). Shrink large photos first.
- Only `.docx` is read, not the older `.doc` format.
