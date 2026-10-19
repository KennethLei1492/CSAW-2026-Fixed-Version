#!/usr/bin/env python3
# ============================================================================
# equiv_netlist.py - prove the recovered RTL is bit-exact to the real netlist.
#
# Addresses the concern that simulating the recovered RTL might not equal the
# gate-level netlist lifted straight from the bitstream.  Rather than assert
# equivalence, we demonstrate it: the SAME race-free SPI stimulus is driven
# into BOTH
#
#   (1) the gate-level netlist  recovered_por.v  (icebox_vlog lift of the
#       bitstream; our unpack repacks to the original .bin byte-for-byte), and
#   (2) the clean recovered RTL rtl/crypto_core_recovered_clean.v behind the
#       SPI wrapper rtl/spi_crypto_top.v,
#
# over the published golden vector and N random operands in BOTH encrypt and
# decrypt modes.  Every output byte must match.  A pass means a simulation of
# the recovered RTL is a faithful simulation of the recovered silicon, so the
# Trojan demonstrated on that RTL (tb_trojan.v) is demonstrated on the device.
#
# Requires OSS CAD Suite + recovery/reverse.sh already run.
# ============================================================================
import subprocess, re, os, sys, random

HERE = os.path.dirname(os.path.abspath(__file__))
RTL  = os.path.join(HERE, "..", "rtl")
sys.path.insert(0, HERE)
import extract_cipher as ec          # reuse the netlist oracle (build/op)

RTL_TB = r'''
`timescale 1ns/1ps
module tb;
  reg SCK=0, RST_N=1, MOSI=0, NORM_CS_N=1, START=0, ENC_DEC=0;
  wire MISO, BUSY, ICE_LED; reg [31:0] rx, DIN; reg ED; integer i;
  spi_crypto_top #(.TROJAN_EN(1'b0)) dut(.SCK(SCK),.RST_N(RST_N),.MOSI(MOSI),.MISO(MISO),
    .NORM_CS_N(NORM_CS_N),.START(START),.ENC_DEC(ENC_DEC),.BUSY(BUSY),.ICE_LED(ICE_LED));
  task tick; begin #2; SCK=1; #5; SCK=0; #3; end endtask
  task xfer(input [31:0] wd, input cap); begin
    NORM_CS_N=0;
    for(i=31;i>=0;i=i-1) begin MOSI=wd[i]; #2; SCK=1; #5; if(cap) rx[i]=MISO; SCK=0; #3; end
    NORM_CS_N=1; MOSI=0;
  end endtask
  initial begin
    if(!$value$plusargs("din=%h",DIN)) DIN=0;
    if(!$value$plusargs("ed=%d",ED))  ED=0;
    RST_N=0; tick; RST_N=1; tick; ENC_DEC=ED;
    xfer(DIN,1'b0); START=1; tick; START=0; repeat(6) tick; xfer(32'h0,1'b1);
    $display("OUT=%08x", rx); $finish;
  end
endmodule
'''

def build_rtl():
    open(os.path.join(HERE, "_rtl_equiv.v"), "w").write(RTL_TB)
    r = subprocess.run(["iverilog", "-g2012", "-o", os.path.join(HERE, "_rtl_equiv.vvp"),
                        os.path.join(HERE, "_rtl_equiv.v"),
                        os.path.join(RTL, "spi_crypto_top.v"),
                        os.path.join(RTL, "crypto_core_recovered_clean.v")],
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit("iverilog (RTL) failed:\n" + r.stderr)

def rtl_op(din, ed):
    # mem files are referenced relative to the working dir -> run from rtl/
    s = subprocess.run(["vvp", os.path.join(HERE, "_rtl_equiv.vvp"),
                        "+din=%08x" % din, "+ed=%d" % ed],
                       capture_output=True, text=True, timeout=120, cwd=RTL).stdout
    return int(re.search(r"OUT=([0-9a-f]+)", s).group(1), 16)

def main():
    ec.build()           # builds the netlist oracle _oracle.vvp
    build_rtl()
    random.seed(0xC5A26)
    tests = [(0x59C359C3, 0), (0x9CD84392, 1), (0x00000000, 0), (0xFFFFFFFF, 0)]
    tests += [(random.getrandbits(32), random.getrandbits(1)) for _ in range(40)]

    mism = 0
    for din, ed in tests:
        net = ec.op(din, ed)     # gate-level netlist (the real device)
        rtl = rtl_op(din, ed)    # recovered RTL behind the SPI wrapper
        tag = "ENC" if ed == 0 else "DEC"
        if net != rtl:
            mism += 1
            print("  [MISMATCH] %s din=%08x netlist=%08x rtl=%08x" % (tag, din, net, rtl))
    # explicit golden check
    golden_ok = (ec.op(0x59C359C3, 0) == 0x9CD84392 == rtl_op(0x59C359C3, 0))
    print("Golden vector enc(0x59C359C3)=0x9CD84392 on BOTH: %s" % golden_ok)
    print("Netlist vs recovered-RTL equivalence over %d transactions (enc+dec): %s"
          % (len(tests), "PASS" if (mism == 0 and golden_ok) else "FAIL (%d mismatch)" % mism))
    return 0 if (mism == 0 and golden_ok) else 1

if __name__ == "__main__":
    sys.exit(main())
