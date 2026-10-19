#!/usr/bin/env bash
# ============================================================================
# run_all.sh - one-click pipeline for the CSAW'26 qualifier submission.
#
#   bitstream --(IceStorm)--> netlist --(oracle probing)--> recovered cipher/key
#   --(Feistel hardening)--> clean core --(AST manipulation)--> Trojaned core
#   --> functional + exploit simulation --> equivalence proof + variants + report.
#
# Requires the OSS CAD Suite (IceStorm, Icarus, Yosys) and python3 on PATH.
#
# Usage:  ./run_all.sh          full staged pipeline + variants + report
#         ./run_all.sh sim      quick: just build + simulate the committed RTL
#
# Prints, at the end, and exits 0 ONLY if both hold:
#   SELFTEST_GOLDEN:  PASS   (recovered core reproduces encrypt(0x59C359C3)=0x9CD84392)
#   SELFTEST_EXPLOIT: PASS   (perfect stealth on all non-trigger traffic + key leak)
# ============================================================================
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; cd "$ROOT"
PY="$(command -v python3 || command -v python)"; [ -n "$PY" ] || { echo "python not found"; exit 2; }
mkdir -p build
GOLDEN=FAIL; EXPLOIT=FAIL; KEYCHK=SKIP; NETEQUIV=SKIP

run_sims(){
  ( cd rtl
    iverilog -g2012 -o ../build/_v.vvp ../tb/tb_validate.v spi_crypto_top.v crypto_core_recovered.v
    vvp ../build/_v.vvp ) | tee build/_v.out
  grep -q 'VALIDATE: ALL PASS' build/_v.out && GOLDEN=PASS
  # Exploit sim runs against the RECOVERED cipher (the real device behaviour),
  # leaking the recovered device key 0x8E043CB1 - not a recreated stand-in.
  ( cd rtl
    iverilog -g2012 -o ../build/_t.vvp ../tb/tb_trojan.v spi_crypto_top.v crypto_core_recovered.v
    vvp ../build/_t.vvp | grep -vE 'VCD|dumpfile' ) | tee build/_t.out
  grep -q 'TB_TROJAN: ALL PASS' build/_t.out && EXPLOIT=PASS
  rm -f build/_v.vvp build/_t.vvp build/_v.out build/_t.out rtl/*.vcd 2>/dev/null || true
}

if [ "${1:-}" = "sim" ]; then
  echo "#### quick sim (committed RTL) ####"
  run_sims
else
  echo "#### RAG: assemble corpus + build the documentation vector store (best-effort) ####"
  bash rag/build_corpus.sh >/dev/null 2>&1 || echo "(corpus fetch partial)"
  "$PY" rag/build_vectorstore.py 2>/dev/null | tail -1 || echo "(RAG store optional; skipped)"

  echo "#### Staged pipeline (5 stages, immediate checks, RAG at each stage) ####"
  "$PY" pipeline/run_pipeline.py || echo "(pipeline reported a failure; see build/verification_report.json)"

  echo "#### Reverse-engineering verification (against the gate-level netlist) ####"
  ( cd recovery && bash reverse.sh >/dev/null 2>&1 || true )
  if "$PY" recovery/recover_key.py; then KEYCHK=PASS; else KEYCHK=FAIL; fi
  if "$PY" recovery/equiv_netlist.py; then NETEQUIV=PASS; else NETEQUIV=FAIL; fi

  echo "#### Formal proof: crypto_core.v (TROJAN_EN=0) == crypto_core_clean.v ####"
  ( cd rtl && timeout 120 yosys -s ../hardening/equiv_check.ys > ../build/equiv.log 2>&1 ) \
     && echo "EQUIV: PASS (clean twin proven equivalent)" \
     || echo "EQUIV: see build/equiv.log"

  echo "#### Trojan variants: generate, validate, CVSS-rank ####"
  "$PY" hardening/eval_variants.py || echo "(variant study non-fatal)"

  echo "#### Figures + technical report ####"
  "$PY" hardening/make_figures.py || echo "(figures non-fatal)"
  "$PY" hardening/build_report.py || echo "(report non-fatal)"
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

  echo "#### Confirm self-tests on the final RTL ####"
  run_sims
fi

echo
echo "SELFTEST_GOLDEN:   $GOLDEN"
echo "SELFTEST_EXPLOIT:  $EXPLOIT"
echo "KEY_RECOVERY:      $KEYCHK     (recovered device key 0x8E043CB1 == leaked key)"
echo "NETLIST_EQUIV:     $NETEQUIV     (recovered RTL bit-exact to gate-level netlist)"
if [ "$GOLDEN" = PASS ] && [ "$EXPLOIT" = PASS ] \
   && [ "$KEYCHK" != FAIL ] && [ "$NETEQUIV" != FAIL ]; then echo "ALL SELF-TESTS PASSED"; exit 0
else echo "SELF-TESTS FAILED"; exit 1; fi
