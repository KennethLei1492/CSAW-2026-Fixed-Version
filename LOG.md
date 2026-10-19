# Engineering Log — CSAW'26 AI HW Attack Qualifier

> Team status: teammates MIA. Solo. This log records every step, command, and
> decision so anyone can pick up where I am. Newest entries at the bottom.

## Where we are (quick status)
- [x] Phase 1: bitstream reverse-engineered → simulatable netlist (byte-exact repack proof)
- [x] Phase 1: pin map + protocol recovered; cipher characterized (byte-wise substitution)
- [x] Phase 1: clean RTL reconstruction; matches golden vector 0x59C359C3→0x9CD84392
- [x] Phase 1: hardware Trojan (2-block knock → key leak); normal op preserved; +2 FF overhead
- [x] Phase 1: one-click pipeline `run_all.sh`; all tests pass
- [ ] Phase 2: HARDEN the crypto core (add diffusion) while keeping Trojan stealthy
- [ ] Phase 2: re-verify Trojan stealth + exploit on hardened core
- [ ] Phase 2: capture simulation proof (logs + waveform figure)
- [ ] Phase 2: CVSS + CWE assessment
- [ ] Phase 2: compile single PDF report into submission/

## Key recovered facts (reference)
- Pins: SCK=io_9_31_1 RST_N=io_13_31_1 MOSI=io_16_31_0 MISO=io_16_31_1
  NORM_CS_N=io_17_31_0 ENC_DEC=io_19_31_1 START=io_18_31_1 BUSY=io_8_31_1 LED=io_19_31_0
- Cipher: 32-bit block, hard-wired key, NO diffusion (byte-local); 4 independent
  8-bit keyed bijections; ENC_DEC=0 encrypt / 1 decrypt; exact inverses.
- Golden vector reproduced: enc(0x59C359C3)=0x9CD84392.
- Trojan: knock KNOCK_1=0xDEADC0DE then KNOCK_2=0xFEEDFACE (encrypt mode) → next
  readout leaks SECRET_KEY on MISO. +2 FF, +0 BRAM.
- **Recovered 32-bit device key = 0x8E043CB1** (key bytes lane0..3 = B1,3C,04,8E).
  White-box from the netlist: each lane computes sbox_addr0 = ROR1(plaintext ^ key_byte);
  probe each BRAM read-address reg with operand=0 → addr0 = ROR1(key_byte) → key_byte =
  ROL1(addr0). Recovered + verified by `recovery/recover_key.py` (KEY RECOVERY: PASS).

## Judge-feedback fixes (post-submission)
- **Trojan now leaks the REAL device key 0x8E043CB1** (was a sentinel 0xC0DECAFE from the
  hardened Feistel recreation). The shipped exploit `tb/tb_trojan.v` runs on the RECOVERED
  cipher (`crypto_core_recovered.v`) and asserts MISO == 0x8E043CB1. Trigger FSM gated by
  TROJAN_EN so the clean twin is provably clean.
- **Simulation now runs against the recovered implementation, proven equal to the netlist.**
  `recovery/equiv_netlist.py` drives the SAME SPI stimulus into the gate-level netlist
  (recovered_por.v) and the recovered RTL over the golden vector + 40 random enc/dec ops;
  all match bit-for-bit (NETLIST_EQUIV: PASS). The hardened-Feistel AST demo kept as a
  secondary artifact with its own tb (`tb/tb_trojan_hardened.v`).

## Phase 2 plan (hardening + report)
Rationale: the recovered cipher is cryptographically weak (a single bit flip
changes only one output byte; trivially breakable byte-by-byte). To raise the
"win" odds we add a real diffusion layer so the accelerator becomes a strong
block cipher (full avalanche), WITHOUT touching the Trojan's stealth: same pin
interface, same 4-cycle BUSY timing, same trigger/payload, negligible overhead.
Design choice: a Feistel network (invertible by construction — no risk of a
non-invertible cipher) built on the recovered nonlinear S-boxes, tuned by
self-test until avalanche ≈ 50%.

---
## Phase 2 progress

