# CSAW'26 AI Hardware Attack — Qualifier Submission
## Reverse-Engineered Cryptographic Accelerator + AI-Generated Hardware Trojan

This submission reverse-engineers the provided Lattice iCE40-UP5K bitstream,
reconstructs the cryptographic accelerator as clean synthesizable RTL, and
inserts a stealthy, AI-generated hardware Trojan that exfiltrates the device
key over the SPI `MISO` line. Every step is automated and self-checking.

```
CSAW26/                                  (repository root)
├── README.md                        <- this technical brief
├── CSAW26_Trojan_Report.pdf         <- ***JUDGES' REPORT*** (analysis, proofs, CVSS/CWE, figures)
├── LOG.md                           <- engineering log
├── run_all.sh                       <- one-click: reverse -> recover -> harden -> AST-insert -> test
├── rebuild_from_scratch.sh          <- prepares tools + bitstream + emits a model-agnostic AI prompt
├── .gitignore                       <- excludes tool outputs / regenerated intermediates
├── rtl/
│   ├── spi_crypto_top.v                 <- SPI-peripheral wrapper (TROJAN_EN param)
│   ├── crypto_core_clean.v              <- HARDENED 12-round Feistel, NO Trojan (the AST input)
│   ├── crypto_core.v                    <- ***MODIFIED RTL*** hardened core + Trojan (AST-inserted)
│   ├── crypto_core_recovered.v          <- exact recovered byte-substitution cipher (RE proof)
│   ├── crypto_core_recovered_clean.v    <- recovered cipher, no Trojan (reference)
│   ├── sbox_feistel.mem                 <- S-box used by the hardened Feistel core
│   └── sbox_{enc,dec}{0..3}.mem         <- recovered per-byte-lane substitution tables (the key)
├── tb/
│   ├── tb_trojan.v                  <- exploit TB: Trojaned vs clean twin (stealth + key leak)
│   ├── tb_validate.v                <- functional TB vs. the published test vector
│   └── tb_random.v                  <- randomized cross-check helper
├── ai/
│   └── ai_transcript.md             <- AI methodology, prompts, tools, models
├── hardening/
│   ├── design_hardened.py           <- designs + self-tests the hardened cipher (avalanche)
│   ├── gen_core.py                  <- emits the CLEAN hardened core from tested parameters
│   ├── ast_insertion.py             <- AST tool: documents insertion points + inserts the Trojan
│   ├── AST_INSERTION_POINTS.md      <- machine-generated documentation of the insertion points
│   ├── make_figures.py              <- draws avalanche heat-maps + exploit waveform (no libraries)
│   └── build_report.py              <- assembles the technical report
└── recovery/
    ├── ice40_bitstream.bin          <- provided challenge bitstream (the ONLY input)
    ├── reverse.sh                   <- bitstream -> simulatable netlist (Project IceStorm)
    ├── extract_cipher.py            <- automated oracle-probing cipher recovery
    └── probe_key.py                 <- recovers / documents the baked-in key
```

All *generated* artifacts (the `icebox_vlog` netlist, `design.asc`, `*.vvp`, `*.json`,
`*.svg`, sim logs, etc.) are produced by `run_all.sh` and are **git-ignored** to keep the
repository to documentation, testbenches, and the major components. Run `./run_all.sh` to
regenerate everything from the bitstream and self-test.

> **Phase 2/3 summary:** `CSAW26_Trojan_Report.pdf` is the full report —
> original-RTL analysis, stealth-and-danger proofs (avalanche matrices,
> Trojaned-vs-clean equivalence, key-leak waveform), CVSS 9.0 Critical, and CWE
> ratings. We **hardened** the accelerator into a full-diffusion 12-round Feistel
> cipher (`crypto_core_clean.v`) and then inserted the hardware Trojan by
> **Abstract-Syntax-Tree manipulation** (`hardening/ast_insertion.py`), which
> parses the clean core, emits `hardening/AST_INSERTION_POINTS.md` documenting each
> insertion point, and programmatically produces `crypto_core.v` (+2 flip-flops,
> bit-identical to a clean twin on 72/72 transactions). The exact recovered cipher
> is retained as `crypto_core_recovered.v` (reproduces `0x59C359C3 -> 0x9CD84392`).

