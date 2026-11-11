#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SEPA/EPC payment QR code generator (EPC069-12, version 002, SEPA Credit Transfer).

Produces a scannable SVG containing an EPC QR code that European banking apps
(George, ELBA, ING, sparkasse apps, etc.) read and prefill as a bank transfer.
Pure Python standard library plus the bundled Nayuki qrcodegen.py (MIT License).

The script performs a full round-trip verification of the generated matrix
(format info BCH check, both format copies, unmasking, zigzag extraction,
de-interleaving, Reed-Solomon check per block, byte-mode payload comparison)
and exits with a non-zero status on any inconsistency. Deliver the SVG only
when the run reports all checks OK.

Usage:
  python3 sepa_qr_svg.py --iban AT043141500000000123 \
      --name "Musterfirma M\u00fcller GmbH" \
      --amount 123.45 --bic BANKATWW \
      --ref "Invoice 2026-000123" \
      --out sepa-qr.svg [--title "..."] [--subtitle "..."]

Output SVG geometry: 12 px per module, 4-module quiet zone, centered labels.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qrcodegen import QrCode, QrSegment


def fail(msg: str) -> None:
    print("ERROR:", msg, file=sys.stderr)
    sys.exit(1)


def normalize_amount(raw: str) -> str:
    v = raw.strip().replace(",", ".").replace(" ", "")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", v):
        fail("Amount must be a decimal number with at most two decimals: " + raw)
    return "EUR%.2f" % float(v)


_LETTERS = {chr(ord("A") + i): str(10 + i) for i in range(26)}


def iban_mod97_valid(iban: str) -> bool:
    """ISO 13616 / ISO 7064 mod-97 checksum."""
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}", iban):
        return False
    digits = "".join(_LETTERS.get(c, c) for c in iban[4:] + iban[:4])
    rem = 0
    for d in digits:
        rem = (rem * 10 + int(d)) % 97
    return rem == 1


def build_payload(name: str, iban: str, amount: str, bic: str, ref: str) -> bytes:
    lines = [
        "BCD",              # Service tag (fixed)
        "002",              # Version (BIC optional since 002)
        "1",                # Character set: 1 = UTF-8 (required for umlauts)
        "SCT",              # SEPA Credit Transfer
        bic,                # BIC, may be empty in version 002
        name,               # Beneficiary name, 1..70 chars
        iban,               # IBAN without spaces
        amount,             # e.g. EUR123.45, dot decimal separator
        "",                 # Purpose (4-char code, usually empty)
        "",                 # Structured remittance (usually empty)
        ref,                # Unstructured remittance, 0..140 chars
        "",                 # Beneficiary-to-originator info (optional in 002)
    ]
    payload = ("\n".join(lines) + "\n").encode("utf-8")

    # ---- EPC069-12 format checks ----
    if not (1 <= len(name) <= 70):
        fail("Beneficiary name must be 1..70 characters, got %d" % len(name))
    if not re.fullmatch(r"[A-Z0-9]{8,11}", bic) and bic != "":
        fail("BIC must be empty or 8..11 alphanumeric characters")
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{10,30}", iban):
        fail("IBAN format invalid (letters country code, 2 check digits, no spaces): " + iban)
    if not iban_mod97_valid(iban):
        fail("IBAN fails the ISO 7064 mod-97 checksum: " + iban)
    if not re.fullmatch(r"EUR\d+\.\d{2}", amount):
        fail("Amount must look like EUR12.34")
    if len(ref) > 140:
        fail("Reference (unstructured remittance) exceeds 140 characters")
    if len(payload) > 331:
        fail("Payload exceeds the 331-byte EPC limit (%d bytes)" % len(payload))
    return payload