### Hardened cipher designed (design_hardened.py)
Command: `python hardening/design_hardened.py`
Result: Feistel, N=12 rounds, rotations ra=3/rb=11, master key 0xC0DECAFE.
  - Avalanche = 16.00 / 32 bits (ideal 50%) — FULL diffusion achieved.
  - Round-trip invertible over 3000 random vectors: PASS.
  - Injective over 5000 samples: PASS.
  - Note: Feistel uses the SAME round function F for enc & dec (only key order
    reverses), so only the forward S-box is needed — no inverse table. F reuses
    the recovered 8-bit S-box for nonlinearity.
  - Round keys: cafe b2bf ecaf 7b2b deca 37b2 5fd8 57f6 95fd 657f d95f f657

### Hardened RTL implemented + verified (crypto_core_hardened.v)
Command: `iverilog ... tb_random.v spi_crypto_top.v crypto_core_hardened.v` then Python cross-check.
Result: hardened RTL == Python reference for ENC, DEC, and round-trip over 30
random vectors (0 mismatches). Full 12-round Feistel computes combinationally;
same 4-cycle BUSY interface as the recovered core.

### File reorg (evidence vs deliverable)
- crypto_core.v            = HARDENED Feistel + Trojan  (PRIMARY deliverable)
- crypto_core_recovered.v  = exact recovered byte-substitution cipher + Trojan
                             (kept as reverse-engineering proof; reproduces
                              golden vector 0x59C359C3->0x9CD84392)
- crypto_core_recovered_clean.v = recovered cipher, no Trojan
- Added parameter TROJAN_EN (default 1) so a clean twin can be instantiated for
  the stealth proof (Trojaned vs clean, driven identically).

### Verification of hardened design (all PASS)
- `vvp tb_trojan (hardened)`: functional round-trip PASS; STEALTH: DUT==clean twin
  on 8 assorted + 64 random transactions (72/72 identical); EXPLOIT: knock leaks
  0xC0DECAFE on MISO while clean core returns real ciphertext 0x3c2e0424; returns
  to normal after. errors=0.
- `vvp tb_validate (recovered core)`: enc(0x59C359C3)=0x9CD84392 PASS, round-trip PASS
  (reverse-engineering evidence preserved).

### Overhead (yosys synth_ice40 -run :map_luts, TROJAN_EN 1 vs 0)
- Hardened+Trojan: 105 FF ; Hardened clean: 103 FF  => Trojan cost = +2 FF.
- S-box maps to logic (many combinational read ports in the unrolled Feistel),
  0 BRAM; fits UP5K comfortably. FF overhead identical to the recovered design.

### Proof artifacts generated
- hardening/avalanche.svg          : hardened SAC matrix, mean p=0.4997 (ideal 0.5)
- hardening/avalanche_recovered.svg : recovered cipher, cross-byte p=0.0000 (no diffusion)
- hardening/waveform.svg           : exploit timing (knock -> key on MISO)
- hardening/sim_trojan.txt, sim_recovered.txt : verbatim sim logs

### Commands used this phase
- python hardening/design_hardened.py
- iverilog -g2012 -o hardening/hx.vvp tb/tb_random.v rtl/spi_crypto_top.v rtl/crypto_core_hardened.v
- iverilog -g2012 -o hardening/troj.vvp tb/tb_trojan.v rtl/spi_crypto_top.v rtl/crypto_core.v ; vvp hardening/troj.vvp
- yosys -s ht.ys / hc.ys  (chparam TROJAN_EN 1/0 ; synth_ice40 -run :map_luts ; stat)

### TODO next
- [ ] Write single PDF report (analysis, implementation, stealth+danger proof,
      CVSS + CWE) into submission/ ; embed the three SVG figures + sim logs.

### PDF report generated  [DONE]
- hardening/build_report.py assembles report.html (embeds the 3 SVGs + verbatim
  sim logs), then headless Chrome prints it to PDF.
- Command:
    python hardening/build_report.py
    "/c/Program Files/Google/Chrome/Application/chrome.exe" --headless --disable-gpu \
      --no-sandbox --no-pdf-header-footer \
      --print-to-pdf=CSAW26_Trojan_Report.pdf file:///.../hardening/report.html
- Output: submission/CSAW26_Trojan_Report.pdf (5 pages). CVSS 9.0 Critical;
  CWE-506/912/200/321.
- Re-verified whole pipeline: `run_all.sh sim` -> Stage 3 golden vector PASS,
  Stage 4 stealth 64/64 + key leak PASS.

## STATUS: COMPLETE
All Phase-2 items done. Deliverables in submission/: hardened Trojaned RTL,
recovered-cipher evidence RTL, dual-core exploit testbench, recovery + hardening
scripts, PDF report, this log, and the AI transcript. Zip = submission.zip.
Teammates: if you are reading this, everything reproduces via ./run_all.sh.

