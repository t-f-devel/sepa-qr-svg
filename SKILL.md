---
name: sepa-qr-svg
description: Generate a scannable SEPA payment QR code (EPC069-12, GiroCode) as an SVG from bank transfer data (IBAN, BIC, beneficiary, amount, remittance reference), e.g. from invoices, payment demands, or enforcement letters. Load when the user asks for a payment or bank transfer QR code in any language ("QR-Code für die Zahlung", "Zahlungs-QR erstellen", "payment QR code", "QR pour payer"). Works fully offline (Python 3 standard library only) and self-verifies the generated code before delivery.
---

# SEPA QR SVG (EPC069-12)

Generates an EPC-compliant QR code that European banking apps (George, ELBA, ING, Sparkasse, and others) read through their QR scan feature as a prefilled SEPA credit transfer.

## Required inputs

- IBAN (whitespace is normalized, ISO 7064 mod-97 validated), BIC (optional), beneficiary name (1 to 70 characters), amount, remittance reference (at most 140 characters).
- If a payment document is available (e.g. an invoice or a payment demand), extract the data from it and include the creditor's customer reference in the remittance so the payment is allocated correctly.
- With accruing interest (common in payment demands): extrapolate the amount to today and tell the user that the amount can be adjusted in the banking app and that the exact figure can be obtained from the creditor. A slight overpayment is credited.

## Workflow

1. Run the generator from a checkout of this repository (Python 3, no dependencies, no network access needed):

   ```bash
   python3 sepa_qr_svg.py \
     --iban "AT04 3141 5000 0000 0123" \
     --name "Musterfirma Müller GmbH" \
     --amount 123.45 \
     --bic BANKATWW \
     --ref "Invoice 2026-000123" \
     --out sepa-qr.svg \
     --title "SEPA transfer" \
     --subtitle "123.45 EUR - remittance"
   ```

2. Deliver the SVG only if the run exits with code 0 and prints `Verification OK`. The verifier checks the format information (BCH, both copies), unmasking, codeword extraction, de-interleaving, Reed-Solomon per block, and that the decoded payment text exactly matches the EPC payload. On failure, never deliver the unconfirmed code.

## EPC format rules (quick reference)

- Lines 1 to 4 are fixed: `BCD`, `002`, `1` (charset UTF-8, required for umlauts), `SCT`.
- BIC optional in version 002; name 1 to 70 characters; IBAN without spaces, mod-97 validated; amount as `EUR#.##` with a dot as decimal separator; unstructured remittance at most 140 characters; line 12 (beneficiary-to-originator) stays empty; total payload at most 331 bytes.
- The generator enforces a single BYTE segment at error correction level MEDIUM; the verifier handles both BYTE count-field widths (8 bit below version 10, 16 bit from version 10), so every version up to the 331-byte EPC limit is verified (a maximal payload lands at version 12).

## User-facing notes after delivery

- Scan with the banking app's QR feature; all transfer data is prefilled and remains adjustable in the app.
- For a clean scan: capture the code fully, with good contrast and without reflections.
- For court enforcement matters: remind the user that after payment they must promptly apply for termination of the enforcement at the court, presenting the payment receipt, so no bailiff costs accrue.
