# JE Validator

Upload a journal entry (JE) Excel and its supporting documents. The tool reads
them, then checks the JE against the documents and reports what is supported,
what doesn't match, and what is missing. It runs on its own; it doesn't use
anything from MJE_Agentic_Solution.

## How it works

```
JE Excel ──template from the database──▶ JE header + lines + totals
Supporting documents:
  Excel ─────────── pandas (every sheet), plus OCR for pictures in the workbook
  Word .docx ────── python-docx (paragraphs and tables, in order), plus OCR
                    for pictures in the document
  CSV / TXT ─────── read as text
  PDF ───────────── page by page: pdfplumber for pages with a text layer,
                    plus OCR for pictures placed on them; OCR for scanned pages
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

Documents that mix typed text with pictures (an invoice pasted into a Word
memo, a scan placed on a typed PDF page, a screenshot in a workbook) get both:
the typed text, and each picture's OCR text as its own block,
`[Image 2 - PP-OCRv6 small]`. Pictures under about 200 × 200 pixels (logos,
icons, signatures) are skipped, and so is a picture whose area the PDF's text
layer already covers (a searchable scan), so nothing is read twice. Vector
drawings (.emf, .wmf) can't be read; the text says so. The gold set has 35
such documents, and every amount in them is found.

### Reading pages by their layout

Invoices put blocks side by side (vendor top left, invoice details top right,
Bill to beside Ship to) and values under headings (a "Date" heading above
"28/10/2026", column headings above table cells). Reading such a page line by
line across its full width mixes the blocks. Scans and typed PDF pages are
therefore read by layout (`documents/layout.py`):

1. **Zones.** PP-DocLayout (`LAYOUT_MODEL`, default `PP-DocLayout_plus-L`)
   finds the page's zones and labels them: title, text, table, header, footer,
   seal, figure, ... It often also returns coarse duplicates: a "table" over
   the whole page, or a table of which only one column was found separately.
   A zone holding a more confident zone, or two nearly as confident, gives
   way to them, and any of its text they don't hold becomes zones of its own.
2. **Text.** PP-OCRv6 reads the lines of a scan, each with its box and
   confidence; a typed PDF page uses its own text layer (exact, no OCR), with
   the layout model run on the rendered page.
3. **Reading order** is XY-cut: the page is cut at its widest gap, horizontal
   gaps first (bands, top to bottom), then vertical ones (blocks, left to
   right), and each side the same way. So blocks side by side are read one
   after the other, and a table row by row. It needs no model, so it still
   orders a page when no layout model is set (`LAYOUT_MODEL=`).
4. **Headings travel with their values.** When a zone's first row is headings
   and later rows sit under them, each value is written with its heading:

```
[Zone 4 - text]
Invoice No | Date | PO
Invoice No: INV-7781 | Date: 28/10/2026 | PO: PO-5512
[Zone 7 - table]
Description | Qty | Unit price | Amount
Description: Cloud hosting October 2026 | Qty: 3 | Unit price: 600.00 | Amount: 1,800.00
```

Two more corrections make this hold on real output. A layout model may cut a
table without borders into side-by-side zones (a Description column, then
Qty | Price | Amount); two such zones are joined back into one table when both
start with a heading row, their rows match one for one, and amounts sit under
the headings. A table without headings may be cut into its labels (items,
Subtotal, VAT, Total) and a bare column of their amounts; such a column joins
the zone on its left that has a label without amounts at the height of each
amount, so "Subtotal | 11,802.34" stays one row. On a slightly skewed scan an
amount sits a little above or below its label and the rows overlap, so a zone
can still come out as a column of labels, then a column of amounts; an amount
left alone in its row is put back with the label on its left it overlaps
most. And PP-OCR may box words from two columns as one line
(`Invoice No Date`); a line is split where the gap between words is wider than
its height (word spaces measured 0.5-0.7 of the height, column gaps 1.4-1.7).

Each document's result also records its pages' zones (label, box, reading
order) for display.

Measured on 52 invoices laid out in blocks (`eval generate --complex 1.0`;
20 scans, 32 typed PDFs):

| | Line-by-line reader (before) | Layout reader |
|---|---|---|
| Heading read with its value, scans / typed PDFs | 0/39, 0/117 | 35/39, 117/117 |
| Item on one line with its amount | 21/21, 74/74 | 21/21, 74/74 |
| Side-by-side blocks kept apart | 9/52, 78/156 | 52/52, 156/156 |
| Amounts found, personal data reaching the model | 100%, - | 100%, 0/664 |

The four heading misses are OCR reading a date and the next field as one word
with no space (`21/04/2026PO-5878`).

### Parts named by their role

The layout model says what a zone looks like (text, table, title), not what
its data is for. `documents/roles.py` reads that from the words, so it holds on
layouts it hasn't seen, and each zone is named by its role where its words say:

| Role | Read from |
|---|---|
| `identity` | Invoice No / number / date, PO, order no, due date |
| `party` | Bill to, Ship to, Sold to, a contact, an email |
| `items` | a heading row with Description or Item and Qty, Price or Amount |
| `vat_summary` | "VAT summary / analysis", or headings with a rate and Net, VAT or Gross |
| `totals` | rows labelled Subtotal, VAT, Total, Amount due, each with its amount |
| `payment` | IBAN, sort code, SWIFT / BIC, account number |
| `approval` | Approved by, authorised by, approver, signature |

Other zones keep the layout model's label. The model then reads
`[Zone 8 - totals]`, and the code counting a JE line's amounts knows the VAT
summary's figures are totals.

The words also split blocks that geometry can't. A table without borders right
next to another block - a VAT summary beside the totals, on the same rows - has
no wider gap between the two than between its own columns, and the layout model
boxes them in ways that mix them: both in one zone read row by row across, or
the totals' labels in one zone and their amounts in the VAT summary's. The
totals' labels stand in a column (two or more, aligned, each with its amount
after it on its row, one of them a sum such as Total, none repeated); those
labels, with what follows each on its row up to its amount, are taken out of
whatever zones hold them into one `totals` zone. A VAT column in an items table
("VAT 20%" on every row, "Total" under it) repeats its label, so it stays in
its table. A part of a table the layout model boxed on its own (the VAT
summary's Gross column) is joined back as before; a title over the table
("VAT summary") no longer stops that.

### PaddleOCR-VL for complex zones, cross-checked (`OCR_MODE=hybrid`)

In hybrid mode, the zones the layout model labels table, seal, chart or
formula are also read by PaddleOCR-VL-1.6 (`ocr/vl.py`), which reads a zone
with its structure: which cell sits under which heading. VL is generative, so
it can write a number that isn't on the page or leave one out. Its reading of
a zone is therefore used only if it holds exactly the same amounts as PP-OCR's
(or a typed PDF's text layer) reading of that zone, each as often. Otherwise
the zone keeps PP-OCR's reading, and the reason is recorded: in the viewer,
hovering the zone shows "VL wrote amounts PP-OCR didn't read: 9,999.00" or
"VL left out amounts PP-OCR read: 300.00". VL can add structure, never a
figure.

The evaluation reports, per reader, the numbers read that aren't on the page
(the hallucination count) and how often VL's reading was used or rejected.

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
  fingerprint.py     SHA-256 fingerprints of the files a result is based on
  cli.py             python -m je_validator validate | templates | serve
  report.py          the command line's text report
  je/
    template.py      what a JE template is
    store.py         where templates are kept (SQLite)
    reader.py        reads a JE Excel with a template
  documents/         supporting documents as text, one module per format:
                     pdf.py, image.py, excel.py, word.py, text.py;
                     pictures.py reads pictures inside typed documents;
                     layout.py orders a page's text by its zones;
                     roles.py names each zone by what its data is for
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
    references.py    references, checked in code
    currency.py      currency, checked in code
    dates.py         dates against the JE period, checked in code
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
| Every reference in the JE (invoice number, PO, ...) is in the documents | code (see below) |
| The documents' currency matches the JE | code (see below) |
| Document dates fall in the JE period (or the JE explains why not) | code; the model judges only when the JE gives a reason (accrual, prepayment, ...) or no document has a date |
| An approval exists and covers the JE total | model finds the approved amount, code compares it with the JE total (see below) |

Each check is PASS, FAIL or CANNOT_VERIFY (the documents don't show what's
needed). Each line is SUPPORTED, MISMATCH or NOT_FOUND. The overall result is
FAIL if anything failed, NEEDS_REVIEW if anything couldn't be verified, and PASS
otherwise. The model is told to use only what the files show and never to guess.

## Vendors, references, currency and dates are checked in code

These are compared by code, not by the model. On the gold set, the small model
passed 9 of 36 planted errors at confidence 90, all of these kinds ("Gbx Ltd"
against "Globex Ltd", INV-7718 against INV-7781, a EUR invoice for a GBP JE,
an invoice dated four months before the period). Code checks them the same way
every time, and was right in all 104 gold-set cases. The model judges what
needs judgement: whether a document is an approval and names an approver, and
whether a reason the JE gives (an accrual, a prepayment) explains an
out-of-period date.

- **Approval** (`validation/checks.py`): the model points at the approved
  amount's tag in the approval document, and code compares its value with the
  JE total. In all 9 wrong approval decisions of a Kaggle run (four runs on
  the two gold sets), the 7B model read both amounts right and still compared
  them wrong: "4,837.00 covers the JE total of 7,639.19", or an approval
  equal to the JE total failed because it was set against the invoice
  instead. An approved amount below the JE total fails; one
  that covers it passes when the approval names an approver (a masked name or
  email). Only amounts in a document the model typed as an approval count; with
  none, a PASS becomes CANNOT_VERIFY, since nothing was compared.

- **Vendors** (`validation/vendors.py`): each vendor the JE names must appear
  in a document, ignoring case, punctuation and legal forms (Ltd, Limited,
  LLP, Inc, GmbH, ...), so "Globex Ltd" matches "GLOBEX LIMITED". A name that
  is close but not the same (an OCR misreading such as "Glob3x Ltd", or
  "Globex UK Ltd") passes with confidence 70, which sends it for a second
  look. Anything else fails, and the line's issues say which name the
  documents show instead. A line's status (SUPPORTED, MISMATCH, ...) is about
  its amount; a vendor or reference that doesn't match shows as a red flag
  beside it (VENDOR MISMATCH, REFERENCE MISMATCH), so a line whose amount is
  right but whose vendor is wrong doesn't read as all clear.
- **References** (`validation/references.py`): each reference the JE gives
  must appear in a document exactly, ignoring case and separators (INV-7781,
  INV 7781 and inv7781 are the same). A transposed or missing digit fails,
  with the closest reference the documents do have.
- **Currency** (`validation/currency.py`): a document that states another
  currency than the JE fails. Currencies are found by ISO code and by £, €
  and ₹ ($ and ¥ name several currencies, so they're not used). If no
  document states a currency, the check can't be verified.
- **Dates** (`validation/dates.py`): every date in the documents (28/10/2026,
  2026-10-28, 28 Oct 2026, a service month such as "July 2026") is compared
  with the JE's month. A date before the period fails; one after it is allowed
  for 62 days (payments and approvals follow the period), and lines about a
  payment or an approval aren't checked. If the JE gives a reason (accrual,
  prepayment, deferral, prior period), or no document has a date, the model
  judges instead. Slash and dot dates are read day first (05/06/2026 is
  5 June), as on UK and European documents.

A small model sometimes picks one figure twice for a JE line, which doubles
it: an invoice's "Subtotal 3,000.00" and its "Total 3,000.00"; an item and
the subtotal that contains it (on a one-item invoice they are equal); an
invoice's total and the bank payment of it; or several numbers of one table
row (its quantity, unit price and amount). None of these is two costs, so code
counts each once and notes it in the line's issues: of the numbers picked from
one row only the rightmost (the Amount column) counts - fixed by its place, not
by whether it equals the JE, so a JE that books the unit price still
mismatches; equal amounts from total lines of one document count once; a total
equal to the sum of the items picked from its document isn't added again; and
equal amounts from documents of different types (as the review typed them)
count once. Whether an amount is a total is judged by its label: in a table
with headings, the row's first cell (so items in a table with a "Total" column
are still items); elsewhere, what is written just before the amount (so
"Subtotal | 3,384.76" is a total even when a VAT summary beside it was read
into the same row). A row with a VAT rate that isn't an item row (no
Description, Qty or Item: "Rate: 0% | Net: 1,827.00 | VAT: 0.00 | Gross:
1,827.00") is the VAT summary, and its figures count as totals, as do all
figures in a zone named `vat_summary` (see *Parts named by their role*), whether
or not their row shows the rate. Counting once
always applies, so a JE that books the amount twice still shows as a mismatch.
Equal amounts that really are two costs - two item rows, or two invoices -
still add up.

A quantity isn't tagged as an amount, so the model can't take it for the
item's amount. A quantity here is a whole number alone in its table cell (or
after its heading, "Qty: 1") with money amounts after it in the row. In "1 |
2,296.00 | 2,296.00", only the two amounts get tags.

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
   memory during the validation. Values the JE itself holds (its account
   codes, references, vendors and JE number) are never masked, even where a
   rule would match them ("Account 620200" in an accrual sheet is the GL
   account, not a bank account), and an account number is never taken from
   the start of an amount.
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
and `eval run` reports how much of it would reach the model. On 52 gold-set
cases: **0 of 664** planted items reach the model (95% upper bound 0.6%),
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
  after they finish, with the page images the viewer shows. Uploaded files are
  deleted when the validation ends.
- The page builds everything from text nodes, so document text can't inject
  script; SQL is parameterised; the password check is constant-time.
- Every result records the **SHA-256 fingerprint** of the JE and of each
  supporting document as received (`fingerprint.py`; on the page under each
  file, and at the end of the command line report). Hashing the files again
  later (`Get-FileHash -Algorithm SHA256 file` in PowerShell, `sha256sum file`
  on Linux) gives the same values only if not a byte has changed, which ties
  the result to exactly those files. A fingerprint reveals nothing about the
  file's content.
- Packages install from hash-pinned lock files (see *Dependency pinning*).
- Keep `.env` (API keys) out of OneDrive and other synced folders.

## Measuring accuracy

Confidence is what the model says about itself; accuracy is how often it is
actually right. The evaluation measures accuracy on a **gold set**: JEs and
documents whose right answers are known.

```
pip install --require-hashes -r requirements-eval.lock
python -m je_validator eval generate                       # 52 synthetic cases in goldset/ (--cases 104 for more)
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
.venv\Scripts\python -m pip install --require-hashes -r requirements.lock
copy .env.example .env      # then adjust the settings you need
.venv\Scripts\python -m je_validator templates add sample_templates/standard.json sample_templates/sap_export.json
```

### Dependency pinning

Packages are installed from **lock files** that pin every package, including
the packages they depend on, to an exact version and list the SHA-256 of each
of its files. With `--require-hashes`, pip refuses any download whose hash
differs, so a tampered or swapped package can't be installed; it stops with
"THESE PACKAGES DO NOT MATCH THE HASHES".

| Install | From | Lock |
|---|---|---|
| The validator (always) | `requirements.txt` | `requirements.lock` |
| OCR (PaddleOCR) | `requirements-ocr.txt` | `requirements-ocr.lock` |
| Claude API (optional) | `requirements-claude.txt` | `requirements-claude.lock` |
| Gold-set generator (optional) | `requirements-eval.txt` | `requirements-eval.lock` |
| PaddleOCR-VL (optional; Python 3.12+) | `requirements-vl.txt` | `requirements-vl.lock` |

Install the main lock first, then any others you need, each with
`--require-hashes`. The locks pin the versions every test and evaluation in
this project ran on, and work on Windows, Linux and macOS with Python 3.11 or
later. They don't cover PaddlePaddle (installed from its own index, pinned to
3.2.1 below) or the Vertex and Bedrock extras.

To change a package: edit the `requirements*.txt` file, regenerate the locks
with [uv](https://docs.astral.sh/uv/), then run the tests and the gold-set
evaluation again before committing:

```
uv pip compile requirements.txt --universal --python-version 3.11 --generate-hashes -o requirements.lock
uv pip compile requirements-ocr.txt -c requirements.lock --universal --python-version 3.11 --generate-hashes -o requirements-ocr.lock
uv pip compile requirements-claude.txt -c requirements.lock --universal --python-version 3.11 --generate-hashes -o requirements-claude.lock
uv pip compile requirements-eval.txt -c requirements.lock --universal --python-version 3.11 --generate-hashes -o requirements-eval.lock
uv pip compile requirements-vl.txt -c requirements.lock -c requirements-ocr.lock --universal --python-version 3.12 --generate-hashes -o requirements-vl.lock
```

(Behind a proxy that inspects HTTPS, add `--native-tls`.)

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
`pip install --require-hashes -r requirements-claude.lock` (see
`requirements-claude.txt` for the Vertex and Bedrock extras). `LLM_MODEL` and `LLM_ESCALATION_MODEL` choose the models; each
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
.venv\Scripts\python -m pip install --require-hashes -r requirements.lock
.venv\Scripts\python -m pip install paddlepaddle==3.2.1 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
.venv\Scripts\python -m pip install --require-hashes -r requirements-ocr.lock
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

- `ppocr` (default): PP-OCRv6, arranged by the page's layout (see *Reading
  pages by their layout*). `OCR_MODEL_SIZE` picks its size:
  `tiny` (about 1 s per A4 page on a laptop CPU), `small` (default, about 2 s)
  or `medium` (most accurate, about 35 s on a CPU; use it on a GPU).
- `hybrid`: as `ppocr`, plus PaddleOCR-VL-1.6 for table, seal, chart and
  formula zones, used only where it agrees with PP-OCR on every amount (see
  *PaddleOCR-VL for complex zones*).
- `auto`: pages where the layout model finds a table, seal, chart or formula go
  to PaddleOCR-VL-1.6, everything else to PP-OCRv6.
- `vl`: always PaddleOCR-VL-1.6.

`hybrid`, `auto` and `vl` also need
`pip install --require-hashes -r requirements-vl.lock` (Python 3.12 or later).
That is PaddleOCR's doc-parser extra without OpenAI's client library and
tokenizer, which it would otherwise install: VL runs on this machine and needs
neither. The model (about 2 GB) downloads on first use. On a CPU,
PaddleOCR-VL-1.6 (0.9B parameters) can take minutes per zone, so use these
modes once a GPU machine is available, with `OCR_DEVICE=gpu:0`. PP-OCRv6 returns plain
lines of text, so table columns in a scan come through as text spaced across
each line, not as a table.

## Run

Web page:

```
.venv\Scripts\python -m je_validator serve         # open http://127.0.0.1:8000
```

Drop the JE Excel in the first box and the supporting documents in the second,
then click Validate.

**Checking the result against the documents.** Each PDF and image document in
the result has *Pages with the zones read*: the page as uploaded, with the
zones the reader found drawn on it, labelled (title, text, table, header,
footer, picture, ...) and numbered in reading order; hover a zone for its label
and confidence, and untick *Show zones* to see the bare page. In the lines
table, each line's evidence has a link such as *Show in invoice.pdf, page 1,
zone 7*: it opens that page and highlights the zone the amount came from (or
the picture, for text read by OCR from a picture on a typed page). Word,
Excel and CSV documents have no page view.

The page images are the originals, not masked: they are for the reviewer, and
are never given to a model or stored in the result. They stay in the server's
memory only, behind the page's password, are never cached by the browser, and
are deleted with the result (`JOB_RETENTION_MINUTES`). The page's address
includes the result (`?job=...`), so reloading it shows the result again while
it is kept.

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
   tar.exe -a -c -f $env:USERPROFILE\Downloads\je-validator-code.zip --exclude=__pycache__ je_validator sample_templates goldset goldset_layout goldset_two_layouts goldset_pii requirements.txt requirements.lock requirements-ocr.txt requirements-ocr.lock requirements-vl.txt requirements-vl.lock
   ```
   The notebook installs from the lock files, so they must be in the zip.
