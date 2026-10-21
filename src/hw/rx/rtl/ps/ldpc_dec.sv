`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// IEEE 802.11n LDPC decoder, n = 1944, rate 2/3 (Z = 81, ldpc_r23_pkg): layered normalized min-sum, Z checks of a
// layer in parallel; bit-exact with decode() of src/model/ldpc_ref.py.
//   in : channel LLRs (LW bits, > 0: bit 0) in coded-bit order, 1944 per codeword, back-pressured
//   out: the 1296 information bits, one per clock (no back-pressure), and per codeword ok / iterations
// Arithmetic: APP (AW) = LLR initially; v2c = sat_MW(APP - c2v_old); per check min1 / min2 / position of min1 /
// sign product; c2v = sign * (m - (m >> 2)); APP = sat_AW(v2c + c2v). Stops after the first iteration in which every
// check was satisfied by the APP signs it read and no APP sign changed, at most MAX_ITER iterations.
// Pipeline: phase 1 of a layer (read APP of a block column, rotate, subtract the old c2v, min search: one block per
// clock) overlaps phase 2 of the previous layer (new c2v, add, rotate back, write: one block per clock). A column
// read by a layer stays "pending" until that layer has written it; a read of a pending column waits (scoreboard).
// Two banks of v2c buffer / min search alternate between layers. About 130 clocks per iteration; LLR input
// double-buffered (a codeword arrives in ~2700 clocks), information bits shifted out while the next one decodes.
// Storage: APP 24 x 891 bit, c2v 8 x 2511 bit (min1, min2 scaled, position, 11 signs per check), v2c 2 x 11 x 729
// bit, input 2 x 24 x 567 bit: distributed RAM.
//////////////////////////////////////////////////////////////////////////////////
module ldpc_dec
    import ldpc_r23_pkg::*;
#(
    parameter int LW = 7,
    parameter int AW = 11,
    parameter int MW = 9,
    parameter int MAX_ITER = 16
)(
    input  logic                 clk,
    input  logic                 rst_n,
    input  logic signed [LW-1:0] s_llr,
    input  logic                 s_valid,
    output logic                 s_ready,
    output logic                 m_bit,
    output logic                 m_valid,
    output logic                 cw_done,                // one clock with the first output bit of a codeword
    output logic                 cw_ok,
    output logic [4:0]           cw_iter
);
    localparam int CWB = MW - 1;                         // |v2c| <= 2^(MW-1)-1 -> CWB bits
    localparam int IW  = 4;                              // edge index 0..10
    localparam int MSGW = 2 * CWB + IW + DC;             // per check: m1s, m2s, idx, c2v signs

    function automatic logic [Z*AW-1:0] rot_a(input logic [Z*AW-1:0] v, input logic [6:0] s);   // lane i <- v[i+s]
        logic [Z*AW-1:0] t, u;
        t = v;
        for (int b = 0; b < 7; b++) begin
            for (int i = 0; i < Z; i++) u[i*AW +: AW] = t[((i + (1 << b)) % Z)*AW +: AW];
            if (s[b]) t = u;
        end
        return t;
    endfunction
    function automatic logic signed [MW-1:0] sat_m(input logic signed [AW+1:0] x);
        localparam logic signed [AW+1:0] MX = 2 ** (MW - 1) - 1;
        return (x > MX) ? MW'(MX) : (x < -MX) ? MW'(-MX) : MW'(x);
    endfunction
    function automatic logic signed [AW-1:0] sat_a(input logic signed [AW+1:0] x);
        localparam logic signed [AW+1:0] MX = 2 ** (AW - 1) - 1;
        return (x > MX) ? AW'(MX) : (x < -MX) ? AW'(-MX) : AW'(x);
    endfunction

    // ================================================================ input: 2 x 24 words of Z LLRs
    (* ram_style = "distributed" *) logic [Z*LW-1:0] ibuf [0:63];      // {bank, column}
    logic [Z*LW-1:0] isr;
    logic [6:0]      ilane;
    logic [4:0]      icol;
    logic            iwb, irb;
    logic [1:0]      ifull;
    logic            iload_done;
    assign s_ready = ~ifull[iwb];
    always_ff @(posedge clk) if (s_valid & s_ready & (ilane == Z - 1)) ibuf[{iwb, icol}] <= {s_llr, isr[Z*LW-1:LW]};
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin ilane <= '0; icol <= '0; iwb <= 1'b0; ifull <= '0; end
        else begin
            if (s_valid & s_ready) begin
                isr <= {s_llr, isr[Z*LW-1:LW]};
                if (ilane == Z - 1) begin
                    ilane <= '0;
                    if (icol == NB - 1) begin icol <= '0; ifull[iwb] <= 1'b1; iwb <= ~iwb; end
                    else icol <= icol + 1'b1;
                end else ilane <= ilane + 1'b1;
            end
            if (iload_done) ifull[~irb] <= 1'b0;                 // irb has already moved on
        end
    end

    // ================================================================ storage
    (* ram_style = "distributed" *) logic [Z*AW-1:0]   app  [0:NB-1];
    (* ram_style = "distributed" *) logic [Z*MSGW-1:0] msg  [0:NL-1];
    (* ram_style = "distributed" *) logic [Z*MW-1:0]   vbuf [0:2*16-1];   // {bank, edge}
    logic [NB-1:0] pending;

    // ================================================================ control
    typedef enum logic [2:0] {C_IDLE, C_LOAD, C_RUN, C_OUT, C_WAIT} cst_t;
    cst_t        cst;
    logic [4:0]  ccnt;
    logic        abort;                                  // stop decision: flush the pipeline
    logic        stop_ok;
    logic [4:0]  stop_iter;

    logic        p2_take, p2_run, p2_bank;               // phase 2 handshake (declared early: used by phase 1)
    // ---------------------------------------------------------------- phase 1
    logic        p1_run;                                 // issuing reads
    logic [2:0]  p1_l;
    logic [3:0]  p1_k;
    logic [4:0]  p1_it;
    logic        p1_bank;
    logic [1:0]  bank_busy;                              // min search result not yet consumed by phase 2
    logic [4:0]  p1_col;
    always_comb p1_col = order_of(p1_l, p1_k);
    logic        p1_issue;
    assign p1_issue = p1_run & ~pending[p1_col] & ~((p1_k == 0) & bank_busy[p1_bank]) & ~abort;
    // stage 1
    logic              s1_v, s1_last, s1_first, s1_bank;
    logic [3:0]        s1_k;
    logic [2:0]        s1_l;
    logic [Z*AW-1:0]   s1_a;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) s1_v <= 1'b0;
        else begin
            s1_v <= p1_issue;
            if (p1_issue) begin
                s1_a <= rot_a(app[p1_col], 7'(shift_of(p1_l, p1_col)));
                s1_k <= p1_k; s1_l <= p1_l; s1_last <= (p1_k == DC - 1); s1_first <= (p1_it == 0); s1_bank <= p1_bank;
            end
        end
    end
    // stage 2: v2c = sat(APP - c2v_old)
    logic [Z*MSGW-1:0] s1_msg;
    assign s1_msg = msg[s1_l];
    logic              s2_v, s2_last, s2_bank;
    logic [3:0]        s2_k;
    logic [2:0]        s2_l;
    logic [Z*MW-1:0]   s2_v2c;
    logic [Z-1:0]      s2_hs;                            // APP signs read (hard decisions)
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) s2_v <= 1'b0;
        else begin
            s2_v <= s1_v & ~abort;
            if (s1_v) begin
                for (int i = 0; i < Z; i++) begin
                    logic signed [AW-1:0]   a;
                    logic [CWB-1:0]         m1s, m2s, mag;
                    logic [IW-1:0]          id;
                    logic [DC-1:0]          sg;
                    logic signed [AW+1:0]   old;
                    a   = s1_a[i*AW +: AW];
                    {m1s, m2s, id, sg} = s1_msg[i*MSGW +: MSGW];
                    mag = (id == s1_k) ? m2s : m1s;
                    old = s1_first ? '0 : (sg[s1_k] ? -$signed({3'b0, mag}) : $signed({3'b0, mag}));
                    s2_v2c[i*MW +: MW] <= sat_m($signed({a[AW-1], a[AW-1], a}) - old);
                    s2_hs[i] <= a[AW-1];
                end
                s2_k <= s1_k; s2_l <= s1_l; s2_last <= s1_last; s2_bank <= s1_bank;
            end
        end
    end
    // stage 3: v2c buffer, min search (per bank), parity of the hard decisions
    logic [CWB-1:0]  mf_m1 [2][Z];
    logic [CWB-1:0]  mf_m2 [2][Z];
    logic [IW-1:0]   mf_id [2][Z];
    logic [Z-1:0]    mf_sp [2];                          // sign product
    logic [Z-1:0]    mf_par;                             // parity of the hard decisions (current layer)
    logic [DC-1:0]   mf_sg [2][Z];                       // v2c signs per edge
    logic [Z-1:0]    hs_buf [2][DC];                     // hard decisions read
    logic [1:0]      res_v;                              // bank holds a finished layer for phase 2
    logic [2:0]      res_l [2];
    logic            chk_acc, chk_iter;                  // all checks satisfied (this / last finished iteration)
    always_ff @(posedge clk) if (s2_v & ~abort) vbuf[{s2_bank, s2_k}] <= s2_v2c;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin res_v <= '0; chk_acc <= 1'b1; chk_iter <= 1'b0; end
        else begin
            if (abort) begin res_v <= '0; chk_acc <= 1'b1; end
            else if (s2_v) begin
                logic [Z-1:0] par;
                hs_buf[s2_bank][s2_k] <= s2_hs;
                par = (s2_k == 0) ? s2_hs : (mf_par ^ s2_hs);
                mf_par <= par;
                for (int i = 0; i < Z; i++) begin
                    logic signed [MW-1:0] v;
                    logic [CWB-1:0] m;
                    v = s2_v2c[i*MW +: MW];
                    m = v[MW-1] ? CWB'(-v) : CWB'(v);
                    mf_sg[s2_bank][i][s2_k] <= v[MW-1];
                    if (s2_k == 0) begin
                        mf_m1[s2_bank][i] <= m; mf_m2[s2_bank][i] <= '1; mf_id[s2_bank][i] <= '0;
                        mf_sp[s2_bank][i] <= v[MW-1];
                    end else begin
                        mf_sp[s2_bank][i] <= mf_sp[s2_bank][i] ^ v[MW-1];
                        if (m < mf_m1[s2_bank][i]) begin
                            mf_m2[s2_bank][i] <= mf_m1[s2_bank][i]; mf_m1[s2_bank][i] <= m; mf_id[s2_bank][i] <= s2_k;
                        end else if (m < mf_m2[s2_bank][i]) mf_m2[s2_bank][i] <= m;
                    end
                end
                if (s2_last) begin
                    res_v[s2_bank] <= 1'b1; res_l[s2_bank] <= s2_l;
                    if (s2_l == NL - 1) begin chk_iter <= chk_acc & ~|par; chk_acc <= 1'b1; end
                    else chk_acc <= chk_acc & ~|par;
                end
            end
            if (p2_take) res_v[p2_bank] <= 1'b0;
        end
    end

    // ---------------------------------------------------------------- phase 2
    logic [2:0]  p2_l;
    logic [3:0]  p2_k;
    logic [4:0]  p2_it;
    logic [CWB-1:0] p2_m1s [Z], p2_m2s [Z];
    logic [IW-1:0]  p2_id [Z];
    logic [DC-1:0]  p2_cs [Z];                           // c2v signs
    logic        flip_acc;
    assign p2_take = ~p2_run & res_v[p2_bank] & ~abort & (cst == C_RUN);
    logic [4:0]  p2_col;
    always_comb p2_col = order_of(p2_l, p2_k);
    logic [Z*MW-1:0] p2_vrd;
    assign p2_vrd = vbuf[{p2_bank, p2_k}];
    // write stage
    logic            w_v;
    logic [4:0]      w_col;
    logic [6:0]      w_sh;
    logic [Z*AW-1:0] w_new;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin p2_run <= 1'b0; p2_bank <= 1'b0; p2_l <= '0; p2_k <= '0; p2_it <= '0; w_v <= 1'b0; end
        else if (abort) begin p2_run <= 1'b0; p2_bank <= 1'b0; p2_l <= '0; p2_k <= '0; p2_it <= '0; w_v <= 1'b0; end
        else begin
            w_v <= 1'b0;
            if (p2_take) begin
                p2_run <= 1'b1; p2_k <= '0; p2_l <= res_l[p2_bank];
                for (int i = 0; i < Z; i++) begin
                    logic [CWB-1:0] m1, m2;
                    m1 = mf_m1[p2_bank][i]; m2 = mf_m2[p2_bank][i];
                    p2_m1s[i] <= m1 - (m1 >> 2); p2_m2s[i] <= m2 - (m2 >> 2);
                    p2_id[i]  <= mf_id[p2_bank][i];
                    p2_cs[i]  <= mf_sg[p2_bank][i] ^ {DC{mf_sp[p2_bank][i]}};
                end
            end else if (p2_run) begin
                logic f;
                f = 1'b0;
                for (int i = 0; i < Z; i++) begin
                    logic signed [MW-1:0] v;
                    logic [CWB-1:0] mag;
                    logic signed [AW+1:0] c, n;
                    v   = p2_vrd[i*MW +: MW];
                    mag = (p2_id[i] == p2_k) ? p2_m2s[i] : p2_m1s[i];
                    c   = p2_cs[i][p2_k] ? -$signed({3'b0, mag}) : $signed({3'b0, mag});
                    n   = $signed({{(AW+3-MW){v[MW-1]}}, v}) + c;
                    w_new[i*AW +: AW] <= sat_a(n);
                    f |= (n < 0) != hs_buf[p2_bank][p2_k][i];
                end
                flip_acc <= ((p2_k == 0) & (p2_l == 0) ? 1'b0 : flip_acc) | f;
                w_v <= 1'b1; w_col <= p2_col; w_sh <= 7'((Z - int'(shift_of(p2_l, p2_col))) % Z);
                if (p2_k == DC - 1) begin
                    p2_run <= 1'b0; p2_bank <= ~p2_bank;
                    if (p2_l == NL - 1) p2_it <= p2_it + 1'b1;
                end else p2_k <= p2_k + 1'b1;
            end
        end
    end
    // message store: written when phase 2 takes a layer
    logic [Z*MSGW-1:0] msg_wd;
    always_comb
        for (int i = 0; i < Z; i++) begin
            logic [CWB-1:0] m1, m2;
            m1 = mf_m1[p2_bank][i]; m2 = mf_m2[p2_bank][i];
            msg_wd[i*MSGW +: MSGW] = {CWB'(m1 - (m1 >> 2)), CWB'(m2 - (m2 >> 2)), mf_id[p2_bank][i],
                                      mf_sg[p2_bank][i] ^ {DC{mf_sp[p2_bank][i]}}};
        end
    always_ff @(posedge clk) if (p2_take) msg[res_l[p2_bank]] <= msg_wd;
    // the iteration ends when the write of the last block of layer NL-1 is issued
    logic it_end;
    assign it_end = p2_run & ~p2_take & (p2_k == DC - 1) & (p2_l == NL - 1) & ~abort;
    logic        last_it_end;                            // registered: decide after chk_iter / flip_acc settle
    logic [4:0]  last_it;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin last_it_end <= 1'b0; last_it <= '0; end
        else begin last_it_end <= it_end; if (it_end) last_it <= p2_it; end
    end

    // ================================================================ APP RAM writes (load / phase 2) and control
    logic [Z*LW-1:0] ld_word;
    assign ld_word = ibuf[{irb, ccnt[4:0]}];
    logic [Z*AW-1:0] ld_app;
    always_comb for (int i = 0; i < Z; i++) ld_app[i*AW +: AW] = AW'($signed(ld_word[i*LW +: LW]));
    always_ff @(posedge clk) begin
        if (cst == C_LOAD) app[ccnt] <= ld_app;
        else if (w_v) app[w_col] <= rot_a(w_new, w_sh);
    end
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) pending <= '0;
        else if (abort | (cst == C_LOAD)) pending <= '0;
        else begin
            logic [NB-1:0] nx;
            nx = pending;
            if (w_v) nx[w_col] = 1'b0;
            if (p1_issue) nx[p1_col] = 1'b1;
            pending <= nx;
        end
    end
    // phase 1 sequencing
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin p1_run <= 1'b0; p1_l <= '0; p1_k <= '0; p1_it <= '0; p1_bank <= 1'b0; end
        else if (abort | (cst != C_RUN)) begin
            p1_run <= (cst == C_LOAD) & (ccnt == NB - 1);      // start right after the load
            p1_l <= '0; p1_k <= '0; p1_it <= '0; p1_bank <= 1'b0;
        end else if (p1_issue) begin
            if (p1_k == DC - 1) begin
                p1_k <= '0; p1_bank <= ~p1_bank;
                if (p1_l == NL - 1) begin
                    p1_l <= '0; p1_it <= p1_it + 1'b1;
                    if (p1_it == MAX_ITER - 1) p1_run <= 1'b0;
                end else p1_l <= p1_l + 1'b1;
            end else p1_k <= p1_k + 1'b1;
        end
    end
    always_comb bank_busy = res_v | {p2_run & p2_bank, p2_run & ~p2_bank};

    // stop: after an iteration's last write, both flags of that iteration are known (phase 1 finished the iteration
    // before phase 2 did); w_v of the last block lands one clock later, so the decision waits for it
    logic out_free;
    always_comb abort = last_it_end & ((chk_iter & ~flip_acc) | (last_it == MAX_ITER - 1));
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin cst <= C_IDLE; ccnt <= '0; stop_ok <= 1'b0; stop_iter <= '0; irb <= 1'b0; iload_done <= 1'b0; end
        else begin
            iload_done <= 1'b0;
            case (cst)
                C_IDLE: if (ifull[irb]) begin cst <= C_LOAD; ccnt <= '0; end
                C_LOAD: if (ccnt == NB - 1) begin cst <= C_RUN; ccnt <= '0; iload_done <= 1'b1; irb <= ~irb; end
                        else ccnt <= ccnt + 1'b1;
                C_RUN:  if (abort) begin
                            stop_ok <= chk_iter & ~flip_acc; stop_iter <= last_it + 1'b1; cst <= C_WAIT;
                        end
                C_WAIT: if (out_free) begin cst <= C_OUT; ccnt <= '0; end    // last APP write has landed
                C_OUT:  if (ccnt == KB - 1) begin cst <= C_IDLE; ccnt <= '0; end
                        else ccnt <= ccnt + 1'b1;
                default: cst <= C_IDLE;
            endcase
        end
    end

    // ================================================================ output: hard decisions of the 16 info columns
    logic [KB*Z-1:0] obuf;
    logic [10:0]     ocnt;                               // bits left
    logic [Z-1:0]    hard;
    always_comb for (int i = 0; i < Z; i++) hard[i] = app[ccnt[3:0]][i*AW + AW - 1];
    logic [KB*Z-1:0] ocol;
    assign out_free = (ocnt == 0);
    assign m_valid  = (ocnt != 0);
    assign m_bit    = obuf[0];
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin ocnt <= '0; cw_done <= 1'b0; cw_ok <= 1'b0; cw_iter <= '0; end
        else begin
            cw_done <= 1'b0;
            if (cst == C_OUT) begin
                ocol[ccnt[3:0]*Z +: Z] <= hard;
                if (ccnt == KB - 1) begin
                    obuf <= {hard, ocol[(KB-1)*Z-1:0]}; ocnt <= KB * Z;
                    cw_done <= 1'b1; cw_ok <= stop_ok; cw_iter <= stop_iter;
                end
            end else if (ocnt != 0) begin obuf <= obuf >> 1; ocnt <= ocnt - 1'b1; end
        end
    end
endmodule
