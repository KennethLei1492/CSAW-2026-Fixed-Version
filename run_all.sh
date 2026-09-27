#!/usr/bin/env bash
# ============================================================================
# run_all.sh  -  one-click pipeline for the CSAW'26 qualifier submission.
#
#   bitstream --(IceStorm)--> netlist --(oracle probing)--> recovered cipher/key
#   --(Feistel hardening)--> clean core --(AST manipulation)--> Trojaned core
#   --> functional + exploit simulation --> report.
#
# Regenerates every derived file from the committed sources, then self-tests.
# Requires the OSS CAD Suite (IceStorm, Icarus, Yosys) and python3 on PATH.
#
# Usage:  ./run_all.sh        full pipeline (re-derives everything, then tests)
#         ./run_all.sh sim    skip re-derivation, just build + simulate
#
# Prints, at the end, and exits 0 ONLY if both hold:
#   SELFTEST_GOLDEN:  PASS   (recovered core reproduces encrypt(0x59C359C3)=0x9CD84392)
#   SELFTEST_EXPLOIT: PASS   (perfect stealth on all non-trigger traffic + key leak)
# ============================================================================
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; cd "$ROOT"
PY="$(command -v python3 || command -v python)"; [ -n "$PY" ] || { echo "python not found"; exit 2; }

if [ "${1:-}" != "sim" ]; then
  echo "#### Stage 1: reverse-engineer the bitstream -> netlist (IceStorm) ####"
  ( cd recovery && bash reverse.sh ice40_bitstream.bin )
  echo "#### Stage 2: recover the cipher from the netlist oracle ####"
  ( cd recovery && "$PY" extract_cipher.py )
  echo "#### Stage 2b: document the recovered key ####"
  ( cd recovery && "$PY" probe_key.py ) || echo "(probe_key non-fatal)"
  echo "#### Stage 3: design + self-test the hardened Feistel cipher ####"
  "$PY" hardening/design_hardened.py
  echo "#### Stage 4: generate the CLEAN hardened core ####"
  "$PY" hardening/gen_core.py
  echo "#### Stage 5: AST manipulation - document insertion points + insert Trojan ####"
  "$PY" hardening/ast_insertion.py
fi

GOLDEN=FAIL; EXPLOIT=FAIL

echo "#### Stage 6: RE proof - recovered core reproduces the golden vector ####"
( cd rtl
  iverilog -g2012 -o ../valrec.vvp ../tb/tb_validate.v spi_crypto_top.v crypto_core_recovered.v
  vvp ../valrec.vvp ) | tee hardening/sim_recovered.txt
grep -q 'VALIDATE: ALL PASS' hardening/sim_recovered.txt && GOLDEN=PASS

echo "#### Stage 7: hardened cipher + AST-inserted Trojan exploit ####"
( cd rtl
  iverilog -g2012 -o ../troj.vvp ../tb/tb_trojan.v spi_crypto_top.v crypto_core.v
  vvp ../troj.vvp | grep -vE 'VCD|dumpfile' ) | tee hardening/sim_trojan.txt
grep -q 'TB_TROJAN: ALL PASS' hardening/sim_trojan.txt && EXPLOIT=PASS

if [ "${1:-}" != "sim" ]; then
  echo "#### Stage 8: figures + technical report ####"
  "$PY" hardening/make_figures.py || echo "(figures non-fatal)"
  "$PY" hardening/build_report.py || echo "(report non-fatal)"
  # best-effort HTML -> PDF (skipped cleanly if no browser engine is present)
  HTML="$ROOT/hardening/report.html"; PDF="$ROOT/CSAW26_Trojan_Report.pdf"
  for B in google-chrome google-chrome-stable chromium chromium-browser \
           "/c/Program Files/Google/Chrome/Application/chrome.exe" \
           "/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" \
           "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" wkhtmltopdf; do
    if command -v "$B" >/dev/null 2>&1 || [ -x "$B" ]; then
      if [[ "$B" == *wkhtmltopdf* ]]; then "$B" "$HTML" "$PDF" >/dev/null 2>&1 && break
      else "$B" --headless --disable-gpu --no-sandbox --no-pdf-header-footer --print-to-pdf="$PDF" "file://$HTML" >/dev/null 2>&1 && break; fi
    fi
  done
fi

rm -f valrec.vvp troj.vvp rtl/*.vcd 2>/dev/null || true

echo
echo "SELFTEST_GOLDEN:  $GOLDEN"
echo "SELFTEST_EXPLOIT: $EXPLOIT"
if [ "$GOLDEN" = PASS ] && [ "$EXPLOIT" = PASS ]; then
  echo "ALL SELF-TESTS PASSED"; exit 0
else
  echo "SELF-TESTS FAILED"; exit 1
fi
