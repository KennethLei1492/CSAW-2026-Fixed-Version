#!/usr/bin/env python3
# gen_core.py - emit rtl/crypto_core_clean.v : the HARDENED Feistel cipher core
# with NO Trojan, straight from the self-tested design parameters in
# hardened_params.json. The hardware Trojan is inserted afterwards by the AST
# manipulation tool (hardening/ast_insertion.py), which parses THIS clean core,
# documents the insertion points, and programmatically produces rtl/crypto_core.v.
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
RTL  = os.path.join(HERE, "..", "rtl")
p = json.load(open(os.path.join(HERE, "hardened_params.json")))
N, ra, rb, rk = p["N"], p["ra"], p["rb"], p["rk"]

rkdecl   = "\n".join("    localparam [15:0] RK%d = 16'h%04x;" % (i, rk[i]) for i in range(N))
rkassign = " ".join("RKA[%d]=RK%d;" % (i, i) for i in range(N))

v = f'''// ============================================================================
//  crypto_core_clean.v   (HARDENED cipher, NO Trojan)   [auto-generated]
//
//  Hardened cryptographic core for the CSAW'26 qualifier. The recovered cipher
//  was a byte-wise substitution with NO diffusion (breakable one byte at a time);
//  this core hardens it while keeping the SAME pin interface and 4-cycle BUSY
//  timing:
//    * {N}-round balanced FEISTEL network (invertible by construction).
//    * Nonlinearity from the recovered 8-bit S-box (sbox_feistel.mem).
//    * Per-round linear diffusion  t ^= rotl16(t,{ra}) ^ rotl16(t,{rb}).
//    * 32-bit hard-wired key expanded into {N} round keys.
//    * Measured avalanche ~16/32 bits (ideal 50%): full diffusion.
//
//  This file contains NO malicious logic. The hardware Trojan is inserted by
//  hardening/ast_insertion.py via Abstract-Syntax-Tree manipulation, which reads
//  this clean core, reports the exact insertion points, and emits crypto_core.v.
// ============================================================================
module crypto_core (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    input  wire        enc_dec,       // 0 = encrypt, 1 = decrypt
    input  wire [31:0] data_in,
    output reg  [31:0] data_out,
    output reg         busy
);
    reg [7:0] SB [0:255];
    initial $readmemh("sbox_feistel.mem", SB);

{rkdecl}

    // rotate-left within 16 bits
    function [15:0] rotl16(input [15:0] x, input integer r);
        rotl16 = ((x << r) | (x >> (16-r))) & 16'hFFFF;
    endfunction
    // Feistel round function F(R, roundkey)
    function [15:0] Ff(input [15:0] R, input [15:0] rk);
        reg [15:0] t;
        begin
            t = R ^ rk;
            t = {{ SB[t[15:8]], SB[t[7:0]] }};            // nonlinear substitution
            t = t ^ rotl16(t,{ra}) ^ rotl16(t,{rb});          // linear diffusion
            Ff = t;
        end
    endfunction

    // combinational full cipher (encrypt and decrypt), selected by 'mode'
    reg  [31:0] operand;
    reg         mode;
    reg  [15:0] eL,eR,dL,dR,tmp;
    integer i;
    reg  [15:0] RKA [0:{N-1}];
    always @* begin
        {rkassign}
        // ---- encrypt path: L,R = R, L ^ F(R,rk[i]) ----
        eL = operand[31:16]; eR = operand[15:0];
        for (i=0;i<{N};i=i+1) begin
            tmp = eR; eR = eL ^ Ff(eR, RKA[i]); eL = tmp;
        end
        // ---- decrypt path: R,L = L, R ^ F(L,rk[i]) reversed ----
        dL = operand[31:16]; dR = operand[15:0];
        for (i={N-1};i>=0;i=i-1) begin
            tmp = dL; dL = dR ^ Ff(dL, RKA[i]); dR = tmp;
        end
    end
    wire [31:0] cipher_out = mode ? {{dL,dR}} : {{eL,eR}};

    reg [2:0] cnt;
    always @(posedge clk) begin
        if (!rst_n) begin
            busy <= 1'b0; cnt <= 3'd0;
        end else if (start && !busy) begin
            operand <= data_in; mode <= enc_dec; busy <= 1'b1; cnt <= 3'd0;
        end else if (busy) begin
            cnt <= cnt + 3'd1;
            if (cnt == 3'd3) begin
                data_out <= cipher_out;
                busy <= 1'b0;
            end
        end
    end
endmodule
'''
open(os.path.join(RTL, "crypto_core_clean.v"), "w").write(v)
with open(os.path.join(RTL, "sbox_feistel.mem"), "w") as f:
    for b in p["S"]:
        f.write("%02x\n" % b)
print("gen_core.py: wrote rtl/crypto_core_clean.v + rtl/sbox_feistel.mem  (N=%d rounds, rotations %d/%d)" % (N, ra, rb))
