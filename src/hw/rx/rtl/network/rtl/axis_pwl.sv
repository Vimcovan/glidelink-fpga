`timescale 1ns / 1ps
// Per-channel piecewise-linear activation (knot form), bit-exact with quant_shallow.IntB.pwl / export_shallow.py:
//   s   = clamp(((x - T0) >>> SL) + 1, 0, NK)          segment (knots T0 + k * 2^SL, k = 0 .. NK-1)
//   k   = max(s - 1, 0)                                 left knot of the segment (T0 below the first knot)
//   acc = Y[c][k] + S[c][s] * (x - (T0 + (k << SL)))
//   y   = sat(rsr(sat27(rsr(acc, PRE)) * M[c], SH[c]), X_W)
// c is the channel of the element in the NHWC stream (counted, reset after tlast = last channel of a pixel).
// Both products use the radix-4 serial LUT multiplier (no DSP); one element every ~20 cycles, enough for the
// 64x64x16 latent-resolution tensors (about 127 cycles per element at 30 fps and 250 MHz).
module axis_pwl #(
    parameter int    X_W        = 12,
    parameter int    C          = 16,
    parameter int    NK         = 9,               // knots; NK + 1 segments
    parameter int    T0         = -256,            // first knot (input LSB)
    parameter int    SL         = 6,               // knot spacing 2^SL
    parameter int    YW         = 17,              // knot ordinate width (signed, accumulator scale)
    parameter int    PRE        = 0,
    parameter string SLOPE_FILE = "slope.mem",     // [C][NK+1] 8-bit signed
    parameter string YK_FILE    = "yk.mem",        // [C][NK] YW-bit signed
    parameter string M_FILE     = "m.mem",         // [C] 17-bit unsigned
    parameter string SH_FILE    = "sh.mem",        // [C] 8-bit, >= 1
    parameter int    U_W        = 1
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic signed [X_W-1:0] s_tdata,
    input  logic                  s_tvalid,
    output logic                  s_tready,
    input  logic                  s_tlast,
    input  logic [U_W-1:0]        s_tuser,
    output logic [X_W-1:0]        m_tdata,
    output logic                  m_tvalid,
    input  logic                  m_tready,
    output logic                  m_tlast,
    output logic [U_W-1:0]        m_tuser
);
    localparam int CW  = (C > 1) ? $clog2(C) : 1;
    localparam int SW  = $clog2(NK + 1);
    localparam int OW  = X_W + 3;                         // x - knot: |x| < 2^(X_W-1), |knot| < 2^(X_W+1)
    localparam int AW  = YW + OW + 9;                     // accumulator
    localparam int PW  = 27 + 17 + 1;                     // requant product

    (* rom_style = "distributed" *) logic [7:0]    srom [C * (NK + 1)];
    (* rom_style = "distributed" *) logic [YW-1:0] yrom [C * NK];
    (* rom_style = "distributed" *) logic [16:0]   mrom [C];
    (* rom_style = "distributed" *) logic [7:0]    hrom [C];
    initial begin
        $readmemh(SLOPE_FILE, srom); $readmemh(YK_FILE, yrom); $readmemh(M_FILE, mrom); $readmemh(SH_FILE, hrom);
    end

    typedef enum logic [3:0] {S_IDLE, S_SEG, S_ROM, S_ST1, S_MUL1, S_ACC, S_ST2, S_MUL2, S_RQ, S_OUT} st_t;
    st_t st;

    logic [CW-1:0]          ch, ch_e;
    logic signed [X_W-1:0]  x;
    logic [SW-1:0]          seg, kn;
    logic signed [OW-1:0]   off;
    logic signed [7:0]      slope;
    logic signed [YW-1:0]   yk;
    logic [16:0]            mq;
    logic [7:0]             sh;
    logic signed [AW-1:0]   acc;
    logic signed [26:0]     a27;

    // ---- segment: d = x - T0, s = (d >>> SL) + 1, clamped
    wire signed [X_W+2:0] d   = (X_W+3)'(x) - (X_W+3)'(T0);
    wire signed [X_W+2:0] q   = (d >>> SL) + 1;
    wire [SW-1:0]         sc  = (q < 0) ? '0 : (q > NK) ? SW'(NK) : SW'(q);

    // ---- multipliers: |slope| * off (signed x unsigned), then a27 * M
    logic m1_start, m1_busy, m1_done, m2_start, m2_busy, m2_done;
    logic signed [OW+8:0]  p1;
    logic signed [PW-1:0]  p2;
    wire  [7:0]            sabs = slope[7] ? 8'(-slope) : 8'(slope);
    mul_serial #(.AW(OW), .BW(8)) u_m1 (.clk(clk), .rst_n(rst_n), .start(m1_start), .a(off), .b(sabs),
                                        .busy(m1_busy), .done(m1_done), .p(p1));
    mul_serial #(.AW(27), .BW(17)) u_m2 (.clk(clk), .rst_n(rst_n), .start(m2_start), .a(a27), .b(mq),
                                         .busy(m2_busy), .done(m2_done), .p(p2));
    assign m1_start = (st == S_ST1);                    // operands registered in the state before
    assign m2_start = (st == S_ST2);

    // ---- requantization pieces
    wire signed [AW-1:0]   pre_r = (PRE > 0) ? ((acc + (AW'(1) <<< (PRE - 1))) >>> PRE) : acc;
    wire signed [26:0]     sat27 = (pre_r > AW'((1 <<< 26) - 1)) ? 27'sd67108863 :
                                   (pre_r < -AW'(1 <<< 26)) ? -27'sd67108864 : 27'(pre_r);
    wire signed [PW-1:0]   rnd   = (p2 + (PW'(1) <<< (sh - 1))) >>> sh;
    localparam signed [PW-1:0] YMAX = (1 <<< (X_W - 1)) - 1;
    wire signed [X_W-1:0]  ysat  = (rnd > YMAX) ? X_W'(YMAX) : (rnd < -YMAX - 1) ? X_W'(-YMAX - 1) : X_W'(rnd);

    assign s_tready = rst_n && (st == S_IDLE);
    assign m_tvalid = (st == S_OUT);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            st <= S_IDLE; ch <= '0;
        end else case (st)
            S_IDLE: if (s_tvalid) begin
                st <= S_SEG;
                ch <= (s_tlast || ch == CW'(C - 1)) ? '0 : ch + 1'b1;
            end
            S_SEG:  st <= S_ROM;
            S_ROM:  st <= S_ST1;
            S_ST1:  st <= S_MUL1;
            S_MUL1: if (m1_done) st <= S_ACC;
            S_ACC:  st <= S_ST2;
            S_ST2:  st <= S_MUL2;
            S_MUL2: if (m2_done) st <= S_RQ;
            S_RQ:   st <= S_OUT;
            S_OUT:  if (m_tready) st <= S_IDLE;
            default: st <= S_IDLE;
        endcase
    end

    always_ff @(posedge clk) begin
        case (st)
            S_IDLE: if (s_tvalid) begin
                x <= s_tdata; ch_e <= ch; m_tlast <= s_tlast; m_tuser <= s_tuser;
            end
            S_SEG: begin
                seg <= sc;
                kn  <= (sc == 0) ? '0 : sc - 1'b1;
            end
            S_ROM: begin
                slope <= $signed(srom[ch_e * (NK + 1) + seg]);
                yk    <= $signed(yrom[ch_e * NK + kn]);
                mq    <= mrom[ch_e];
                sh    <= hrom[ch_e];
                off   <= OW'(x) - OW'(T0 + (int'(kn) <<< SL));
            end
            S_MUL1: if (m1_done) acc <= AW'(yk) + (slope[7] ? -AW'(p1) : AW'(p1));
            S_ACC:  a27 <= sat27;
            S_RQ:   m_tdata <= ysat;
            default: ;
        endcase
    end
endmodule