2. **Upload it as a private dataset.** On kaggle.com go to *Datasets → New
   Dataset*, upload the zip, name it `je-validator-code`, and leave it
   **Private**. Repeat this whenever the code changes. With the Kaggle API set
   up, `.\kaggle\eval\push.ps1 code` does steps 1 and 2 (see *Evaluating on
   Kaggle*). Once the branch is on GitHub, you can set `GIT_URL` in the
   notebook instead.
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
escalating to `qwen2.5:14b`), the OCR mode (default `ppocr`, the most
accurate in the gold-set evaluation; `hybrid`, `auto` and `vl` add
PaddleOCR-VL-1.6) and the password. Leave the password empty to get a new
random one each run.

Everything on the Kaggle machine is lost when the session ends, including
templates you added on the page. Keep your template JSON files on your own
machine and upload them again next time.

### Evaluating on Kaggle

The gold-set evaluation can run on Kaggle's GPUs as a batch job, started and
collected from your laptop with the Kaggle API. No browser is needed.
[kaggle/eval/run_eval.py](kaggle/eval/run_eval.py) installs the project from
the lock files and starts Ollama on one T4. It then runs `eval run` for each
entry of its `RUNS` list, with PaddleOCR on the other T4. By default that is
the complex-layout gold set with `ppocr` and with `hybrid`, both with
`qwen2.5:7b`. Runs on the same gold set and model are compared with McNemar's
test.