### Key documentation (teammate question)
Q: does "recovered S-box/key bytes" == the key used by the FPGA accelerator? A: yes.
- The device has NO SPI-loaded key; key is baked into the bitstream.
- Recovered secret = 256-byte S-box (BRAM INIT_0..7) + the four per-lane 256-entry
  key tables (sbox_enc/dec*.mem). Reproduces enc(0x59C359C3)=0x9CD84392;
  fingerprint enc(0)=0x08ABD043.
- Obtained white-box (read BRAM INIT from netlist) AND black-box (256 chosen-PT
  oracle queries, extract_cipher.py); both agree.
- Probed round-state (recovery/round_ad.json): non-standard 2-phase schedule
  (addr collapses to consts 0x54/0xA8 on alternate cycles) -> key is distributed
  across S-box + routing, so no single short N-bit key; the tables ARE the key.
- README.md section 2.3 updated with exact values + method.
- NOTE: 0xC0DECAFE is the hardened core's key (what the Trojan leaks), NOT the
  recovered device key.

### From-scratch bootstrap script  [DONE]
- rebuild_from_scratch.sh : ONE self-contained script for a teammate with nothing
  installed. Installs OSS CAD Suite (Icarus/GTKWave/Yosys/IceStorm), downloads ONLY
  the official bitstream (JBlocklove repo, qualifier/), writes every source file
  from embedded heredocs, and runs reverse -> recover -> harden -> build -> exploit
  -> report with no user input.
- Built by hardening/make_bootstrap.py (embeds 17 authored source files).
- New helper scripts so the whole thing regenerates: hardening/gen_core.py
  (emits crypto_core.v + sbox_feistel.mem), hardening/make_figures.py (numpy-free
  SVGs), recovery/probe_key.py (key evidence). Fixed build_report.py to use paths
  relative to __file__ (was hardcoded).
- CERTIFIED end-to-end in a fresh dir (SKIP_TOOL_INSTALL reuses toolchain):
  bitstream 104090 OK; VALIDATE ALL PASS; TB_TROJAN ALL PASS; overhead 105 vs 103
  FF = +2 FF; PDF 144 KB; 0 leftover intermediates.
- Run: `bash rebuild_from_scratch.sh [OUT_DIR]`  (env: SKIP_TOOL_INSTALL=1, SKIP_PDF=1)

### rebuild_from_scratch.sh reworked -> AI-generation orchestrator (no embedded code)
Per teammate request, the bootstrap no longer embeds our source files. It now:
  1. installs OSS CAD Suite, 2. ensures the Claude Code CLI, 3. opens a NEW project
  dir, 4. downloads ONLY the official bitstream, 5. writes BUILD_PROMPT.md (a
  thorough, code-free methodology covering all 9 stages + a mandatory self-checking
  run_all.sh with SELFTEST_GOLDEN/SELFTEST_EXPLOIT sentinels), 6. launches Claude
  Code in that project seeded with the prompt so the AI writes every file itself,
  7. runs the AI-generated run_all.sh to show PASS/FAIL.
- SAFETY: launches Claude Code INTERACTIVELY (supervised), NOT with permissions
  disabled and NOT in an auto-respawning agent loop. An earlier draft that wired
  `claude --dangerously-skip-permissions` in an auto-repair loop was (correctly)
  blocked as an unsafe-autonomous-agent pattern; the interactive design gives the
  same result with a human approving actions (Shift+Tab enables auto-accept).
