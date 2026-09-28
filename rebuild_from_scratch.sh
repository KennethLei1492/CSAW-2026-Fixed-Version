#!/usr/bin/env bash
# =============================================================================
#  rebuild_from_scratch.sh
#  CSAW'26 AI Hardware Attack - Qualifier : PREPARE + CLEAN-ROOM AI BUILD PROMPT
# -----------------------------------------------------------------------------
#  This script contains NO source code from the project. It prepares a clean
#  environment and emits a detailed, MODEL-AGNOSTIC, CLEAN-ROOM methodology prompt
#  (BUILD_PROMPT.md) that ANY AI coding agent can follow to regenerate the entire
#  submission from scratch, saving every generated element into the project folder.
#  The only required input is the challenge bitstream (downloaded automatically);
#  the two Lattice reference PDFs are optional and user-supplied (for the RAG store).
#
#  Running it (no user input):
#     1. installs the OSS CAD Suite (Icarus Verilog, GTKWave, Yosys, IceStorm),
#     2. opens a NEW empty project directory,
#     3. downloads ONLY the challenge bitstream into it (the sole allowed input,
#        from JBlocklove/CSAW-AI-Hardware-Attack-Challenge-2026, qualifier/),
#     4. writes BUILD_PROMPT.md - the full methodology, with no code snippets.
#
#  Then point the AI model of your choice at BUILD_PROMPT.md (paste it in, or run
#  your agent in the project directory) to have it write every RTL / testbench /
#  analysis / report file and a self-checking run_all.sh. If a run_all.sh already
#  exists when this script finishes, it will run it and show the PASS/FAIL result.
#
#  Requirements: bash, curl (or wget), tar, python3 (used by the generated
#  scripts). No AI-vendor tooling is assumed or configured here.
#
#  Usage:  bash rebuild_from_scratch.sh [PROJECT_DIR]      # default ./csaw26_ai_build
#  Env:    SKIP_TOOL_INSTALL=1   reuse an OSS CAD Suite already on PATH
# =============================================================================
set -uo pipefail

PROJECT_DIR="${1:-$PWD/csaw26_ai_build}"
REPO_RAW="https://raw.githubusercontent.com/JBlocklove/CSAW-AI-Hardware-Attack-Challenge-2026/main/qualifier"
OSS_DATE="20260920"; OSS_TAG="2026-09-20"
OSS_BASE="https://github.com/YosysHQ/oss-cad-suite-build/releases/download/${OSS_TAG}"

say(){ printf '\n\033[1;36m==== %s ====\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m%s\033[0m\n' "$*" >&2; }
die(){ printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
have(){ command -v "$1" >/dev/null 2>&1; }
fetch(){ if have curl; then curl -fL --retry 3 -o "$2" "$1"; elif have wget; then wget -q -O "$2" "$1"; else die "need curl or wget"; fi; }

# ---------------------------------------------------------------------------
setup_tools(){
  if [ "${SKIP_TOOL_INSTALL:-0}" = "1" ] || { have iverilog && have yosys && have icepack; }; then
    say "OSS CAD Suite already available - skipping install"
    export OSS_ROOT="$(dirname "$(dirname "$(command -v yosys)")")"; return
  fi
  say "Installing OSS CAD Suite (Icarus Verilog, GTKWave, Yosys, IceStorm)"
  local os arch plat tgz suite
  case "$(uname -s)" in
    Linux) os=linux;; Darwin) os=darwin;; MINGW*|MSYS*|CYGWIN*) os=windows;;
    *) die "unsupported OS $(uname -s)";; esac
  case "$(uname -m)" in
    x86_64|amd64) arch=x64;; arm64|aarch64) arch=arm64;;
    *) die "unsupported arch $(uname -m)";; esac
  plat="${os}-${arch}"; mkdir -p "$PROJECT_DIR"; tgz="$PROJECT_DIR/oss-cad-suite.tgz"
  echo "platform: $plat"
  fetch "${OSS_BASE}/oss-cad-suite-${plat}-${OSS_DATE}.tgz" "$tgz" || die "OSS CAD Suite download failed"
  say "Extracting toolchain (large; please wait)"
  tar -xzf "$tgz" -C "$PROJECT_DIR"; suite="$PROJECT_DIR/oss-cad-suite"
  [ -d "$suite" ] || die "extract did not produce $suite"
  export OSS_ROOT="$suite"
  [ -f "$suite/environment" ] && . "$suite/environment"
  export PATH="$suite/bin:$suite/lib:$PATH"
  [ -f "$suite/etc/cacert.pem" ] && export SSL_CERT_FILE="$suite/etc/cacert.pem"
  have iverilog && have yosys && have icepack || die "toolchain not on PATH after install"
}

