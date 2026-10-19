#!/usr/bin/env python3
# ============================================================================
# recover_key.py - recover the 32-bit hard-wired device key from the netlist.
#
# The accelerator never receives a key over SPI; the key is baked into the
# bitstream.  White-box analysis of the recovered netlist shows the key is
# injected at the very first round of every byte lane:
#
#       sbox_addr0[lane] = ROR1( plaintext_byte[lane]  XOR  key_byte[lane] )
#
# i.e. the first S-box address is a 1-bit right-rotate of (plaintext XOR key).
# Therefore, driving plaintext = 0 makes the first S-box address equal to
#       addr0 = ROR1(key_byte)   ==>   key_byte = ROL1(addr0).
#
# We read each of the four BRAM read-address registers (ram40_6_5 / _7 / _9 /
# _11, one per byte lane) directly out of the gate-level netlist by simulation,
# with the operand register held at 0, then rotate to recover each key byte.
# The four bytes concatenate to the full 32-bit device key.
#
# Result:  KEY = 0x8E043CB1   (bytes lane0..3 = B1 3C 04 8E)
#
# The structure ROR1(P ^ key) is then re-verified against the live netlist on
# several non-zero inputs per lane, so the recovery is proven, not asserted.
#
# Requires the OSS CAD Suite (iverilog/vvp) and recovery/reverse.sh already run
# (produces recovered_por.v + cells_sim.v).
# ============================================================================
import subprocess, re, os, sys

HERE  = os.path.dirname(os.path.abspath(__file__))
NET   = os.path.join(HERE, "recovered_por.v")
CELLS = os.path.join(HERE, "cells_sim.v")

# Per-lane BRAM read-address nets (index bit 0..7) and read-data nets (bit 0..7).
# Taken from the RADDR/RDATA port maps of the four SB_RAM40_4K primitives in
# recovered_netlist.v.  The data nets are listed so iverilog retains the BRAM
# read path (unreferenced nets are optimised away and read as 0).
LANE_ADDR = {
    0: ['n620','n766','n616','n600','n612','n767','n768','n626'],
    1: ['n772','n771','n774','n769','n777','n780','n783','n784'],
    2: ['n716','n714','n713','n792','n791','n715','n712','n623'],
    3: ['n803','n799','n804','n805','n726','n801','n806','n797'],
}
LANE_DATA = {
    0: ['n696','n695','n694','n693','n692','n691','n690','n689'],
    1: ['n613','n704','n703','n702','n701','n700','n699','n605'],
    2: ['n711','n710','n709','n708','n707','n706','n705','n622'],
    3: ['n722','n479','n724','n723','n721','n720','n718','n719'],
}

def _ex(nets):
    return "|".join("(dut.%s<<%d)" % (n, i) for i, n in enumerate(nets))

def _build():
    decl = "".join("reg[7:0] A%d,D%d;" % (L, L) for L in range(4))
    samp = "".join("A%d=%s;D%d=%s;" % (L, _ex(LANE_ADDR[L]), L, _ex(LANE_DATA[L]))
                   for L in range(4))
    disp = "".join('$display("L%d a=%%02x",A%d);' % (L, L) for L in range(4))
    tb = r'''
`timescale 1ns/1ps
module tb;
  reg sck=0,rst_n=1,mosi=0,cs_n=1,start=0,enc_dec=0; wire busy,led,miso;
  reg [31:0] DIN; __DECL__
  chip dut(.io_9_31_1(sck),.io_13_31_1(rst_n),.io_16_31_0(mosi),.io_19_31_1(enc_dec),
           .io_17_31_0(cs_n),.io_18_31_1(start),.io_8_31_1(busy),.io_19_31_0(led),.io_16_31_1(miso));
  task tick; begin #2;sck=1;#5;sck=0;#3; end endtask
  task shift(input [31:0] wd); integer i; begin
    for(i=31;i>=0;i=i-1) begin mosi=wd[i]; tick; end end endtask
  initial begin
    if(!$value$plusargs("din=%h",DIN)) DIN=0;
    sck=0;rst_n=1;cs_n=1;start=0;enc_dec=0;mosi=0;#10;
    rst_n=0; tick; rst_n=1; tick; enc_dec=0;
    cs_n=0; shift(DIN); cs_n=1; #3;      // operand loaded; sample first S-box address
    __SAMP__ __DISP__
    $finish;
  end
endmodule
'''.replace("__DECL__", decl).replace("__SAMP__", samp).replace("__DISP__", disp)
    open(os.path.join(HERE, "_recover.v"), "w").write(tb)
    r = subprocess.run(["iverilog", "-g2012", "-DNO_ICE40_DEFAULT_ASSIGNMENTS",
                        "-o", os.path.join(HERE, "_recover.vvp"),
                        os.path.join(HERE, "_recover.v"), NET, CELLS],
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit("iverilog failed:\n" + r.stderr)

def _run(din):
    s = subprocess.run(["vvp", os.path.join(HERE, "_recover.vvp"), "+din=%08x" % din],
                       capture_output=True, text=True, timeout=120).stdout
    return {int(L): int(v, 16) for L, v in re.findall(r"L(\d) a=(\w+)", s)}

def ror1(x): return ((x >> 1) | (x << 7)) & 0xFF
def rol1(x): return ((x << 1) | (x >> 7)) & 0xFF

def main():
    if not (os.path.exists(NET) and os.path.exists(CELLS)):
        sys.exit("run recovery/reverse.sh first (need recovered_por.v + cells_sim.v)")
    _build()

    c0 = _run(0)                                  # addr0 with plaintext = 0  ==  ROR1(key_byte)
    key = 0
    print("Per-lane recovery (operand=0 -> addr0 = ROR1(key_byte)):")
    for L in range(4):
        kb = rol1(c0[L])
        key |= kb << (8 * L)
        print("  lane%d: addr0=0x%02x  key_byte = ROL1(addr0) = 0x%02x" % (L, c0[L], kb))
    print("\nRECOVERED DEVICE KEY = 0x%08X" % key)

    # Re-verify the ROR1(P ^ key) structure on non-zero inputs per lane.
    ok = True
    for L in range(4):
        kb = rol1(c0[L])
        for b in (0x01, 0x55, 0x8E, 0xB1, 0xFF):
            got = _run(b << (8 * L))[L]
            exp = ror1(b ^ kb)
            if got != exp:
                ok = False
                print("  [FAIL] lane%d P=0x%02x: addr0=0x%02x expected ROR1(P^key)=0x%02x"
                      % (L, b, got, exp))
    print("Structure addr0 == ROR1(plaintext ^ key) verified on all probes: %s" % ok)

    match = (key == 0x8E043CB1)
    print("\nKEY RECOVERY: %s (expected 0x8E043CB1)" % ("PASS" if (ok and match) else "FAIL"))
    return 0 if (ok and match) else 1

if __name__ == "__main__":
    sys.exit(main())
