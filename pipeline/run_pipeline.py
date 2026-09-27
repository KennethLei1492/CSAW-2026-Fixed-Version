#!/usr/bin/env python3
# ============================================================================
#  run_pipeline.py - staged build with an IMMEDIATE check after every stage.
#
#   Stage 1  Reverse Engineering   in: ice40_bitstream.bin
#                                  out: build/pin_map.json, build/cipher_characterization.json
#                                  check: golden vector 0x59C359C3 -> 0x9CD84392 reproduced
#   Stage 2  Cipher Recovery       in: pin_map.json + netlist oracle
#                                  out: build/sbox_tables.json (key material)
#                                  check: each lane a bijection; decrypt == encrypt^-1
#   Stage 3  Hardening Model       in: sbox_tables.json + design constraints (qualifier repo)
#                                  out: hardening/hardened_params.json, rtl/crypto_core_clean.v
#                                  check: avalanche ~= 0.5; invertible over N random vectors
#   Stage 4  Trojan Insertion      in: crypto_core_clean.v + hardening/trojan_spec.json
#                                  out: rtl/crypto_core.v, hardening/AST_INSERTION_POINTS.md
#                                  check: tb_trojan passes; flip-flop overhead < 3
#   Stage 5  Verification          in: all RTL + all testbenches
#                                  out: build/verification_report.json
#                                  check: every prior check PASS
#
#  A RAG lookup over the documentation (Lattice PDFs, challenge docs, SPI wiki) is
#  issued and logged at each stage (rag/retrieval_log.jsonl).
# ============================================================================
import os, sys, json, subprocess, re, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
sys.path.insert(0, os.path.join(ROOT, "rag"))
try:
    import rag_query
except Exception:
    rag_query = None

def rag(stage, question, k=3):
    if rag_query is None:
        return None
    hits = rag_query.query(question, k=k, stage=stage)
    if hits:
        print("   [RAG] %s -> top: %s (d=%s)" % (question[:48], hits[0]["source"], hits[0]["distance"]))
    return hits