scaffold(){
  say "Opening a new project at: $PROJECT_DIR"
  mkdir -p "$PROJECT_DIR"/{rtl,tb,recovery,hardening,ai}
}

download_bitstream(){
  say "Downloading the ONLY permitted input: the official challenge bitstream"
  fetch "$REPO_RAW/ice40_bitstream.bin" "$PROJECT_DIR/recovery/ice40_bitstream.bin" \
    || die "bitstream download failed"
  fetch "$REPO_RAW/ice40_cryptographic_IP.md"   "$PROJECT_DIR/recovery/ref_ip_doc.md" 2>/dev/null || true
  fetch "$REPO_RAW/spi_ice40_crypto_ip_test.py" "$PROJECT_DIR/recovery/ref_micropython.py" 2>/dev/null || true
  local n; n=$(wc -c < "$PROJECT_DIR/recovery/ice40_bitstream.bin")
  echo "bitstream bytes: $n (expected 104090)"
}

# ---------------------------------------------------------------------------
# The methodology prompt - the ONLY substantive content of this script.
# Plain-English, model-agnostic instructions; no code from the reference project.
write_prompt(){
  say "Writing the model-agnostic methodology prompt (BUILD_PROMPT.md)"
  cat > "$PROJECT_DIR/BUILD_PROMPT.md" <<'PROMPT_EOF'
# CLEAN-ROOM REBUILD - CSAW'26 AI Hardware Attack qualifier
# (reverse-engineer an FPGA crypto accelerator, then insert and prove a stealthy hardware Trojan)

## How to run this (invocation contract)
This file is a **CLEAN-ROOM REBUILD** specification for an AI coding agent (e.g. Claude Code).
Invoke the agent with a prompt of exactly this shape:

    "run and carry out the following tasks. save all elements generated in this <FOLDER> folder"

where `<FOLDER>` is the project directory you are working in RIGHT NOW (this BUILD_PROMPT.md sits
at its root). Then do EVERYTHING below and **write every file you generate into that folder** and
its subfolders (`rtl/`, `tb/`, `recovery/`, `hardening/`, `pipeline/`, `rag/`, `build/`, `ai/`).
No further input from the user is required beyond the one file named under "Required inputs".

## Clean-room method (mandatory - what "clean room" means here)
Reverse-engineer the design's BEHAVIOUR only from the provided bitstream (plus public references),
then RE-DERIVE clean, human-readable RTL that is behaviourally identical to the recovered netlist.
The vendor RTL is NOT provided and must never be hand-transcribed or copied; **write every RTL,
testbench, analysis script, and report yourself from first principles**, and justify each artifact
by an observation you made (a simulation, a probe, a synthesis result) or by public documentation.
This is a clean-room reconstruction: do not copy any pre-existing source. Keep an engineering log at
`LOG.md` and SELF-TEST after every stage.

Keywords for this build (so you know the full scope to replicate): clean-room reverse engineering,
iCE40 UP5K bitstream, Project IceStorm `icepack -u` / `icebox_vlog` netlist lift, byte-exact repack
proof, power-on-reset (flip-flops power up to 0) patch, simulation ORACLE, race-free SPI mode-0
stimulus, one-hot datapath mapping, byte-local cipher (no diffusion), 256 chosen-plaintext key
extraction, hard-wired baked-in key, 12-round Feistel hardening, Strict Avalanche Criterion,
Abstract-Syntax-Tree (AST) Trojan insertion, spec-driven trigger/payload variants, two-block knock
trigger, key-exfiltration payload, 32-bit master key + round-key schedule, `TROJAN_EN` clean twin,
yosys sequential equivalence, ChromaDB RAG with per-stage decision logging, CVSS v3.1 + CWE,
self-checking `run_all.sh` with SELFTEST_GOLDEN / SELFTEST_EXPLOIT sentinels.

## Required inputs (what the user must provide; do NOT fabricate)
- **REQUIRED:** the challenge bitstream, already placed at `recovery/ice40_bitstream.bin` (fetched
  for you from the official qualifier repo). This is the ONLY input needed for the reverse
  engineering and the Trojan; download nothing else for that work.
- **OPTIONAL (RAG store only):** the two Lattice reference PDFs - FPGA-AN-02109 (iCE40 Design
  Guidelines) and FPGA-TN-02011 (Advanced iCE40 I2C & SPI Hardened IP User Guide). They are
  copyrighted and are NOT auto-downloaded; if the user supplies them (e.g. in `~/Downloads`),
  extract them into `rag/corpus/`, otherwise proceed with the challenge docs + SPI wiki alone.
- If a REQUIRED input is missing, STOP and tell the user exactly what to provide.

## Environment
- The target is a Lattice iCE40-UP5K FPGA holding a small 32-bit SPI cryptographic accelerator. Its RTL is NOT given; recover it from the bitstream.
- Installed tools on PATH: `icepack`, `icebox_vlog`, `iverilog`, `vvp`, `yosys`, `gtkwave`, `python3` (and `pip` for the RAG deps). Use only these plus standard shell.
- Optional references you may read (not required): `recovery/ref_ip_doc.md`, `recovery/ref_micropython.py`. The documented interface: a unified SCK clock (<=1 MHz) clocks the whole design; active-low synchronous RST_N; SPI mode 0, MSB-first; NORM_CS_N is the active-low chip-select / shift-enable; START begins an operation; ENC_DEC selects 0=encrypt / 1=decrypt; BUSY is high for ~4 clocks while processing; ICE_LED mirrors BUSY; MISO/MOSI are serial data. The one published test vector is encrypt(0x59C359C3) = 0x9CD84392.

## GLOBAL RULE: self-test until green. Actually RUN the tools after each stage; never declare success until the compiled simulations pass. All hardware must be AI-written.

## STAGE 1 - Bitstream to a simulatable netlist
1. Unpack with `icepack -u` to the ASCII form. PROVE it is loss-less by repacking with `icepack` and confirming byte-identical to the original (`cmp`).
2. Lift to a structural Verilog netlist with `icebox_vlog`. Expect ~71 flip-flops, four identical `SB_RAM40_4K` block RAMs (the S-box), and 9 top I/O named by location (e.g. io_9_31_1).
3. Make it simulate like real silicon (patched copy):
   - `icebox_vlog` declares the one registered output twice; merge into a single `output reg`.
   - Initialize every fabric flip-flop AND the BRAM output register to 0. Real iCE40 flip-flops power up to 0, but Icarus starts regs at X; without this the datapath fills with X. Copy the iCE40 `cells_sim.v` model from the yosys install (under the OSS CAD Suite share/yosys/ice40 folder) for `SB_RAM40_4K` and set its read-data register initial value to 0.

## STAGE 2 - Pin map and datapath
1. SCK is the net clocking all flip-flops; the LED output is a plain `assign` copy of BUSY; MISO is the only registered output.
2. Resolve RST_N, MOSI, NORM_CS_N, START, ENC_DEC by an AUTOMATED search: a Python harness that generates an Icarus testbench per candidate permutation, simulates, and scores each against (a) the SPI read-back self-test (write 32 bits, read them back, must match) and (b) the "BUSY high ~4 cycles after START" behavior. Keep the permutation satisfying both.
3. Find the 32-bit datapath/shift register by one-hot stimulus: drive a single 1 bit in and diff the flip-flops; each input bit maps to exactly one flip-flop. Record the bit-to-FF map to read the datapath in simulation.

## STAGE 3 - CRITICAL timing, then characterize
- Drive the core race-free, like a real SPI controller: change inputs while SCK is LOW with a real setup delay before each rising edge, and clear START only while SCK is low. If START is de-asserted in the same instant as a clock edge, the pulse widens and the cipher returns wrong (even input-independent) results. This is what makes the netlist reproduce the published vector.
- Using the netlist as a simulation ORACLE driven this way, establish: it is a 32-bit block cipher with a key baked into the bitstream (no key over SPI); ENC_DEC=0 encrypts and 1 decrypts and they are exact inverses; and by flipping one input bit at a time that the cipher is BYTE-LOCAL (each input byte affects only its own output byte) - i.e. four independent 8-bit keyed bijections, no diffusion.

## STAGE 4 - Extract the cipher and recover the key (bonus)
1. Because it is byte-local, capture all four byte-lane permutations in 256 oracle queries: drive the input equal to v repeated in all four bytes (v=0..255) and read the four output bytes. That yields four 256-entry forward tables (encrypt) and their inverses (decrypt); each must be a bijection. Emit them as `$readmemh` hex memory files under rtl/.
2. Document the key: the device has no SPI-loaded key - the secret is (a) the 256-byte S-box read from the BRAM INIT parameters of the netlist and (b) the four lane tables; both must reproduce encrypt(0x59C359C3)=0x9CD84392. Probe the internal round-state registers cycle by cycle to show a non-standard schedule (constant keying bytes appear on alternate cycles), i.e. the key is distributed across S-box and routing rather than one short value; the S-box + tables ARE the recovered key.

## STAGE 5 - Clean reconstruction (reverse-engineering proof)
- Write a clean, synthesizable SPI-peripheral top plus a "recovered-cipher" core that applies the recovered per-byte tables (forward for ENC_DEC=0, inverse for =1). Match the documented pin interface and the 4-cycle BUSY timing: on START capture the operand, hold BUSY 4 clocks, and on BUSY's falling edge parallel-load the result into the shift register for read-out; MISO is a registered copy of the shift-register MSB so a controller sampling on the rising edge reads the word back verbatim. Verify a testbench: this core reproduces encrypt(0x59C359C3)=0x9CD84392 and decrypt inverts encrypt.

## STAGE 6 - HARDEN the cipher (keep the interface identical)
The recovered cipher is weak (no diffusion; breakable one byte at a time). Replace the datapath - keeping the SAME pin interface, 4-cycle BUSY timing, and read-back path - with a strong cipher:
- Use a balanced FEISTEL network (invertible by construction). Split the 32-bit block into two 16-bit halves. The round function: XOR in a 16-bit round key, pass each byte through the recovered 8-bit S-box (nonlinearity), then apply an invertible linear diffusion (XOR of two different left-rotations of the 16-bit word). Use ~12 rounds; expand a 32-bit hard-wired master key into the round keys with a simple schedule.
- SELF-TEST in a small Python reference first: confirm decrypt inverts encrypt over thousands of random inputs, and measure the Strict Avalanche Criterion (flip one input bit, count output-bit flips). Tune the round count and the two rotation amounts until avalanche is ideal - about 16 of 32 output bits (~50%) flip per input-bit flip, uniformly. Then implement the identical cipher in synthesizable Verilog and cross-check the RTL against the Python reference bit-for-bit (encrypt, decrypt, round-trip) over many random vectors.

## STAGE 7 - Insert the hardware Trojan by AST MANIPULATION (not hand-editing)
First emit a completely CLEAN hardened core (no malicious logic). Then write a small,
self-contained Verilog AST tool (a tokenizer + recursive-descent parser, no external
libraries) that parses the clean core into a structural syntax tree, LOCATES and DOCUMENTS
the insertion points, and programmatically inserts the Trojan by transforming the tree and
regenerating the Verilog. Write the located insertion points to a markdown file (e.g.
AST_INSERTION_POINTS.md) so the insertion is documented, not guessed. The Trojan itself:
- TRIGGER: a two-block "knock" in ENCRYPT mode. Processing a first magic plaintext ARMS it; if the VERY NEXT encryption processes a second magic plaintext, it FIRES. Any other operand in between, or a knock in decrypt mode, silently disarms. Choose two 32-bit magic constants (accidental-trigger probability ~2^-64).
- PAYLOAD: precise key exfiltration. On the firing block, load a 32-bit SECRET_KEY register into the result instead of the true ciphertext, so the next read-out streams the key on MISO. Only that one block is affected; normal before and after.
- STEALTH: a tiny 2-bit FSM (armed, fire) plus two 32-bit equality compares and one 32-bit output mux - a couple of flip-flops, no timing/BUSY change, no extra BRAM. Add a parameter (e.g. TROJAN_EN, default enabled) so a "clean twin" with the Trojan disabled can be instantiated.
The AST tool should identify, as tree nodes, these insertion points and explain each: the module PARAMETER list (Trojan constants), the item list before the sequential always block (the armed/fire flip-flops), the reset branch, the operand-capture branch `if (start && !busy)` (where the knock is watched), and the single `data_out` assignment (rewritten into a `fire ? SECRET_KEY : cipher_out` multiplexer). Confirm the AST-produced core still passes the exploit testbench.

## STAGE 8 - Prove it: testbenches
- Validation TB: drive the recovered core over the real SPI protocol and assert encrypt(0x59C359C3)=0x9CD84392 and decrypt(that)=0x59C359C3.
- Exploit TB: instantiate the Trojaned core AND an identical clean twin (Trojan disabled), driven in lock-step over SPI. Assert that for every non-trigger transaction - assorted values plus several dozen random encrypt/decrypt ops - both cores give BIT-IDENTICAL MISO output; that a lone knock, a broken knock sequence, and a decrypt-mode knock never leak; and that after the two-block knock the Trojaned core streams SECRET_KEY on MISO while the clean twin returns the real ciphertext (diverge only at the trigger, then re-converge).

## STAGE 9 - Overhead, report, one-click pipeline
- Measure Trojan cost with `yosys` `synth_ice40`: synthesize the top with the Trojan enabled vs disabled and compare flip-flop counts (expect ~+2 flip-flops, no extra BRAM). On some hosts the final AIGER writer aborts; stop synthesis before LUT mapping to read register/BRAM counts, and treat any writer abort as non-fatal.
- Generate a concise technical report as an HTML file (also render to PDF if a headless Chrome/Edge or wkhtmltopdf is available, else leave HTML). Include: original-RTL analysis and faithfulness proof; how the cipher works and why it is weak (draw weak-vs-hardened avalanche heat-maps as simple SVGs from Python with no external libraries); the hardening and its avalanche; the Trojan trigger/payload/stealth and measured overhead; verbatim simulation logs; reproduction steps; and a severity section with CVSS v3.1 vector AV:L/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:N (base ~9.0, Critical) and CWE mapping CWE-506 (Embedded Malicious Code), CWE-912 (Hidden Functionality), CWE-200 (Exposure of Sensitive Information), CWE-321 (Use of Hard-coded Cryptographic Key). Also write a README.md describing the tree and how to run everything.

## STAGE 10 - Structure it as a 5-stage pipeline with immediate checks
Wrap the work in an orchestrator (pipeline/run_pipeline.py) that runs five stages and asserts a machine-checked result after EACH one:
- Stage 1 Reverse Engineering: in ice40_bitstream.bin -> out build/pin_map.json, build/cipher_characterization.json; check the golden vector 0x59C359C3 -> 0x9CD84392 is reproduced by the netlist oracle.
- Stage 2 Cipher Recovery: in pin_map.json + netlist oracle -> out build/sbox_tables.json (the key material); check each byte lane is a bijection and decrypt == encrypt^-1.
- Stage 3 Hardening Model: in sbox_tables.json + design constraints (from the qualifier repo) -> out hardening/hardened_params.json + rtl/crypto_core_clean.v; check avalanche ~= 0.5 and invertibility over N random vectors.
- Stage 4 Trojan Insertion: in crypto_core_clean.v + hardening/trojan_spec.json -> out rtl/crypto_core.v + hardening/AST_INSERTION_POINTS.md; check tb_trojan passes and the flip-flop overhead is < 3.
- Stage 5 Verification: in all RTL + all testbenches -> out build/verification_report.json; check every prior stage passed.

## STAGE 11 - RAG over the documentation
Install ChromaDB (and sentence-transformers if the platform allows; otherwise use ChromaDB's built-in ONNX all-MiniLM-L6-v2, the same model). Build a vector store (rag/build_vectorstore.py) over: the Lattice iCE40 Design Guidelines (FPGA-AN-02109) and Advanced iCE40 I2C & SPI Hardened IP User Guide (FPGA-TN-02011) PDFs, the challenge documents, and https://simple.wikipedia.org/wiki/Serial_Peripheral_Interface. Expose a query() function (rag/rag_query.py) that the pipeline calls at each stage and that LOGS every retrieval to rag/retrieval_log.jsonl. For EACH stage, record a `decision` field that documents how the retrieved chunks and their distances (lower = closer/more influential) shaped the design decision at that stage; store these per-stage `{query, retrieved:[{source,distance}], decision}` records in the retrieval log AND in build/verification_report.json.
Also create an empty top-level folder named "AI Chat Log" (with a placeholder file so it is tracked) where the AI chat-history links and exported log .zip archives can be uploaded for documentation.

## STAGE 12 - Trojan variants + CVSS comparison
Make the AST inserter spec-driven (trojan_spec.json) supporting triggers {two_block_knock, counter_based, time_bomb} and payloads {key_exfil, output_corruption, bypass}. Generate three variants (knock/key-exfil, counter/corruption, time-bomb/bypass), validate each through the pipeline (fire in simulation vs a TROJAN_EN=0 twin, and flip-flop overhead), compute a CVSS v3.1 score for each, and write a ranked comparison (CVSS, stealth, overhead, effectiveness) to hardening/TROJAN_VARIANTS.md + build/trojan_variants.json.

## STAGE 13 - Formal equivalence proof
Write a yosys script (hardening/equiv_check.ys) that proves crypto_core.v with TROJAN_EN=0 is equivalent to crypto_core_clean.v (the clean-twin stealth guarantee): the Trojan adds no observable behaviour unless enabled and triggered.

## MANDATORY: a self-checking `run_all.sh`
Create an executable `run_all.sh` in the project root that rebuilds and re-simulates everything end to end (running the 5-stage pipeline, the equivalence proof, the variant study, and the report). It must compile with no errors and, at the very end, print these two sentinel lines and exit 0 ONLY if both hold:
- `SELFTEST_GOLDEN: PASS`  - only when the recovered core yields encrypt(0x59C359C3)=0x9CD84392 and the decrypt round-trip matches.
- `SELFTEST_EXPLOIT: PASS` - only when the exploit testbench confirms perfect stealth on all non-trigger traffic AND the knock leaks the key.
If either check fails, print a clear failure line and exit non-zero. Before finishing, RUN `run_all.sh` yourself and keep fixing until it prints both PASS lines and exits 0.

## Suggested layout
rtl/ (top + clean core + AST-inserted Trojaned core + memory files), tb/ (validation + exploit), recovery/ (unpack/lift/extract/key/characterize scripts + the bitstream), hardening/ (cipher design, gen_core, ast_insertion, eval_variants, equiv_check.ys, figures, report builder, trojan_spec.json), pipeline/ (run_pipeline.py), rag/ (corpus, build_vectorstore.py, rag_query.py, retrieval_log.jsonl), build/ (generated JSON artifacts), README.md, LOG.md, and the report. Everything reproducible from `run_all.sh`.
PROMPT_EOF
  echo "prompt written to $PROJECT_DIR/BUILD_PROMPT.md ($(wc -l < "$PROJECT_DIR/BUILD_PROMPT.md") lines)"
}

final_note(){
  if [ -f "$PROJECT_DIR/run_all.sh" ]; then
    say "An AI-generated run_all.sh is present - running the self-test pipeline"
    ( cd "$PROJECT_DIR" && bash run_all.sh ) || warn "run_all.sh reported a failure - see output above"
  else
    say "Environment ready - now hand the clean-room prompt to any AI coding agent"
    cat <<EOF
Next step (model-agnostic, CLEAN-ROOM rebuild):
  1. Open your AI coding agent (e.g. Claude Code) IN this project directory:
        $PROJECT_DIR
  2. Give it exactly this instruction (BUILD_PROMPT.md holds the full spec):
        "run and carry out the following tasks. save all elements generated in
         this $PROJECT_DIR folder"  ->  then read BUILD_PROMPT.md and do it all.
     (BUILD_PROMPT.md already tells the agent it is a clean-room rebuild, what the
      only required input is, and to save every generated file into this folder.)
  3. The agent iterates until it self-tests green; verify with:
        ( cd "$PROJECT_DIR" && bash run_all.sh )
     which must print SELFTEST_GOLDEN: PASS and SELFTEST_EXPLOIT: PASS.
EOF
  fi
}

# ---------------------------------------------------------------------------
main(){
  mkdir -p "$PROJECT_DIR"
  setup_tools
  scaffold
  download_bitstream
  write_prompt
  final_note
  say "DONE"
  echo "Project: $PROJECT_DIR"
  echo "Prompt : $PROJECT_DIR/BUILD_PROMPT.md"
}
main "$@"
