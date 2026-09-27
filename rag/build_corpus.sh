#!/usr/bin/env bash
# ============================================================================
#  build_corpus.sh - (re)assemble the RAG corpus under rag/corpus/.
#
#  Freely-redistributable sources are fetched automatically:
#    * the CSAW'26 challenge documents (from the public qualifier repo)
#    * the Serial Peripheral Interface article (simple.wikipedia.org, CC BY-SA)
#
#  The two Lattice reference PDFs are copyrighted and are NOT redistributed in
#  this repo. If you have them, point these env vars at the files (or drop them
#  in ~/Downloads with their original names) and they will be extracted too:
#     ICE40_DESIGN_PDF = FPGA-AN-02109 iCE40 Design Guidelines
#     ICE40_SPI_PDF    = FPGA-TN-02011 Advanced iCE40 I2C & SPI Hardened IP User Guide
#  Requires: curl, python3, and (for the PDFs) pdftotext.
# ============================================================================
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; C="$HERE/corpus"; mkdir -p "$C"
RAW="https://raw.githubusercontent.com/JBlocklove/CSAW-AI-Hardware-Attack-Challenge-2026/main/qualifier"
DL="${HOME}/Downloads"

echo "[corpus] challenge documents"
curl -fsSL "$RAW/ice40_cryptographic_IP.md"   -o "$C/challenge_ip_doc.md"            || true
curl -fsSL "$RAW/spi_ice40_crypto_ip_test.py" -o "$C/challenge_micropython.py"       || true
curl -fsSL "$RAW/README.md"                    -o "$C/challenge_qualifier_readme.md"  || true

echo "[corpus] SPI reference (simple.wikipedia.org)"
curl -fsSL -A "Mozilla/5.0" "https://simple.wikipedia.org/wiki/Serial_Peripheral_Interface" -o "$C/_spi.html" && \
python3 - "$C" << 'PY' || true
import re, html, sys, os
c = sys.argv[1]
src = open(os.path.join(c, "_spi.html"), encoding="utf-8", errors="ignore").read()
m = re.search(r'id="mw-content-text"(.*?)(?:id="catlinks"|class="printfooter")', src, re.DOTALL)
body = m.group(1) if m else src
body = re.sub(r'<(style|script|table)[^>]*>.*?</\1>', '', body, flags=re.DOTALL)
text = html.unescape(re.sub(r'<[^>]+>', ' ', body)); text = re.sub(r'[ \t]+', ' ', text)
text = "\n".join(l.strip() for l in text.splitlines() if len(l.strip()) > 1)
open(os.path.join(c, "spi_wikipedia_simple.txt"), "w", encoding="utf-8").write(
    "Source: https://simple.wikipedia.org/wiki/Serial_Peripheral_Interface\n\n" + text)
os.remove(os.path.join(c, "_spi.html"))
PY

echo "[corpus] Lattice PDFs (optional, not redistributed)"
AN="${ICE40_DESIGN_PDF:-$DL/FPGA-AN-02109-1-0-iCE40-Design-Guidelines.pdf}"
TN="${ICE40_SPI_PDF:-$DL/FPGA-TN-02011-1-8-Advanced-iCE40-I2C-and-SPI-Hardened-IP-User-Guide.pdf}"
if command -v pdftotext >/dev/null 2>&1; then
  [ -f "$AN" ] && pdftotext -layout "$AN" "$C/ice40_design_guidelines.txt"     && echo "  + iCE40 Design Guidelines" || echo "  (design-guidelines PDF not found; skipped)"
  [ -f "$TN" ] && pdftotext -layout "$TN" "$C/ice40_spi_i2c_hardened_ip.txt"    && echo "  + iCE40 SPI/I2C Hardened IP" || echo "  (hardened-IP PDF not found; skipped)"
else
  echo "  (pdftotext not available; skipping PDF extraction)"
fi
echo "[corpus] done:"; ls -1 "$C"