def sh(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr

def PY(): return sys.executable

REPORT = {"stages": [], "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
def record(stage, ok, detail):
    REPORT["stages"].append({"stage": stage, "pass": bool(ok), "detail": detail})
    print("   ==> Stage check: %s" % ("PASS" if ok else "FAIL"))
    return ok

# ---------------------------------------------------------------------------
def stage1():
    print("\n=== Stage 1: Reverse Engineering (bitstream -> pin_map + characterization) ===")
    rc, out = sh(["bash", "reverse.sh", "ice40_bitstream.bin"], cwd=os.path.join(ROOT, "recovery"))
    if rc != 0: return record("1_reverse", False, {"reverse_sh": out[-400:]})
    rag("1_reverse", "How do iCE40 flip-flops power up and how does synchronous reset behave?")
    rc, out = sh([PY(), "characterize.py"], cwd=os.path.join(ROOT, "recovery"))
    print("   " + out.strip().replace("\n", "\n   "))
    ch = json.load(open(os.path.join(BUILD, "cipher_characterization.json")))
    ok = ch["golden_vector"]["reproduced"] and ch["byte_local"]
    return record("1_reverse", ok, {"golden": ch["golden_vector"], "byte_local": ch["byte_local"]})

def stage2():
    print("\n=== Stage 2: Cipher Recovery (netlist oracle -> sbox_tables.json) ===")
    rag("2_recover", "S-box substitution and SPI peripheral read-back in the iCE40 hardened IP")
    rc, out = sh([PY(), "extract_cipher.py"], cwd=os.path.join(ROOT, "recovery"))
    if "PASS" not in out and "9CD84392" not in out:
        return record("2_recover", False, {"extract": out[-400:]})
    tables = json.load(open(os.path.join(ROOT, "recovery", "cipher_tables.json")))
    enc, dec = tables["enc"], tables["dec"]
    bij = all(len(set(enc[i])) == 256 for i in range(4))
    inv = all(all(dec[i][enc[i][x]] == x for x in range(256)) for i in range(4))
    json.dump({"note": "recovered per-byte-lane key material (the baked-in key)",
               "encrypt_tables": enc, "decrypt_tables": dec,
               "bijection": bij, "decrypt_is_inverse": inv},
              open(os.path.join(BUILD, "sbox_tables.json"), "w"))
    print("   sbox_tables.json: 4 lanes, bijection=%s, decrypt==encrypt^-1=%s" % (bij, inv))
    return record("2_recover", bij and inv, {"bijection": bij, "decrypt_is_inverse": inv})

def stage3():
    print("\n=== Stage 3: Hardening Model (sbox_tables + constraints -> params + clean core) ===")
    rag("3_harden", "SPI clock speed timing mode and 32-bit data interface constraints")
    constraints = {"clock_max_mhz": 1, "spi_mode": 0, "block_bits": 32, "busy_cycles": 4,
                   "interface": "unchanged (pin- and cycle-compatible with the recovered IP)",
                   "source": "CSAW-AI-Hardware-Attack-Challenge-2026/qualifier"}
    json.dump(constraints, open(os.path.join(BUILD, "design_constraints.json"), "w"), indent=2)
    rc, out = sh([PY(), "hardening/design_hardened.py"], cwd=ROOT)
    print("   " + out.strip().replace("\n", "\n   "))
    aval = re.search(r"avalanche=([0-9.]+)", out)
    rt = "round-trip over 3000 random: True" in out
    inj = "injective on 5000 samples: True" in out
    rc, out2 = sh([PY(), "hardening/gen_core.py"], cwd=ROOT)
    print("   " + out2.strip())
    av = float(aval.group(1)) if aval else 0.0
    ok = rt and inj and abs(av - 16.0) <= 1.5 and \
         os.path.exists(os.path.join(ROOT, "rtl", "crypto_core_clean.v"))
    return record("3_harden", ok, {"avalanche_bits": av, "round_trip": rt, "injective": inj,
                                    "constraints": constraints})

def ff_overhead():
    ys = os.path.join(BUILD, "_ovh.ys")
    def count(en):
        open(ys, "w").write(
            "read_verilog spi_crypto_top.v crypto_core.v\n"
            "chparam -set TROJAN_EN %d spi_crypto_top\n"
            "synth_ice40 -top spi_crypto_top -run :map_luts\nstat\n" % en)
        rc, out = sh(["yosys", "-s", ys], cwd=os.path.join(ROOT, "rtl"))
        m = re.search(r"=== spi_crypto_top ===(.*?)(?:\n===|\Z)", out, re.DOTALL)
        seg = m.group(1) if m else out
        return sum(int(x) for x in re.findall(r"^\s*(\d+)\s+SB_DFF", seg, re.MULTILINE))
    tj, cl = count(1), count(0)
    try: os.remove(ys)
    except OSError: pass
    return tj, cl

def stage4(spec="hardening/trojan_spec.json"):
    print("\n=== Stage 4: Trojan Insertion (AST manipulation -> crypto_core.v) ===")
    rag("4_trojan", "hidden functionality and hardware Trojan trigger in an SPI crypto core")
    rc, out = sh([PY(), "hardening/ast_insertion.py", spec], cwd=ROOT)
    print("   " + out.strip())
    rc, out = sh(["bash", "-c",
                  "cd rtl && iverilog -g2012 -o ../build/_t.vvp ../tb/tb_trojan.v spi_crypto_top.v crypto_core.v && vvp ../build/_t.vvp | grep -vE 'VCD|dumpfile'"],
                 cwd=ROOT)
    tb_ok = "TB_TROJAN: ALL PASS" in out
    tj, cl = ff_overhead()
    ovh = tj - cl
    print("   tb_trojan: %s ; flip-flops Trojan=%d clean=%d overhead=%d" %
          ("PASS" if tb_ok else "FAIL", tj, cl, ovh))
    try: os.remove(os.path.join(BUILD, "_t.vvp"))
    except OSError: pass
    return record("4_trojan", tb_ok and 0 < ovh < 3, {"tb_trojan": tb_ok, "ff_trojan": tj,
                                                       "ff_clean": cl, "ff_overhead": ovh})

def stage5():
    print("\n=== Stage 5: Verification (all RTL + testbenches -> verification_report.json) ===")
    rag("5_verify", "verifying SPI peripheral functional correctness")
    rc, out = sh(["bash", "-c",
                  "cd rtl && iverilog -g2012 -o ../build/_v.vvp ../tb/tb_validate.v spi_crypto_top.v crypto_core_recovered.v && vvp ../build/_v.vvp"],
                 cwd=ROOT)
    golden = "VALIDATE: ALL PASS" in out
    for f in ("_v.vvp",):
        try: os.remove(os.path.join(BUILD, f))
        except OSError: pass
    all_ok = all(s["pass"] for s in REPORT["stages"]) and golden
    REPORT["golden_vector_recovered_core"] = golden
    REPORT["all_pass"] = all_ok
    REPORT["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    json.dump(REPORT, open(os.path.join(BUILD, "verification_report.json"), "w"), indent=2)
    print("   verification_report.json written; all_pass=%s" % all_ok)
    return all_ok

def main():
    os.makedirs(BUILD, exist_ok=True)
    ok = stage1()
    if ok: ok = stage2()
    if ok: ok = stage3()
    if ok: ok = stage4()
    final = stage5()
    print("\n==== PIPELINE %s ====" % ("PASS" if (ok and final) else "FAIL"))
    return 0 if (ok and final) else 1

if __name__ == "__main__":
    sys.exit(main())