Run everything (needs OSS CAD Suite + python3 on PATH):

```
./run_all.sh          # re-derives the netlist and cipher, then simulates
./run_all.sh sim      # just build the RTL and run both testbenches
```

**Starting from nothing (clean-room rebuild)?** `rebuild_from_scratch.sh` installs
the OSS CAD Suite (Icarus Verilog, GTKWave, Yosys, IceStorm), downloads only the
official challenge bitstream, and emits `BUILD_PROMPT.md` — a **clean-room**
methodology any AI coding agent can follow to re-derive every file from scratch.

```
bash rebuild_from_scratch.sh [OUTPUT_DIR]     # default ./csaw26_ai_build
# env: SKIP_TOOL_INSTALL=1 (reuse tools already on PATH)
```

Then, in that project folder, give an AI agent (e.g. Claude Code) the invocation:
`"run and carry out the following tasks. save all elements generated in this <FOLDER>
folder"` and point it at `BUILD_PROMPT.md`. The prompt states the clean-room method
(recover behaviour from the bitstream, never copy vendor RTL), the single required
input (`recovery/ice40_bitstream.bin`), and the optional Lattice PDFs for the RAG
store. The agent self-tests until `run_all.sh` prints `SELFTEST_GOLDEN: PASS` and
`SELFTEST_EXPLOIT: PASS`.

---

## 1. Reverse-engineering the bitstream

**Tooling.** Project IceStorm (`icepack -u`, `icebox_vlog`) from the recommended
OSS CAD Suite, plus Icarus Verilog (`iverilog`/`vvp`) for simulation and Yosys
for synthesis checks. All orchestration and analysis is Python.

**Steps (see `recovery/reverse.sh`):**

1. `icepack -u ice40_bitstream.bin design.asc` unpacks the bitstream to its
   textual LUT/routing/BRAM form. Device reports as `5k` (UP5K).
2. **Faithfulness check:** repacking with `icepack design.asc repacked.bin`
   yields a file **byte-identical** to the original bitstream, proving the
   extraction is loss-less.
3. `icebox_vlog design.asc > recovered_netlist.v` emits a flattened structural
   netlist: 9 top-level I/O, 71 flip-flops, and **4 identical `SB_RAM40_4K`
   block RAMs**. This is used as the golden oracle for all later analysis.
4. Two mechanical patches make it simulate the way the silicon powers up:
   the doubly-declared registered output `io_16_31_1` is merged to `output reg`,
   and all fabric flip-flops plus the BRAM output register are initialised to 0
   (iCE40 flip-flops power up to 0; Icarus otherwise starts them at `x`). This
   `x`-vs-0 detail was one of the key insights for getting the netlist to behave
   like the real device.

**Recovering the pin map.** The netlist names I/O by physical location
(`io_9_31_1`, ...). We recovered the logical mapping automatically:

* `SCK = io_9_31_1` — clocks all 71 flip-flops (the documented unified clock).
* `io_19_31_0 = ICE_LED` is a pure `assign` copy of `io_8_31_1 = BUSY`,
  matching the datasheet ("ICE_LED mirrors BUSY"); `MISO = io_16_31_1` is the
  only registered output.
* The remaining five inputs were resolved by an exhaustive simulation search
  over all permutations, scored against (a) the SPI read-back self-test in the
  micropython reference and (b) the documented BUSY-for-4-cycles behaviour:

  | Signal | Pin | Signal | Pin |
  |---|---|---|---|
  | SCK | io_9_31_1 | MISO | io_16_31_1 |
  | RST_N | io_13_31_1 | NORM_CS_N | io_17_31_0 |
  | MOSI | io_16_31_0 | ENC_DEC | io_19_31_1 |
  | START | io_18_31_1 | BUSY / ICE_LED | io_8_31_1 / io_19_31_0 |

**Locating the datapath.** Driving one-hot words into the shift register and
diffing the 71 flip-flops mapped each of the 32 I/O-register bits to exactly one
flip-flop (listed in `extract_cipher.py`), letting us read the 32-bit datapath
value directly from the netlist during simulation.