Only the code, the lock files and the made-up gold sets are uploaded. `.env`,
`eval_results` and anything else stay on your laptop.

1. **Set up the API, once.**
   - On kaggle.com, go to *Settings → API → Create New Token*. Save the
     downloaded `kaggle.json` in `%USERPROFILE%\.kaggle\`. It is a password:
     never commit or share it.
   - Install the CLI as a separate tool, outside the project's venv:
     `uv tool install kaggle --with six --with pip-system-certs`. On a
     company network that inspects HTTPS, `pip-system-certs` makes it trust
     the Windows certificate store; without it, every call fails with
     `CERTIFICATE_VERIFY_FAILED`.
   - Your account needs a verified phone number for GPU and Internet.
2. **Upload the code and gold sets:** `.\kaggle\eval\push.ps1 code`. This
   creates the private dataset `je-validator-code`, or adds a new version of
   it. Repeat it whenever the code changes. It passes `-t` because Kaggle
   would otherwise convert the gold sets' Excel files to CSV.
3. **Start the run:** `.\kaggle\eval\push.ps1 run`. This pushes the private
   script `je-validator-eval` and runs it on GPU T4 x2.
4. **Follow it:** `kaggle kernels status <user name>/je-validator-eval`. A run
   may take at most 12 hours, out of about 30 GPU hours a week.
5. **Collect the results:**
   ```
   kaggle kernels output <user name>/je-validator-eval -p eval_results\kaggle
   ```
   This downloads `eval_results/` (one folder and log per run, with
   `report.json`, plus the comparison) and the run's log.

To change what is run, edit `RUNS` in `run_eval.py` before step 3. For
example, add `("pii_14b", "goldset_pii", "ppocr", "qwen2.5:14b")`.

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
