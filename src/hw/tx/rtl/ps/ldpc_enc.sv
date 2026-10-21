`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// IEEE 802.11n LDPC encoder, n = 1944, rate 2/3 (Z = 81, ldpc_r23_pkg), for the SSCC baseline:
//   in : scrambled information bits, one per beat (CW * 1296 per frame, tlast on the last; only counted)
//   out: 6 coded bits per beat for qam64_map (m_tdata[0] first): CW codewords {u_0 .. u_1295, p_0 .. p_647},
//        then PAD zero bits, tlast on the last beat of the frame
// Encoding (src/model/ldpc_ref.py): lambda_r = sum_c<16 P^s(r,c) u_c, one non-zero block per clock (128 clocks);
// p_0 = sum_r lambda_r (column 16 = P^1 + P^0 + P^1 = I); p_1 = lambda_0 + P^s(0,16) p_0,
// p_(r+1) = lambda_r + P^s(r,16) p_0 + p_r (7 clocks). The input register takes the next codeword while the
// previous one is encoded and sent (1296 input clocks vs ~140 encoding + 324 output beats).
//////////////////////////////////////////////////////////////////////////////////
module ldpc_enc
    import ldpc_r23_pkg::*;
#(
    parameter int CW  = 101,                           // codewords per frame
    parameter int PAD = 264                            // zero bits after the last codeword (multiple of 6)
)(
    input  logic       clk,
    input  logic       rst_n,
    input  logic       s_tdata,
    input  logic       s_tlast,
    input  logic       s_tvalid,
    output logic       s_tready,
    output logic [5:0] m_tdata,
    output logic       m_tlast,
    output logic       m_tvalid,
    input  logic       m_tready
);
    localparam int K = KB * Z, N = NB * Z;

    // lane i <- v[(i + s) mod Z], one stage per bit of s
    function automatic logic [Z-1:0] rotl(input logic [Z-1:0] v, input logic [6:0] s);
        logic [Z-1:0] t, u;
        t = v;
        for (int b = 0; b < 7; b++) begin
            for (int i = 0; i < Z; i++) u[i] = t[(i + (1 << b)) % Z];
            if (s[b]) t = u;
        end
        return t;
    endfunction

    logic          take, oload, obusy;
    // ---------------------------------------------------------------- input register (s_tlast: frame end, implied
    // by the counts)
    logic [K-1:0]  ibuf;
    logic [$clog2(K)-1:0] icnt;
    logic          ifull;
    assign s_tready = ~ifull;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin icnt <= '0; ifull <= 1'b0; end
        else begin
            if (s_tvalid & s_tready) begin
                ibuf[icnt] <= s_tdata;
                if (icnt == K - 1) begin icnt <= '0; ifull <= 1'b1; end
                else icnt <= icnt + 1'b1;
            end
            if (take) ifull <= 1'b0;
        end
    end

    // ---------------------------------------------------------------- encoder
    typedef enum logic [1:0] {E_IDLE, E_LAM, E_PAR, E_DONE} est_t;
    est_t          est;
    logic [K-1:0]  u;
    logic [Z-1:0]  lam [NL];
    logic [Z-1:0]  p   [NL];
    logic [3:0]    ec;                                 // information column
    logic [2:0]    er;                                 // row
    assign take  = (est == E_IDLE) & ifull;
    assign oload = (est == E_DONE) & ~obusy;
    logic [Z-1:0]  p0;
    always_comb begin
        p0 = '0;
        for (int r = 0; r < NL; r++) p0 ^= lam[r];
    end
    logic signed [7:0] sh;
    always_comb sh = shift_of(er, 5'(ec));
    logic [Z-1:0] uc;
    always_comb uc = u[ec * Z +: Z];
    logic signed [7:0] sh16;
    always_comb sh16 = shift_of(er, 5'(KB));
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin est <= E_IDLE; ec <= '0; er <= '0; end
        else case (est)
            E_IDLE: if (take) begin
                u <= ibuf; ec <= '0; er <= '0; est <= E_LAM;
                for (int r = 0; r < NL; r++) lam[r] <= '0;
            end
            E_LAM: begin
                if (sh >= 0) lam[er] <= lam[er] ^ rotl(uc, sh[6:0]);
                er <= er + 1'b1;
                if (er == NL - 1) begin
                    ec <= ec + 1'b1;
                    if (ec == KB - 1) begin est <= E_PAR; er <= '0; end
                end
            end
            E_PAR: begin                               // er = r: p_(r+1) = lambda_r + P^s(r,16) p_0 (+ p_r, r > 0)
                if (er == 0) p[0] <= p0;
                p[er + 1] <= lam[er] ^ ((sh16 >= 0) ? rotl(p0, sh16[6:0]) : '0) ^ ((er == 0) ? '0 : p[er]);
                er <= er + 1'b1;
                if (er == NL - 2) est <= E_DONE;
            end
            E_DONE: if (oload) est <= E_IDLE;
        endcase
    end

    // ---------------------------------------------------------------- output: 6 bits per beat
    logic [N-1:0] oreg;
    logic [$clog2(N/6)-1:0] ocnt;                      // beats left in the codeword
    logic [$clog2(CW)-1:0]  wcnt;                      // codewords sent in this frame
    logic [$clog2(PAD/6+1)-1:0] pcnt;
    logic         opad;
    logic [N-1:0] cw_bits;
    always_comb begin
        cw_bits[K-1:0] = u;
        for (int r = 0; r < NL; r++) cw_bits[K + r * Z +: Z] = p[r];
    end
    assign obusy    = (ocnt != 0) | opad;
    assign m_tvalid = (ocnt != 0) | opad;
    assign m_tdata  = opad ? 6'd0 : oreg[5:0];
    assign m_tlast  = opad & (pcnt == PAD / 6 - 1);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin ocnt <= '0; wcnt <= '0; pcnt <= '0; opad <= 1'b0; end
        else begin
            if (oload) begin oreg <= cw_bits; ocnt <= N / 6; end
            else if (m_tvalid & m_tready) begin
                if (opad) begin
                    if (pcnt == PAD / 6 - 1) begin opad <= 1'b0; pcnt <= '0; end
                    else pcnt <= pcnt + 1'b1;
                end else begin
                    oreg <= oreg >> 6;
                    ocnt <= ocnt - 1'b1;
                    if (ocnt == 1) begin
                        if (wcnt == CW - 1) begin wcnt <= '0; opad <= 1'b1; end
                        else wcnt <= wcnt + 1'b1;
                    end
                end
            end
        end
    end
endmodule