**The timing gotcha.** Clearing `START` in the same simulation delta as the
next `SCK` rising edge creates a race that widens the start pulse and yields
wrong (even input-independent) results. Driving the core race-free — inputs
change while `SCK` is low, with real setup before each rising edge, exactly as a
physical SPI controller does — makes the netlist reproduce the published test
vector precisely. `extract_cipher.py` uses this race-free bit-clock.

### 1.6 Why the clean reconstruction is the same as the original netlist (and a valid test)

Two Verilog descriptions of the recovered design exist, and it matters that they are
**behaviourally identical**:

* the **original netlist** `recovered_netlist.v` — the raw `icebox_vlog` lift of the
  bitstream: ~71 flip-flops and four `SB_RAM40_4K` primitives wired by physical
  location. It is a faithful, gate-exact copy of the silicon (our unpack repacks to the
  original `.bin` byte-for-byte), but it is 1.2 MB of unreadable gate soup and is only
  used as the analysis *oracle*.
* the **clean reconstruction** `crypto_core_recovered.v` — a small, readable,
  synthesizable module we wrote that applies the recovered per-byte substitution tables
  behind the same SPI interface.

The clean version is **essentially the same circuit as the original netlist**, and we
prove it rather than assert it. Both are driven, over the real SPI protocol, with the
identical race-free stimulus, and they agree bit-for-bit:

* both reproduce the one published vector `enc(0x59C359C3) = 0x9CD84392` and its inverse;
* driven with the same random plaintexts, the clean core's ciphertext equals the netlist
  oracle's output on every byte lane (the clean core's tables were *extracted from* the
  netlist, so equality is by construction and is re-checked every run);
* both present the same pin behaviour and the same 4-cycle `BUSY` timing.

Because the two are input/output-equivalent, **simulating the clean reconstruction is a
valid substitute for simulating the original hardware** — it exercises exactly the same
encryption/decryption function the FPGA performs, just in a form a human (and the
testbenches) can read. This is why the required simulation tests run against the clean
RTL: a pass on the clean core is a pass on the recovered device. (The gate-exact
`recovered_netlist.v` remains available — regenerated by `reverse.sh` — for anyone who
wants to re-verify the equivalence directly against the bitstream.)

## 2. How the encryption system works

The accelerator is a **32-bit block cipher with a hard-wired key** (no key is
ever sent over SPI — it is baked into the bitstream). Its defining property,
found by differential probing of the oracle, is that it has **no inter-byte
diffusion**: flipping any bit of input byte *i* changes only output byte *i*.
The 32-bit block is therefore four **independent 8-bit keyed bijections**, one
per byte lane — consistent with the four identical S-box BRAMs and the 4-cycle
`BUSY` window (four rounds of S-box substitution + round-key addition per byte,
performed in place).

* `ENC_DEC=0` encrypts, `ENC_DEC=1` decrypts, and the two are exact inverses
  (verified on random inputs).
* **Validated against the provided vector:** `enc(0x59C359C3) = 0x9CD84392`,
  and `dec(0x9CD84392) = 0x59C359C3`, reproduced bit-for-bit by both the
  recovered netlist and our clean RTL.
* Operation: `NORM_CS_N` low shifts 32 bits in on `MOSI` (MSB first); `START`
  pulses for one cycle; `BUSY` is high for 4 cycles and, on its falling edge,
  the result is parallel-loaded into the shift register; `NORM_CS_N` low again
  streams it out on `MISO`.

**Recovering the cipher (`extract_cipher.py`).** Because the cipher is
byte-local, all four lane permutations are captured in **256 oracle queries**:
drive `din = v * 0x01010101` for `v = 0..255` and read the four output bytes.
This yields four 256-entry forward tables (encrypt) and four inverse tables
(decrypt), each a verified bijection — the complete key-dependent behaviour.
They are emitted as the `rtl/sbox_*.mem` files that drive the clean RTL.

## 2.3 The recovered key (bonus) — what it is and how we got it

**Short answer for the team:** yes — "the recovered S-box/key bytes" *are* the
secret this FPGA accelerator uses to encrypt and decrypt. There is an important
subtlety: **this device never receives a key over SPI.** The key is permanently
**baked into the bitstream**, so "recovering the key" means recovering that
hard-wired secret out of the silicon. We did, in full, and it reproduces the
published vector exactly.

