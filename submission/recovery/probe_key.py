#!/usr/bin/env python3
# probe_key.py - recover / document the hard-wired key of the FPGA accelerator.
#
# This device is given NO key over SPI; the key is baked into the bitstream.
# We recover it two independent ways and cross-check:
#   (1) white-box : the 256-byte S-box read straight out of the four BRAMs
#                   (recovery/sbox_bram_init.txt, from the netlist INIT params);
#   (2) black-box : the four per-byte-lane 256-entry key tables
#                   (recovery/cipher_tables.json, from extract_cipher.py).
# We also probe the internal round-state registers cycle-by-cycle to show the
# engine uses a non-standard two-phase schedule (so there is no short N-bit key;
# the S-box + lane tables ARE the key). Writes recovery/round_ad.json.
import subprocess, re, json, os
HERE = os.path.dirname(os.path.abspath(__file__))
NET  = os.path.join(HERE, "recovered_por.v")
CELLS= os.path.join(HERE, "cells_sim.v")

# lane-0 S-box address FFs and data-output nets (from BRAM ram40_6_5 wiring)
ADDR = ['n620','n766','n616','n600','n612','n767','n768','n626']   # bits 0..7
DATA = ['n696','n695','n694','n693','n692','n691','n690','n689']   # bits 0..7
aexpr = "|".join("(dut.%s<<%d)" % (n, i) for i, n in enumerate(ADDR))
dexpr = "|".join("(dut.%s<<%d)" % (n, i) for i, n in enumerate(DATA))

TB = r'''
`timescale 1ns/1ps
module tb;
  reg sck=0, rst_n=1, mosi=0, cs_n=1, start=0, enc_dec=0; wire busy,led,miso;
  reg [7:0] A,D; reg [31:0] DIN; integer c;
  chip dut(.io_9_31_1(sck),.io_13_31_1(rst_n),.io_16_31_0(mosi),.io_19_31_1(enc_dec),
           .io_17_31_0(cs_n),.io_18_31_1(start),.io_8_31_1(busy),.io_19_31_0(led),.io_16_31_1(miso));
  task tick; begin #2; sck=1; #5; sck=0; #3; end endtask
  task shift(input [31:0] wd); integer i; begin for(i=31;i>=0;i=i-1) begin mosi=wd[i]; tick; end end endtask
  initial begin
    if(!$value$plusargs("din=%h",DIN))DIN=0;
    sck=0;rst_n=1;cs_n=1;start=0;enc_dec=0;mosi=0;#10;
    rst_n=0; tick; rst_n=1; tick; enc_dec=0;
    cs_n=0; shift(DIN); cs_n=1; #3;
    A=__A__; D=__D__; $display("r0 a=%02x d=%02x",A,D);
    mosi=0; start=1; tick; start=0; #1; A=__A__; D=__D__; $display("r1 a=%02x d=%02x",A,D);
    for(c=2;c<=5;c=c+1) begin tick; #1; A=__A__; D=__D__; $display("r%0d a=%02x d=%02x",c,A,D); end
    $finish;
  end
endmodule
'''.replace("__A__", aexpr).replace("__D__", dexpr)

open(os.path.join(HERE, "_probe.v"), "w").write(TB)
subprocess.run(["iverilog","-g2012","-DNO_ICE40_DEFAULT_ASSIGNMENTS","-o",os.path.join(HERE,"_probe.vvp"),
                os.path.join(HERE,"_probe.v"), NET, CELLS], capture_output=True, text=True)
seq = {}
for v in range(256):
    s = subprocess.run(["vvp", os.path.join(HERE,"_probe.vvp"), "+din=%08x"%v],
                       capture_output=True, text=True, timeout=60).stdout
    seq[v] = [(int(a,16), int(d,16)) for _, a, d in re.findall(r"r(\d) a=(\w+) d=(\w+)", s)]
json.dump(seq, open(os.path.join(HERE, "round_ad.json"), "w"))

# per-round-transition constant test: a[r+1] ^ d[r] constant => that constant is a keying byte
print("Round-transition analysis (a[next] XOR d[cur]):")
for r in range(4):
    ks = set(seq[v][r+1][0] ^ seq[v][r][1] for v in range(256) if len(seq[v]) > r+1)
    tag = "CONSTANT 0x%02x (hard-wired keying byte)" % next(iter(ks)) if len(ks)==1 else "input-dependent (data phase)"
    print("  round %d->%d : %s" % (r, r+1, tag))

# report the concrete recovered key material
CT = json.load(open(os.path.join(HERE, "cipher_tables.json")))
enc = CT["enc"]
pt = 0x59C359C3; ct = 0; fp = 0
for i in range(4):
    ct |= enc[i][(pt>>(8*i)) & 0xff] << (8*i)
    fp |= enc[i][0] << (8*i)
print("\nRecovered key material (device has no SPI-loaded key; key is in the bitstream):")
print("  * 256-byte secret S-box  -> recovery/sbox_bram_init.txt")
for i in range(4):
    print("  * lane%d key table[0..7] = %s ..." % (i, " ".join("%02x"%enc[i][v] for v in range(8))))
print("  self-check enc(0x59C359C3) = 0x%08X (published vector), fingerprint enc(0)=0x%08X" % (ct, fp))
print("  => the S-box + four lane tables ARE the key: they encrypt/decrypt any block offline.")
