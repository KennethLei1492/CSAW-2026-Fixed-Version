#!/usr/bin/env python3
# make_figures.py - produce the report figures (pure Python, no numpy):
#   avalanche.svg           : hardened-cipher Strict Avalanche Criterion matrix
#   avalanche_recovered.svg : recovered weak cipher (no cross-byte diffusion)
#   waveform.svg            : exploit timing diagram (knock -> key on MISO)
#   avalanche_stats.json    : summary stats used by the report
import json, os, random
HERE = os.path.dirname(os.path.abspath(__file__))
P  = json.load(open(os.path.join(HERE, "hardened_params.json")))
CT = json.load(open(os.path.join(HERE, "..", "recovery", "cipher_tables.json")))
S, N, ra, rb, rk = P["S"], P["N"], P["ra"], P["rb"], P["rk"]
M = 0xFFFF

def rotl16(x, r): x &= M; return ((x << r) | (x >> (16-r))) & M
def F(R, k):
    t = (R ^ k) & M
    t = (S[t & 0xFF] | (S[(t >> 8) & 0xFF] << 8)) & M
    return (t ^ rotl16(t, ra) ^ rotl16(t, rb)) & M
def enc_hard(x):
    L = (x >> 16) & M; R = x & M
    for r in range(N): L, R = R, (L ^ F(R, rk[r])) & M
    return ((L << 16) | R) & 0xFFFFFFFF
enc = CT["enc"]
def enc_recovered(x):
    o = 0
    for i in range(4): o |= enc[i][(x >> (8*i)) & 0xff] << (8*i)
    return o

def avalanche_matrix(fn, trials=4000, seed=7):
    random.seed(seed)
    cnt = [[0]*32 for _ in range(32)]
    for _ in range(trials):
        x = random.getrandbits(32); y = fn(x)
        for i in range(32):
            d = fn(x ^ (1 << i)) ^ y
            for j in range(32):
                if (d >> j) & 1: cnt[i][j] += 1
    return [[cnt[i][j]/trials for j in range(32)] for i in range(32)]

def cell_color(v):
    dev = abs(v - 0.5) / 0.5
    r = int(46 + (200-46)*dev); g = int(160 + (50-160)*dev); b = int(67 + (40-67)*dev)
    return "#%02x%02x%02x" % (r, g, b)

def heatmap_svg(Pm, title, subtitle, footer):
    cell = 13; pad = 44; W = pad+32*cell+20; H = pad+32*cell+34
    s = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="Segoe UI,Arial" font-size="9">' % (W, H)]
    s.append('<text x="%d" y="16" font-size="13" font-weight="700" fill="#111">%s</text>' % (pad, title))
    s.append('<text x="%d" y="30" fill="#555">%s</text>' % (pad, subtitle))
    for i in range(32):
        for j in range(32):
            s.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s"/>' %
                     (pad+j*cell, pad+i*cell, cell-1, cell-1, cell_color(Pm[i][j])))
    s.append('<text x="%d" y="%d" fill="#333">%s</text>' % (pad, pad+32*cell+18, footer))
    s.append('</svg>')
    return "\n".join(s)

def mean(Pm):
    vals=[Pm[i][j] for i in range(32) for j in range(32)]; return sum(vals)/len(vals)
def mn(Pm): return min(Pm[i][j] for i in range(32) for j in range(32))
def mx(Pm): return max(Pm[i][j] for i in range(32) for j in range(32))

# hardened
Ph = avalanche_matrix(enc_hard)
open(os.path.join(HERE, "avalanche.svg"), "w", encoding="utf-8").write(heatmap_svg(
    Ph, "Strict Avalanche: P(output bit j flips given input bit i flipped)",
    "4000 random plaintexts per bit; ideal = 0.50 everywhere (full diffusion)",
    "rows = input bit i, cols = output bit j. Uniform green = ideal diffusion."))
json.dump({"mean": round(mean(Ph),4), "min": round(mn(Ph),3), "max": round(mx(Ph),3)},
          open(os.path.join(HERE, "avalanche_stats.json"), "w"))
# recovered (weak)
Pr = avalanche_matrix(enc_recovered)
open(os.path.join(HERE, "avalanche_recovered.svg"), "w", encoding="utf-8").write(heatmap_svg(
    Pr, "RECOVERED cipher avalanche (weak: no diffusion)",
    "Only the 8x8 diagonal blocks react; input bit i affects one byte only.",
    "Red = p far from 0.5 (no dependence). A byte-by-byte attack recovers the key."))

