// ============================================================================
//  crypto_core_clean.v   (HARDENED cipher, NO Trojan)   [auto-generated]
//
//  Hardened cryptographic core for the CSAW'26 qualifier. The recovered cipher
//  was a byte-wise substitution with NO diffusion (breakable one byte at a time);
//  this core hardens it while keeping the SAME pin interface and 4-cycle BUSY
//  timing:
//    * 12-round balanced FEISTEL network (invertible by construction).
//    * Nonlinearity from the recovered 8-bit S-box (sbox_feistel.mem).
//    * Per-round linear diffusion  t ^= rotl16(t,3) ^ rotl16(t,11).
//    * 32-bit hard-wired key expanded into 12 round keys.
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

    localparam [15:0] RK0 = 16'hcafe;
    localparam [15:0] RK1 = 16'hb2bf;
    localparam [15:0] RK2 = 16'hecaf;
    localparam [15:0] RK3 = 16'h7b2b;
    localparam [15:0] RK4 = 16'hdeca;
    localparam [15:0] RK5 = 16'h37b2;
    localparam [15:0] RK6 = 16'h5fd8;
    localparam [15:0] RK7 = 16'h57f6;
    localparam [15:0] RK8 = 16'h95fd;
    localparam [15:0] RK9 = 16'h657f;
    localparam [15:0] RK10 = 16'hd95f;
    localparam [15:0] RK11 = 16'hf657;

    // rotate-left within 16 bits
    function [15:0] rotl16(input [15:0] x, input integer r);
        rotl16 = ((x << r) | (x >> (16-r))) & 16'hFFFF;
    endfunction
    // Feistel round function F(R, roundkey)
    function [15:0] Ff(input [15:0] R, input [15:0] rk);
        reg [15:0] t;
        begin
            t = R ^ rk;
            t = { SB[t[15:8]], SB[t[7:0]] };            // nonlinear substitution
            t = t ^ rotl16(t,3) ^ rotl16(t,11);          // linear diffusion
            Ff = t;
        end
    endfunction

    // combinational full cipher (encrypt and decrypt), selected by 'mode'
    reg  [31:0] operand;
    reg         mode;
    reg  [15:0] eL,eR,dL,dR,tmp;
    integer i;
    reg  [15:0] RKA [0:11];
    always @* begin
        RKA[0]=RK0; RKA[1]=RK1; RKA[2]=RK2; RKA[3]=RK3; RKA[4]=RK4; RKA[5]=RK5; RKA[6]=RK6; RKA[7]=RK7; RKA[8]=RK8; RKA[9]=RK9; RKA[10]=RK10; RKA[11]=RK11;
        // ---- encrypt path: L,R = R, L ^ F(R,rk[i]) ----
        eL = operand[31:16]; eR = operand[15:0];
        for (i=0;i<12;i=i+1) begin
            tmp = eR; eR = eL ^ Ff(eR, RKA[i]); eL = tmp;
        end
        // ---- decrypt path: R,L = L, R ^ F(L,rk[i]) reversed ----
        dL = operand[31:16]; dR = operand[15:0];
        for (i=11;i>=0;i=i-1) begin
            tmp = dL; dL = dR ^ Ff(dL, RKA[i]); dR = tmp;
        end
    end
    wire [31:0] cipher_out = mode ? {dL,dR} : {eL,eR};

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