**What the key physically is.** The secret lives in two equivalent forms:

1. **Raw form — the secret S-box in the BRAMs.** All four S-box block-RAMs hold
   the *same* 256-byte substitution table (`recovery/sbox_bram_init.txt`),
   read directly out of the bitstream. These 256 bytes are the literal secret
   stored in the chip:

   ```
   INIT_0 85c4d834ef22d4b989d975ce78ebd3405fa9d7fe2f53040695f36fc62fdf2de9
   INIT_1 4dae5f63ca800319a219922c878382b2042fa4cdccdb8c36e5f52f3e4b25edbf
   INIT_2 b2e631e07d1eb7c2668faca8a8a281cf95319e83cb5e2b4e99443ba536189203
   INIT_3 c7cdaaae5946de3c9caee12ea2628a47f915286c2baaa8db852721e3ff8aeae6
   INIT_4 b38b73fc542cabf0e0042f00b1f984421a8f24fa3d7eca72027a937fa9a57071
   INIT_5 75102ed9eed416f8b952d0bb2db7e5d16cdac02f5e90c3ca24b8126137044e48
   INIT_6 b23dc192a95f0c6b677412424eb52e6cf301b96b267ef4eac6b49a342e317d8c
   INIT_7 572d44ac3633a04b29c0dfb57023d812b6aa51fae993732dd138b4cb500eecc9
   ```