# ---- exploit timing waveform (values verified in simulation) ----
def waveform_svg():
    W=980; H=340; left=120; top=40; rowh=38; usableW=W-left-20
    sig=["SCK","NORM_CS_N","START","BUSY","MOSI","MISO"]
    rows={s:top+i*rowh for i,s in enumerate(sig)}
    phases=[("Txn 1: ENCRYPT 0xDEADC0DE  (KNOCK_1 - arms)",0.0,0.34),
            ("Txn 2: ENCRYPT 0xFEEDFACE  (KNOCK_2 - FIRES)",0.34,0.68),
            ("Txn 3: READ-OUT (32 SCK)  -> MISO",0.68,1.0)]
    def X(f): return left+f*usableW
    s=['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="Segoe UI,Arial" font-size="11">'%(W,H)]
    s.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>'%(W,H))
    s.append('<text x="8" y="20" font-size="14" font-weight="700" fill="#111">Exploit timing (from tb_trojan simulation): two-block knock leaks the key on MISO</text>')
    cols=["#eef4ff","#fff0f0","#eefaf0"]
    for k,(lbl,a,b) in enumerate(phases):
        s.append('<rect x="%.0f" y="%d" width="%.0f" height="%d" fill="%s" opacity="0.7"/>'%(X(a),top-6,X(b)-X(a),len(sig)*rowh,cols[k]))
        s.append('<text x="%.0f" y="%d" font-size="10" font-weight="700" fill="#333">%s</text>'%(X(a)+6,top-10,lbl))
    for sg in sig:
        y=rows[sg]
        s.append('<text x="8" y="%d" font-weight="600" fill="#222">%s</text>'%(y+4,sg))
        s.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#ddd"/>'%(left,y+8,W-20,y+8))
    def wave(sg, segs, color):
        y0=rows[sg]+8; y1=rows[sg]-8; pts=[]
        for (a,b,l) in segs:
            yy=y1 if l else y0; pts.append((X(a),yy)); pts.append((X(b),yy))
        d="M %.1f %.1f"%(pts[0]); prev=pts[0][1]
        for (x,y) in pts[1:]:
            if y!=prev: d+=" L %.1f %.1f"%(x,prev)
            d+=" L %.1f %.1f"%(x,y); prev=y
        s.append('<path d="%s" fill="none" stroke="%s" stroke-width="1.6"/>'%(d,color))
    def clockburst(a,b,ncyc=16,color="#0b5"):
        y0=rows["SCK"]+8; y1=rows["SCK"]-8; step=(b-a)/(ncyc*2); d="M %.1f %.1f"%(X(a),y0)
        for i in range(ncyc):
            x1=a+(2*i+1)*step; x2=a+(2*i+2)*step
            d+=" L %.1f %.1f L %.1f %.1f L %.1f %.1f L %.1f %.1f"%(X(x1),y0,X(x1),y1,X(x2),y1,X(x2),y0)
        s.append('<path d="%s" fill="none" stroke="%s" stroke-width="1.1"/>'%(d,color))
    clockburst(0.02,0.30); clockburst(0.36,0.64); clockburst(0.70,0.98)
    wave("NORM_CS_N",[(0.0,0.02,1),(0.02,0.16,0),(0.16,0.30,1),(0.30,0.36,1),(0.36,0.50,0),(0.50,0.70,1),(0.70,0.98,0),(0.98,1.0,1)],"#a60")
    wave("START",[(0.0,0.17,0),(0.17,0.19,1),(0.19,0.51,0),(0.51,0.53,1),(0.53,1.0,0)],"#06a")
    wave("BUSY",[(0.0,0.19,0),(0.19,0.28,1),(0.28,0.53,0),(0.53,0.62,1),(0.62,1.0,0)],"#c40")
    wave("MOSI",[(0.0,0.02,0),(0.02,0.16,1),(0.16,0.36,0),(0.36,0.50,1),(0.50,1.0,0)],"#555")
    s.append('<text x="%.0f" y="%d" font-size="9" fill="#333">DEADC0DE</text>'%(X(0.05),rows["MOSI"]-11))
    s.append('<text x="%.0f" y="%d" font-size="9" fill="#333">FEEDFACE</text>'%(X(0.39),rows["MOSI"]-11))
    wave("MISO",[(0.0,0.70,0),(0.70,0.98,1),(0.98,1.0,0)],"#b00")
    s.append('<text x="%.0f" y="%d" font-size="10" font-weight="700" fill="#b00">0xC0DECAFE  (SECRET KEY)</text>'%(X(0.71),rows["MISO"]-11))
    s.append('<text x="8" y="%d" fill="#666" font-size="9">Values are the actual simulated results: KNOCK_1/2 encrypt normally; the read-out after the second knock returns the key, not a ciphertext.</text>'%(H-8))
    s.append('</svg>')
    return "\n".join(s)
open(os.path.join(HERE, "waveform.svg"), "w", encoding="utf-8").write(waveform_svg())
print("make_figures.py: hardened avalanche mean=%.4f min=%.3f max=%.3f ; wrote 3 SVGs" % (mean(Ph), mn(Ph), mx(Ph)))