def generate_and_verify(payload: bytes):
    """Returns (matrix, size, version) after full round-trip verification."""
    seg = QrSegment.make_bytes(payload)           # single BYTE-mode segment
    qr = QrCode.encode_segments([seg], QrCode.Ecc.MEDIUM)
    ver, size, mask, ecl = qr.get_version(), qr.get_size(), qr.get_mask(), qr.get_error_correction_level()

    # Second identical construction with function-module recording.
    function_modules = set()
    orig = QrCode._set_function_module

    def patched(self, x, y, isdark):
        function_modules.add((x, y))
        orig(self, x, y, isdark)

    QrCode._set_function_module = patched
    try:
        qr2 = QrCode.encode_segments([QrSegment.make_bytes(payload)], QrCode.Ecc.MEDIUM)
    finally:
        QrCode._set_function_module = orig
    if qr2.get_version() != ver or qr2.get_mask() != mask:
        fail("Non-deterministic generation")

    m = [[qr2.get_module(x, y) for x in range(size)] for y in range(size)]

    # 1) Format info readback, both copies, LSB-first bit order.
    bits = [None] * 15
    for i in range(0, 6):
        bits[i] = 1 if m[i][8] else 0
    bits[6] = 1 if m[7][8] else 0
    bits[7] = 1 if m[8][8] else 0
    bits[8] = 1 if m[8][7] else 0
    for i in range(9, 15):
        bits[i] = 1 if m[8][14 - i] else 0
    f1 = 0
    for i, b in enumerate(bits):
        f1 |= b << i
    bits2 = [None] * 15
    for i in range(0, 8):
        bits2[i] = 1 if m[8][size - 1 - i] else 0
    for i in range(8, 15):
        bits2[i] = 1 if m[size - 15 + i][8] else 0
    f2 = 0
    for i, b in enumerate(bits2):
        f2 |= b << i
    if f1 != f2:
        fail("Format info copies differ")
    raw = f1 ^ 0x5412
    data, rem = raw >> 10, raw & 0x3FF
    r = data
    for _ in range(10):
        r = (r << 1) ^ ((r >> 9) * 0x537)
    if (r & 0x3FF) != rem:
        fail("Format info BCH check failed")
    if (data & 7) != mask or (data >> 3) != ecl.formatbits:
        fail("Format info does not encode the expected mask/ECC level")

    # 2) Unmask data modules.
    def invert(x, y, msk):
        return {0: (x + y) % 2 == 0, 1: y % 2 == 0, 2: x % 3 == 0, 3: (x + y) % 3 == 0,
                4: (x // 3 + y // 2) % 2 == 0, 5: (x * y) % 2 + (x * y) % 3 == 0,
                6: ((x * y) % 2 + (x * y) % 3) % 2 == 0,
                7: ((x + y) % 2 + (x * y) % 3) % 2 == 0}[msk]
    unm = [[(m[y][x] != invert(x, y, mask)) if (x, y) not in function_modules else m[y][x]
            for x in range(size)] for y in range(size)]

    # 3) Zigzag codeword extraction in reader order. Versions with remainder
    # bits leave the surplus modules light; collect the full stream, then keep
    # exactly rawcw * 8 codeword bits.
    rawcw = QrCode._get_num_raw_data_modules(ver) // 8
    limit = rawcw * 8
    bits = []
    right = size - 1
    while right > 0:
        if right == 6:
            right -= 1
        for vert in range(size):
            for j in range(2):
                x = right - j
                upward = (right + 1) & 2 == 0
                y = (size - 1 - vert) if upward else vert
                if (x, y) not in function_modules:
                    bits.append(1 if unm[y][x] else 0)
        right -= 2
    if len(bits) < limit or len(bits) - limit >= 8:
        fail("Codeword bit count mismatch (%d data-module bits, need %d)" % (len(bits), limit))
    bits = bits[:limit]
    cw = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    # 4) De-interleave (exact inverse of qrcodegen _add_ecc_and_interleave).
    numblocks = QrCode._NUM_ERROR_CORRECTION_BLOCKS[ecl.ordinal][ver]
    blockecclen = QrCode._ECC_CODEWORDS_PER_BLOCK[ecl.ordinal][ver]
    if len(cw) != rawcw:
        fail("Codeword count mismatch")
    numshortblocks = numblocks - rawcw % numblocks
    shortblocklen = rawcw // numblocks
    datlen = [shortblocklen - blockecclen + (0 if b < numshortblocks else 1) for b in range(numblocks)]
    data_blocks = [[] for _ in range(numblocks)]
    ecc_blocks = [[] for _ in range(numblocks)]
    pos = 0
    for i in range(shortblocklen + 1):
        for b in range(numblocks):
            if i == shortblocklen - blockecclen and b < numshortblocks:
                continue  # padding slot of a short block
            c = cw[pos]
            pos += 1
            (data_blocks[b] if i < datlen[b] else ecc_blocks[b]).append(c)
    if pos != rawcw:
        fail("De-interleaving did not consume all codewords")

    # 5) Reed-Solomon check per block.
    rsdiv = QrCode._reed_solomon_compute_divisor(blockecclen)
    for b in range(numblocks):
        rem = QrCode._reed_solomon_compute_remainder(data_blocks[b], rsdiv)
        if bytes(rem) != bytes(ecc_blocks[b]):
            fail("Reed-Solomon mismatch in block %d" % b)

    # 6) Byte-mode payload comparison (8-bit count field for versions <= 9).
    datacw = [c for b in range(numblocks) for c in data_blocks[b]]
    bs = "".join(format(c, "08b") for c in datacw)
    if bs[0:4] != "0100":
        fail("Data does not start with BYTE-mode indicator")
    countbits = 16 if ver >= 10 else 8
    n = int(bs[4:4 + countbits], 2)
    if not 1 <= n <= 65535:
        fail("Implausible byte count")
    off = 4 + countbits
    text = bytes(int(bs[off + i * 8:off + 8 + i * 8], 2) for i in range(n))
    if text != payload:
        fail("Round-trip payload mismatch")

    print("Verification OK: format info BCH, %d codewords, %d RS blocks, payload identical."
          % (rawcw, numblocks))
    return m, size, ver


def render_svg(m, size, path, title, subtitle):
    s = 12          # px per module
    quiet = 4       # quiet zone in modules
    top = quiet * s
    qrpx = size * s
    W = (size + 2 * quiet) * s
    head_h = 64 if not title else 100
    H = top + qrpx + quiet * s + head_h
    parts = []
    for y in range(size):
        for x in range(size):
            if m[y][x]:
                x0, y0 = top + x * s, top + y * s
                parts.append("M%d %dh%dv%dh-%dz" % (x0, y0, s, s, s))
    d = "".join(parts)
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d">' % (W, H, W, H),
           '  <rect width="%d" height="%d" fill="#ffffff"/>' % (W, H),
           '  <path d="%s" fill="#000000" shape-rendering="crispEdges"/>' % d]
    if title:
        svg.append('  <text x="%d" y="%d" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
                   'font-size="28" font-weight="bold" fill="#1a1a1a">%s</text>'
                   % (W // 2, top + qrpx + quiet * s + 46, title))
    if subtitle:
        ysub = top + qrpx + quiet * s + (78 if title else 42)
        svg.append('  <text x="%d" y="%d" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
                   'font-size="22" fill="#333333">%s</text>' % (W // 2, ysub, subtitle))
    svg.append('</svg>')
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(svg) + "\n")
    print("SVG written:", path, os.path.getsize(path), "bytes")


def xml_escape(t):
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def main():
    ap = argparse.ArgumentParser(description="SEPA/EPC QR code (SVG) generator with verification")
    ap.add_argument("--iban", required=True, help="Beneficiary IBAN, no spaces")
    ap.add_argument("--name", required=True, help="Beneficiary name, 1..70 characters")
    ap.add_argument("--amount", required=True, help="Amount, e.g. 123.45 or '123,45'")
    ap.add_argument("--bic", default="", help="BIC, 8..11 chars or empty")
    ap.add_argument("--ref", default="", help="Reference / unstructured remittance, <=140 chars")
    ap.add_argument("--out", required=True, help="Output SVG path")
    ap.add_argument("--title", default="", help="Caption line above subtitle (optional)")
    ap.add_argument("--subtitle", default="", help="Caption line under the code (optional)")
    a = ap.parse_args()

    iban = a.iban.replace(" ", "").upper()
    amount = normalize_amount(a.amount)
    payload = build_payload(a.name, iban, amount, a.bic.strip().upper(), a.ref.strip())
    print("EPC payload (%d bytes):" % len(payload))
    print(payload.decode("utf-8"), end="")
    m, size, ver = generate_and_verify(payload)
    render_svg(m, size, a.out, xml_escape(a.title), xml_escape(a.subtitle))
    print("QR version %d, %dx%d modules, mask OK." % (ver, size, size))


if __name__ == "__main__":
    main()