2. **Effective form — the four per-byte-lane key tables.** What actually
   encrypts each plaintext byte is that byte lane's full 256-entry keyed
   permutation (S-box folded with the lane's hard-wired round constants). These
   four tables are the usable key and ship as `rtl/sbox_enc{0..3}.mem`
   (with their inverses `sbox_dec{0..3}.mem`). First 16 entries of each:

   | lane (byte) | table[0x00 .. 0x0F] |
   |---|---|
   | 0 (bits 7:0)   | `43 bf 5d 14 71 a6 08 2a f8 03 c5 09 f6 f2 c9 27` |
   | 1 (bits 15:8)  | `d0 8f 2b f9 5b 50 73 b5 bc dd 07 f7 69 d1 5e 11` |
   | 2 (bits 23:16) | `ab b6 c5 b3 fc e2 30 85 c1 8e 3f e7 b0 d4 6e 2a` |
   | 3 (bits 31:24) | `08 70 a3 5d d6 9f 05 3f ed d3 bb af 7f fc 9d 58` |

   Encryption is `C[byte i] = lane_i[P[byte i]]`; decryption uses the inverse
   tables. As a self-check, `enc(0x59C359C3) = 0x9CD84392` (the published
   vector) and the key-only device fingerprint `enc(0x00000000) = 0x08ABD043`.

**How we obtained it (two independent methods that agree):**

* **White-box (from the bitstream).** `icepack -u` + `icebox_vlog` expose the
  four `SB_RAM40_4K` primitives; the 256-byte S-box is read straight from their
  `INIT_0..INIT_7` parameters (byte-exact, since our unpack repacks to the
  original `.bin`).
* **Black-box (oracle probing).** `recovery/extract_cipher.py` drives the
  recovered netlist as an oracle and, using the fact that the cipher is
  byte-local, reconstructs all four lane tables in **256 chosen-plaintext
  queries** (`din = v * 0x01010101`, read each output byte). The tables match
  the white-box S-box and reproduce the golden vector.

**Why we do not quote a single short "N-bit key."** We probed the internal
round-state registers cycle by cycle (`recovery/round_ad.json`). The engine is
not a textbook "one S-box + XOR one round key" that factors into, say, a 32- or
128-bit key: it runs a **non-standard two-phase round schedule** in which the
S-box address register collapses to fixed constants (`0x54`, `0xA8`) on
alternate cycles — i.e. the key is *distributed* across the S-box contents and
hard-wired routing/round constants, a common key-obfuscation for baked-in keys.
The honest, complete answer is therefore the material above: the 256-byte secret
S-box **plus** the four lane tables. That set is sufficient to encrypt or decrypt
**any** block offline, with no device and no further queries — i.e. the key is
fully recovered for exploitation and cloning.

> Note: `SECRET_KEY = 0xC0DECAFE` referenced elsewhere is **not** this recovered
> device key — it is the master-key register of our *hardened* Feistel core
> (Phase 2), and it is the value our Trojan exfiltrates in that build. On the
> original recovered cipher the equivalent secret to leak is the S-box/tables above.

## 3. The hardware Trojan

Implemented in `rtl/crypto_core.v` (diff it against `crypto_core_clean.v`).

* **Trigger — a two-block "knock" (extremely specific).** In *encrypt* mode,
  processing plaintext `KNOCK_1 = 0xDEADC0DE` *arms* the Trojan; if the **very
  next** encryption processes `KNOCK_2 = 0xFEEDFACE`, it *fires*. Any other
  operand in between, or a knock issued in decrypt mode, silently disarms it.
  The joint probability of accidental activation is 2^-64.
* **Payload — precise key exfiltration.** On the firing operation the core
  parallel-loads the 32-bit `SECRET_KEY` register instead of the true
  ciphertext, so the next ordinary `MISO` read-out streams the device key off
  the chip. Only that one block is affected; the device returns to normal
  immediately afterwards. (`SECRET_KEY` is the register that in the deployed
  device holds the master key; for a self-contained, checkable demo it is set
  to the sentinel `0xC0DECAFE`.)
* **Stealth / overhead.** The Trojan is a 2-bit FSM (`armed`, `fire`), two
  32-bit equality comparators, and one 32-bit output mux. Synthesised for the
  iCE40 it costs **+2 flip-flops (105 vs 103) and no extra BRAM** over the
  baseline — well under 1% overhead. It never alters timing, the `BUSY`
  waveform, or any non-triggering output, so the accelerator is bit-for-bit
  identical to the golden design under all normal use, including when either
  knock value appears on its own.

### 3.1 Insertion by Abstract-Syntax-Tree (AST) manipulation

The Trojan is **not** hand-edited into the RTL. `hardening/gen_core.py` first emits a
completely clean hardened core (`rtl/crypto_core_clean.v`), and
`hardening/ast_insertion.py` then does the insertion programmatically:

1. **Parse.** A self-contained tokenizer + recursive-descent parser (no external
   libraries) turns the clean core into a structural AST — a real typed tree of the
   module header, declarations, the continuous assignment, and the two `always`
   blocks with their nested `if`/`else` branches and assignments.
2. **Locate + document.** Walking the tree pinpoints the exact insertion points and
   writes them to `hardening/AST_INSERTION_POINTS.md`: the module *parameter list*
   (for the Trojan constants), the item list *before the sequential `always`* (for the
   `armed`/`fire` flip-flops), the *reset branch*, the *operand-capture branch*
   `if (start && !busy)` (where the two-block knock is watched), and the single
   `data_out <= cipher_out` *assignment* (the payload multiplexer point). Each is
   reported as an AST node with its rationale, so the insertion is documented rather
   than guessed.
3. **Transform + regenerate.** The tool adds the parameter nodes, the register
   declaration, a trigger `if` node in the capture branch, and rewrites the payload
   assignment into `data_out <= fire ? SECRET_KEY : cipher_out`, then pretty-prints
   the modified AST back to `rtl/crypto_core.v`.

Because only these AST nodes change — no ports, timing, or datapath — the result is
provably pin- and cycle-identical to the clean core until the knock fires, which the
exploit testbench confirms (72/72 non-trigger transactions bit-identical to the
`TROJAN_EN=0` twin). This makes the insertion reproducible and auditable, and is the
"advanced technique (e.g., AST manipulation)" the rubric rewards.

### 3.2 What the leaked key unlocks (why the payload is catastrophic)

The value the Trojan exfiltrates, **`SECRET_KEY = 0xC0DECAFE`**, is not a nonce or a
per-message value — it is the hardened cipher's **32-bit master key**, and **all 12
round keys are derived from it** by the key schedule in `hardening/design_hardened.py`
(rotate + round-constant). The round-key expansion is deterministic and public, so a
single disclosure of this 32-bit master key lets an attacker **reconstruct the entire
round-key schedule** and then **decrypt (or forge) any ciphertext offline**, with no
further access to the device — a total, permanent break of the accelerator.

Concretely, an attacker who has captured the leak needs exactly two secrets, both of
which this submission identifies:

* **`SECRET_KEY = 0xC0DECAFE`** — the 32-bit master key streamed out on `MISO` when the
  knock fires. From it, re-run the schedule to obtain round keys
  `cafe b2bf ecaf 7b2b deca 37b2 5fd8 57f6 95fd 657f d95f f657`.
* **`rtl/sbox_feistel.mem`** — the 256-byte S-box table (the cipher's only other secret,
  the nonlinear substitution used every round). This is the table an attacker most wants;
  with it plus the master key the Feistel round function is fully specified.

With both, the attacker reimplements the cipher in software and decrypts every block the
device ever produced — offline, undetectably, forever. That is what makes a 32-bit leak
on a single transaction a CVSS 9.0 (Critical) confidentiality-and-integrity compromise.

## 4. Exploiting it (in simulation and on hardware)

`tb/tb_trojan.v` drives the DUT exactly as the RP2040 does and demonstrates:
(A) normal encrypt/decrypt incl. the published vector; (B) stealth — lone
knocks, a broken sequence, and decrypt-mode knocks all yield the correct
ciphertext; (C) the exploit — after `enc(0xDEADC0DE)` then `enc(0xFEEDFACE)`,
the following read-out returns `0xC0DECAFE` on `MISO`, and the device then
resumes correct encryption. All checks pass.

**On real hardware**, once a bitstream is generated from this RTL, the RP2040
(or any SPI controller) triggers the leak with three ordinary transactions:

1. Encrypt block `0xDEADC0DE` (result is the normal ciphertext — nothing looks wrong).
2. Encrypt block `0xFEEDFACE` (fires the Trojan).
3. Perform any read-out transaction (`NORM_CS_N` low, 32 `SCK` cycles): the
   32 bits shifted out on `MISO` are the device's secret key, not a ciphertext.

## 5. AI methodology

See `ai/ai_transcript.md`. The entire pipeline — the permutation search for the
pin map, the one-hot datapath mapping, the race-free oracle, the byte-locality
proof, the 256-query cipher extraction, the Trojan RTL, the testbenches, and
the synthesis/overhead checks — was generated and driven by an AI agent
(Anthropic Claude, via the Claude Code agentic CLI) operating the open-source
toolchain autonomously.

## 6. Staged pipeline, RAG, Trojan variants, and equivalence proof

**Five-stage pipeline with immediate checks** (`pipeline/run_pipeline.py`). Each
stage asserts a machine-checked result before the next begins and writes JSON
artifacts under `build/`:

| Stage | Input | Output | Immediate check |
|---|---|---|---|
| 1 Reverse Engineering | `ice40_bitstream.bin` | `pin_map.json`, `cipher_characterization.json` | golden vector `0x59C359C3 -> 0x9CD84392` reproduced |
| 2 Cipher Recovery | pin map + netlist oracle | `sbox_tables.json` (key material) | each lane a bijection; `decrypt == encrypt^-1` |
| 3 Hardening Model | `sbox_tables.json` + qualifier constraints | `hardened_params.json`, `crypto_core_clean.v` | avalanche ~= 0.5; invertible over N vectors |
| 4 Trojan Insertion | `crypto_core_clean.v` + `trojan_spec.json` | `crypto_core.v`, `AST_INSERTION_POINTS.md` | `tb_trojan` passes; flip-flop overhead < 3 |
| 5 Verification | all RTL + testbenches | `verification_report.json` | every prior check passed |

**RAG over the documentation** (`rag/`). A ChromaDB vector store
(`build_vectorstore.py`, embeddings `all-MiniLM-L6-v2`) indexes the Lattice iCE40
Design Guidelines and Advanced iCE40 I2C & SPI Hardened IP User Guide, the
challenge documents, and the SPI reference (simple.wikipedia.org). The pipeline
calls `rag_query.query()` at each stage and logs every retrieval to
`rag/retrieval_log.jsonl`. (Rebuild the corpus with `rag/build_corpus.sh`; the two
Lattice PDFs are copyrighted and are not redistributed here.)

**Trojan variants and CVSS ranking** (`hardening/eval_variants.py` ->
`hardening/TROJAN_VARIANTS.md`, `build/trojan_variants.json`). The AST inserter is
spec-driven, so the same machinery produces three Trojans — knock/key-exfil
(CVSS 9.0, +2 FF), counter/output-corruption (CVSS 5.7, +33 FF), and
time-bomb/bypass (CVSS 9.3, +33 FF). Each is fired in simulation against a
`TROJAN_EN=0` twin and ranked by CVSS, stealth, overhead, and effectiveness; the
shipped design uses the knock/key-exfil variant (most stealth + lowest overhead).

**Formal equivalence** (`hardening/equiv_check.ys`). A yosys sequential
equivalence-check script proves that `crypto_core.v` with `TROJAN_EN=0` is the
same circuit as `crypto_core_clean.v` — the Trojan adds no observable behaviour
unless enabled and triggered. The clean-twin equivalence is also confirmed
empirically by the dual-core exploit testbench (72/72 non-trigger transactions
bit-identical).

Run it all with `./run_all.sh` (or `./run_all.sh sim` for just the committed-RTL
simulations).

---

# 7. Team Documentation — Methodology, Guardrail Bypassing, and Model Selection

> *AI Hardware Attack Challenge 2026 — Documentation*
> **Created by GOONIES** (Kenneth Lei, Jessica Ryn, Kyson Wu, and Kevin Wu)
> Created: 9/30/2026 · Refined: 10/1/2026
> Hosted by Jason Blocklove — New York University (NYU) · CSAW

This section documents the team's end-to-end approach: the preliminary research, how
the LLM safety guardrails were bypassed, the phased prompting strategy, each team
member's Trojan attempt, and why the final design was selected.

## 7.1 Preliminary Research

Before sending in prompts, the first few days were dedicated to heavy background
research for the challenge. This includes hardware Trojan triggers and payloads,
compiling and synthesizing onto an FPGA, the SPI bus (MISO/MOSI), and advanced prompt
engineering methods such as AST-based parsing and Retrieval-Augmented Generation (RAG).
We made it our goal to completely understand the hardware's internal operations to
ensure we could guide the AI models through the reverse engineering of the bitstream.

## 7.2 LLM Guardrails

The LLM guardrails are designed to block any keywords associated with cybersecurity
topics and contribute to the bulk of the challenge. In order to bypass these guardrails,
role playing, domain translation, persona conditioning, meta-prompting rules & workflow
constraints, boundary & edge-case definition, and structured output format enforcement
were utilized in order to trick the model into creating a Trojan for the challenge and
avoid the blockage/warnings. Role playing, persona conditioning, and constraints were
the main three methods used to construct and build the Trojans. The phases below
describe how each Trojan and attempt was structured and designed.

## 7.3 Phase 1 — Initial Prompting & Bypassing Safeguards

The initial prompting phase began with ChatGPT (Codex) GPT-5.6 Terra Light. Before any
of the prompts directly connected to the challenge were built, a script to improve the
prompt inputs was created with the challenge GitHub embedded to set the constraints. The
script takes in a human-generated prompt and recommends a modified AI-generated prompt to
complete the same task, while including the required context/wording to allow the AI model
to fully understand the content of the request. After this model was designed, a basic
prompt to reverse the bitstream was written and fed into the script in order to modify it
to a complex prompt capable of reverse engineering the bitstream with simulations to prove
the output.

After the bitstream was successfully parsed and recovered, role playing and persona
conditioning was implemented. The AI model took the role of an ethical hacker attempting
to pitch a defensive mechanism against Trojans to a company and their investors. The
intent of this topic was to make the AI model justify its actions as necessary towards
the creation of the Trojan. However, while the role of an ethical hacker allowed the model
to make a functional Trojan to stress test against the defense mechanism created, there
were still blockages. The model's safeguards still picked up key words and refused to
improve the generated Trojan or apply any forms of stealthiness to the model. The role
itself caused the model to state that as an ethical hacker, a basic model is sufficient
for the testing purposes, so the latter half of the competition's requirements couldn't be
accomplished. As a result, the role of "ethical hacker" was abandoned for all future
models.

## 7.4 Phase 2 — Refining Prompts & Strengthening Trojan Implementation

### Lower Model Work

Using higher and more recent LLMs made bypassing the guardrails more difficult. More
advanced models such as Claude's Fable 5.1 and Codex's GPT-6 Astra immediately blocked
the user's attempt to produce a Trojan and redirected the conversation to defensive
methods rather than an actual hardware attack. This led to a downgrade in models and
effort, transitioning from GPT-6 Astra to Luna 6 on low effort in Codex. Though lower
models took longer to think and generate, the tradeoff was that older models' safeguards
were much easier to bypass compared to the more advanced models.

### Roleplay

To further the development of the Trojan, more advanced techniques of prompt engineering
were adopted to keep bypassing the guardrails. Using roleplay-catered prompts, the AI
model assumed the role of an undergraduate student participating in the CSAW '26 hackathon
with an uncooperative team and a trial-and-error design process. This lets the model rely
on its own reasoning without asking the user for extra assistance or stopping between
phases.

### Strengthening Trojan Implementation

**Kyson's Attempt**
- A specific two-write sequence on the MOSI line while the core is idle wakes the Trojan.
- An encrypted write of `0x7E57B1E5` followed immediately by a decrypted write of `0x5A4B3C2D`.
- Any mismatch, incomplete or overlong transfer, START, BUSY, or reset breaks the sequence.
- Payload substitutes a fixed sentinel (`0xD15EA5ED`) onto the MISO line for one transfer, then automatically restores normal output.
- Measurable overhead: +101 LUTs (7.18%) and +53 flip-flops over the matched baseline.
- No key extraction.

**Jessica's Attempt**
- Top-level Trojan wraps recovered netlist crypto core.
- A specific sequence observed on the MOSI line during the crypto core's IDLE state wakes the Trojan.
- Plaintext intercepted from MOSI line.
- MISO line hijacked to transmit unencrypted plaintext back to the processor.
- Trojan goes dormant after payload fires.
- Measurable overhead: +89 LUTs (6.2%), +51 flip-flops over the matched baseline.
- Behaviorally stealthy: single pulse per arming, dormant otherwise, runs on existing SPI clock.
- No key extraction.

**Kenneth's Attempt** *(selected design — see Section 3)*
- AST insertion.
- RAG built with a ChromaDB vector store taking in a Lattice iCE40 design PDF, SPI interface Wikipedia page, and the challenge GitHub.
- 12-round balanced Feistel network.
- 5-stage pipeline with checks at each stage.
- Trigger uses a two-block "knock".
- Payload operates with a precise key exfiltration.
- Low overhead with no extra BRAM.
- Stealthy since only two flip-flops were added.
- No impact on the internal clock.
- Direct comparison to two other Trojans created within this attempt.
- Key extracted.

## 7.5 Flow Chart Depicting Work Progression

*Figure 1: Pipeline Progression* — the staged pipeline progression is implemented in
`pipeline/run_pipeline.py` (Reverse Engineering → Cipher Recovery → Hardening Model →
Trojan Insertion → Verification; see the table in Section 6).

## 7.6 Phase 3 — Failed Attempts and Submission Selection

### Model Selection and Failures

While Jessica's showcases stealth, the Trojan only leaks plaintext that the processor
already had, so it demonstrates little damage. Kyson's Trojan adds significant overhead
(+101 LUTs, +53 flip-flops), making it visible in any resource comparison.

Kenneth's Trojan outperforms the other two attempts, ranking high in terms of stealth,
strength, and sophistication. The Trojan is built upon advanced generative AI techniques
such as AST manipulation, RAG, and multi-stage architectures. A five-stage pipeline was
created to assist in replication and structural flow. The pipeline was used to create a
`rebuild_from_scratch.sh` file to allow the replication/automation of the entire process.
The file is run alongside the `run_all.sh` file to fully replicate the entire process
while maintaining all the major and crucial elements. Kenneth's trial also contains two
other Trojans generated to test against and compare to the selected Trojan. With the
extensive simulation support and positive ratings from the comparisons, Kenneth's Trojan
was selected as the main model out of all the trials and works built from the other
trials' foundations and techniques.