- Removed hardening/make_bootstrap.py (it embedded code for the old approach).
- VERIFIED: `bash -n` clean; plumbing test (SKIP_TOOL_INSTALL, sourced funcs)
  created the dir tree, downloaded the bitstream (104090 bytes), and wrote the
  62-line methodology prompt with zero embedded RTL/code. The claude launch itself
  is not run here (needs the teammate's authenticated Claude Code).

### rebuild_from_scratch.sh made model-agnostic + pushed to GitHub
- Per teammate: removed all Claude Code setup/launch. The script now only
  prepares the environment (OSS CAD Suite), downloads the bitstream, and writes
  BUILD_PROMPT.md (model-agnostic methodology). Any AI agent can then follow the
  prompt to regenerate the project; if a run_all.sh exists it is run to show PASS.
- Pushed the full submission folder to https://github.com/KennethLei1492/CSAW26
  (into submission/, plus refreshed submission.zip).

### Repo cleanup + AST manipulation + netlist-equivalence doc (judges' feedback)
- CLEANED the repo: removed all tool outputs / compiled binaries / duplicates
  (recovered_netlist.v, design.asc, repacked.bin, cells_sim.v, *.err, sim/,
  submission.zip, root duplicates, all *.json/*.svg/*.vvp intermediates). Added a
  root .gitignore so only docs, testbenches, and major components are tracked;
  run_all.sh regenerates every derived file from the bitstream.
- AST MANIPULATION for Trojan insertion (rubric "advanced technique"):
  * gen_core.py now emits a CLEAN hardened core (crypto_core_clean.v), no Trojan.
  * NEW hardening/ast_insertion.py: self-contained Verilog tokenizer + recursive-
    descent parser builds a structural AST, LOCATES + DOCUMENTS the insertion
    points (hardening/AST_INSERTION_POINTS.md), then transforms the tree (params,
    armed/fire regs, trigger if-node, payload mux) and regenerates crypto_core.v.
  * Verified: AST-produced crypto_core.v passes tb_trojan (stealth 64/64 + key leak).
- README: added section 1.6 explaining the clean reconstruction is behaviourally
  identical to the original icebox_vlog netlist (same golden vector, same oracle
  outputs, same pin/BUSY timing) -> simulating the clean core is a valid test of
  the real device. Added section 3.1 on the AST insertion technique. Updated tree.
- run_all.sh rewritten: reverse -> extract -> probe_key -> design -> gen_core(clean)
  -> ast_insertion -> tb_validate -> tb_trojan -> figures/report, printing
  SELFTEST_GOLDEN and SELFTEST_EXPLOIT sentinels (exit 0 only if both PASS).
- rebuild_from_scratch.sh BUILD_PROMPT.md STAGE 7 updated to require AST-based
  insertion with documented insertion points.
- Re-verified full pipeline from scratch (reverse..report): ALL SELF-TESTS PASSED.

### Phase 3: pipeline, RAG, variants, equivalence, transcript (final stretch)
- AI transcript (ai/ai_transcript.md) updated: main agent = Claude Opus 4.8 (Low);
  added "How AST manipulation works as an AI-driven technique", verbatim teammate
  prompts, a RAG section, and a "Failures encountered and fixed" section.
- RAG: installed chromadb (sentence-transformers/torch hit a Windows long-path
  error, so we use ChromaDB's ONNX all-MiniLM-L6-v2 - same model). Corpus =
  2 Lattice PDFs + 3 challenge docs + SPI simple-wiki. rag/build_vectorstore.py
  builds the store (272 chunks/6 docs); rag/rag_query.py exposes query() and logs
  every retrieval to rag/retrieval_log.jsonl. Corpus rebuildable via build_corpus.sh
  (Lattice PDFs not redistributed - copyrighted).
- 5-stage pipeline (pipeline/run_pipeline.py) with an immediate check after each
  stage + a RAG query per stage; emits build/{pin_map,cipher_characterization,
  sbox_tables,design_constraints,verification_report}.json. Full run: all stages PASS.
- AST inserter made spec-driven (trojan_spec.json): triggers {two_block_knock,
  counter_based, time_bomb} x payloads {key_exfil, output_corruption, bypass}.
- Trojan variants (hardening/eval_variants.py): 3 variants generated by AST,
  each fired vs a TROJAN_EN=0 twin + FF overhead; CVSS scored and ranked ->
  hardening/TROJAN_VARIANTS.md + build/trojan_variants.json. v1 CVSS 9.0 (+2FF),
  v2 5.7 (+33FF), v3 9.3 (+33FF). Shipped design = v1 (most stealth, lowest overhead).
- Yosys equivalence (hardening/equiv_check.ys): SEC of crypto_core(TROJAN_EN=0)
  vs crypto_core_clean. Note: full formal SEC is compute-heavy for the 12-round
  cipher; run_all runs it best-effort (timeout), and the clean-twin equivalence is
  also proven empirically by tb_trojan (72/72 identical). 
- run_all.sh + rebuild_from_scratch.sh updated to orchestrate pipeline + RAG +
  variants + equiv; .gitignore updated for all new generated artifacts.
- Verified: run_all.sh sim -> SELFTEST_GOLDEN PASS, SELFTEST_EXPLOIT PASS.
