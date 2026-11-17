# Sample files

Invented JEs and supporting documents for trying the validator. All names,
companies, bank details and figures are made up.

Each numbered folder is one complete validation: `JE.xlsx` plus its supporting
documents. Every folder books the same invoice (Globex Ltd, INV-7781,
October 2026: IT support 1,200.00 + cloud hosting 1,800.00 = GBP 3,000.00), so
the difference between folders is only what the folder is about.

## How to run them

On the page (`python -m je_validator serve`, then http://127.0.0.1:8000): drop
the folder's `JE.xlsx` in the first box and all its other files in the second,
then click **Validate**.

From the command line, for example:

```powershell
$py = "$env:USERPROFILE\.venvs\je-validator\Scripts\python.exe"
& $py -m je_validator validate --je samples\1_clean_pass\JE.xlsx samples\1_clean_pass\invoice.pdf samples\1_clean_pass\approval.docx samples\1_clean_pass\bank.csv
```

With `qwen2.5:7b` on a laptop CPU each run takes about 3-4 minutes.

## What each folder should show

Results marked *code* are decided by the program and should always come out
as stated. Results marked *model* depend on the model's review; a small model
can still get those wrong, which the confidence score and human review are for.

### 1_clean_pass - everything matches

| Check | Expected |
|---|---|
| Debits equal credits | PASS (code) |
| Line amounts match the documents | PASS: 1,200.00, 1,800.00 and 3,000.00 all found (code compares the amounts the model picks) |
| Vendors named in the documents | PASS (code) |
| References found in the documents | PASS: INV-7781 is in the invoice and the bank line (code) |
| Currency consistent | PASS: GBP (code) |
| Document dates in the JE period | PASS: all dates in October 2026 (code) |
| Approval covers the JE total | PASS (model) |

Overall: **PASS**, unless the model is unsure (then human review).

### 2_vendor_mismatch - vendor names

The JE names the vendor three ways: line 1 `Globex Limited`, line 2
`Gbx Ltd`, line 3 `Globex Ltd`. The documents say `Globex Ltd`.

- **Vendors named in the documents: FAIL** (code). `Globex Limited` is
  accepted (Ltd and Limited are the same legal form); `Gbx Ltd` isn't named in
  any document.
- Line 2's issues say: *Vendor 'Gbx Ltd' isn't named in the documents; the
  closest is '... Globex Ltd ...'*.
- Overall: **FAIL**.

### 3_text_and_pictures - typed text with pictures inside

- `invoice.pdf`: the heading is typed, the items and totals are a pasted
  picture.
- `approval.docx`: typed paragraphs, with the approval table as a screenshot.
- `bank_payment.xlsx`: a typed note, with the bank line as a screenshot.

Expected: each document's method shows both readers, e.g. *text layer,
PP-OCRv6 small*, and *Text the model saw* has an `[Image 1 - PP-OCRv6 small]`
block holding the picture's text. The amounts in the pictures (1,200.00,
1,800.00, 3,000.00) are found, so the result should match folder 1:
**PASS**. Before pictures were read, these amounts never reached the model.

### 4_personal_data - masking and minimising

The invoice carries an address, a contact name and email, a phone number,
a sort code, an account number, an IBAN and a signed-off name; the approval
names the approver and has an email.

Open **Text the model saw** for each document:

- the approver is `[PERSON-B] (Finance Manager)` and the email `[EMAIL-A]`;
- the address, contact, phone, bank details, IBAN and sign-off lines are gone
  from the invoice; only the lines with the invoice number, date, PO, items
  and totals are left;
- the JE header shown has no *Prepared By*.

The checks should come out as in folder 1: **PASS**.

## Older samples (in this folder)

`JE_standard.xlsx` (JE-2026-0042, with `invoice.pdf`, `scan_invoice.png`,
`approval.docx`, `bank.csv`) has two planted differences: line 2 is 1,800.50
against 1,800.00 on the invoice (**MISMATCH**) and names `Gbx Ltd`
(**vendor FAIL**). `scan_invoice.png` is the same invoice as a scan, read by
OCR. `JE_sap.xlsx` is an SAP-style export layout (bank fees, EUR).
