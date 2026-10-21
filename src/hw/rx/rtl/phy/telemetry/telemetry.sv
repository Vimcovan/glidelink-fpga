`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// telemetry: periodic one-frame snapshot of the RX equalizer stages + statistics,
//            read by the PC through JTAG-to-AXI (this module is an AXI4 slave).
//
// Capture (all in clk domain, taps are the read-only dbg_* ports of rx_baseband_top):
//   every PERIOD ms: arm -> wait start of a pre-EQ frame (64 x 22 samples, sym0/1 = LTF1/LTF2) -> capture it,
//   then CE / CPE / SFO (64 x 20 each) capture their next frame start after that (same frame: frames are
//   ~146 us apart, pipeline latency is far shorter). The bit errors of that frame come from the frame_done
//   with the same frame index (SFO frames and frame_done are counted from reset; decode latency is longer
//   than a frame spacing, so "the next frame_done" would belong to the previous frame).
//   When complete: write the header of the write buffer, flip ping-pong, seq++.
//   Not complete within TIMEOUT -> abandon, timeouts++.
//
// Word map (32-bit words, AXI byte address = word*4):
//   0x0000 global : 0 magic 0x7E1E0001 | 1 seq (completed snapshots) | 2 last buffer (always 0: single buffer in the JSCC version) |
//                   3 period_ms (R/W) | 4 timeouts | 5 enable (R/W, reset 1) | 6 layout version (4: + SSCC packets) |
//                   7 start_sym (R/W, reset 0): first captured data symbol (0..NSYM-20), e.g. NSYM-20 = frame tail |
//                   8 SSCC packets decoded | 9 SSCC buffer of the newest packet | 10 SSCC input FIFO overflows |
//                   11 SSCC packet bytes
//   0x0040 + 64*b header of buffer b (b = 0,1):
//                   0 seq | 1 frame_bit_err | 2 [0] frame_bad, [31:16] start_sym of this snapshot | 3 agc ctrl (CTRL_OUT: [7] lock, [6:0] gain idx) |
//                   4/5 bit_rcvd lo/hi | 6/7 bit_err lo/hi | 8/9 frame_rcvd lo/hi | 10/11 frame_err lo/hi |
//                   12 n_pre | 13 n_ce | 14 n_cpe | 15 n_sfo  (samples captured)
//   0x1000 + 2048*b  PRE samples  (1408) = LTF1 + LTF2 + 20 data symbols from start_sym, each word = {8'h0, Q[11:0], I[11:0]}
//   0x2000 + 2048*b  CE  samples  (1280)
//   0x3000 + 2048*b  CPE samples  (1280)
//   0x4000 + 2048*b  SFO samples  (1280)
//   0x5000 / 0x6000  SSCC packet buffer 0 / 1 (sscc_rx, PAY_BYTES / 4 words, byte n of a word in bits [8n+7:8n])
//////////////////////////////////////////////////////////////////////////////////
module telemetry #(
    parameter int CLK_HZ     = 100_000_000,
    parameter int NSYM       = 20,          // data symbols per frame (frame = LTF + NSYM symbols)
    parameter int N_PRE      = 64*22,       // captured samples: pre-EQ = LTF1 + LTF2 + 20 data symbols
    parameter int N_POST     = 64*20,       // captured samples: other stages = first 20 data symbols
    parameter int NERR_W     = 12,          // width of frame_bit_err
    parameter int PERIOD_MS0 = 50,
    parameter int TIMEOUT_MS = 20
)(
    input  logic        clk,
    input  logic        rst_n,
    // taps
    input  logic [23:0] pre_data,  input logic pre_fire,
    input  logic [23:0] ce_data,   input logic ce_fire,
    input  logic [23:0] cpe_data,  input logic cpe_fire,
    input  logic [23:0] sfo_data,  input logic sfo_fire,
    input  logic        frame_done, input logic frame_bad, input logic [NERR_W-1:0] frame_bit_err,
    input  logic [63:0] bit_rcvd_cnt, input logic [63:0] bit_err_cnt,
    input  logic [63:0] frame_rcvd_cnt, input logic [63:0] frame_err_cnt,
    input  logic [7:0]  agc_ctrl,
    // SSCC baseline packets (sscc_rx): read port (one clock latency) + status
    output logic [12:0] sscc_rd_addr,
    input  logic [31:0] sscc_rd_q,
    input  logic [31:0] sscc_seq,
    input  logic        sscc_last_buf,
    input  logic [31:0] sscc_ovf,
    input  logic [15:0] sscc_bytes,
    input  logic [31:0] sscc_cw_fail,       // LDPC codewords not converged (cumulative)
    input  logic [31:0] sscc_cw_iters,      // LDPC decoder iterations (cumulative)
    // AXI4 slave (from jtag_axi)
    input  logic [31:0] s_axi_awaddr,  input logic [7:0] s_axi_awlen, input logic s_axi_awvalid, output logic s_axi_awready,
    input  logic [31:0] s_axi_wdata,   input logic s_axi_wlast,       input logic s_axi_wvalid,  output logic s_axi_wready,
    output logic [1:0]  s_axi_bresp,   output logic s_axi_bvalid,     input logic s_axi_bready,
    input  logic [31:0] s_axi_araddr,  input logic [7:0] s_axi_arlen, input logic s_axi_arvalid, output logic s_axi_arready,
    output logic [31:0] s_axi_rdata,   output logic [1:0] s_axi_rresp, output logic s_axi_rlast,
    output logic        s_axi_rvalid,  input logic s_axi_rready
);
    localparam int CLK_PER_MS = CLK_HZ / 1000;

    //---------------------------------------------------------------- control regs
    logic [31:0] period_ms, seq, timeouts;
    logic        enable, last_buf;
    logic [15:0] start_sym, cap_sym;     // capture window start (data symbol index); cap_sym = latched per snapshot
    wire  [15:0] post_off = {cap_sym[9:0], 6'd0};   // 64*cap_sym

    //---------------------------------------------------------------- stream frame counters
    // position inside the frame of each stream (frame = FR_PRE / FR_POST samples); a capture starts at a frame start
    localparam int FR_PRE  = 64*(NSYM+2);    // pre-EQ stream: LTF1 + LTF2 + NSYM data symbols
    localparam int FR_POST = 64*NSYM;
    logic [$clog2(FR_PRE)-1:0] cnt_pre, cnt_ce, cnt_cpe, cnt_sfo;
    logic [31:0] sfo_frames, fd_frames;     // completed SFO frames / frame_done events since reset (1:1 by index)
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin cnt_pre <= '0; cnt_ce <= '0; cnt_cpe <= '0; cnt_sfo <= '0; sfo_frames <= '0; fd_frames <= '0; end
        else begin
            if (sfo_fire && cnt_sfo == FR_POST-1) sfo_frames <= sfo_frames + 1'b1;
            if (frame_done) fd_frames <= fd_frames + 1'b1;
            if (pre_fire) cnt_pre <= (cnt_pre == FR_PRE-1)  ? '0 : cnt_pre + 1'b1;
            if (ce_fire)  cnt_ce  <= (cnt_ce  == FR_POST-1) ? '0 : cnt_ce  + 1'b1;
            if (cpe_fire) cnt_cpe <= (cnt_cpe == FR_POST-1) ? '0 : cnt_cpe + 1'b1;
            if (sfo_fire) cnt_sfo <= (cnt_sfo == FR_POST-1) ? '0 : cnt_sfo + 1'b1;
        end
    end

    //---------------------------------------------------------------- capture control
    logic [31:0] tmr;          // clocks since last arm
    logic        armed, wb;    // wb = buffer being written
    logic        pre_act, ce_act, cpe_act, sfo_act;      // capturing
    logic        pre_done, ce_done, cpe_done, sfo_done, nerr_done;
    logic        post_go;      // pre-EQ frame of this snapshot has started
    logic        sfo_started;
    logic [31:0] sfo_idx;      // frame index of the captured SFO frame
    logic [10:0] n_pre, n_ce, n_cpe, n_sfo;
    logic [NERR_W-1:0] cap_nerr; logic cap_bad;
    logic        commit;

    wire pre_start = armed & ~pre_act & ~pre_done & pre_fire & (cnt_pre == 0);
    wire ce_start  = post_go & ~ce_act  & ~ce_done  & ce_fire  & (cnt_ce  == post_off);
    wire cpe_start = post_go & ~cpe_act & ~cpe_done & cpe_fire & (cnt_cpe == post_off);
    wire sfo_start = post_go & ~sfo_act & ~sfo_done & sfo_fire & (cnt_sfo == post_off);
    // pre-EQ: LTF1 + LTF2 (samples 0..127, host: per-subcarrier noise from LTF1 - LTF2) + 20 data symbols from
    // start_sym (skip the symbols in between)
    wire pre_in_win = (cnt_pre < 128) || (cnt_pre >= 128 + post_off);
    wire pre_wr = pre_start | (pre_act & pre_fire & pre_in_win);
    wire ce_wr  = ce_start  | (ce_act  & ce_fire);
    wire cpe_wr = cpe_start | (cpe_act & cpe_fire);
    wire sfo_wr = sfo_start | (sfo_act & sfo_fire);
    wire all_done = pre_done & ce_done & cpe_done & sfo_done & nerr_done;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            tmr <= '0; armed <= 1'b0; wb <= 1'b0; last_buf <= 1'b0; seq <= '0; timeouts <= '0;
            {pre_act, ce_act, cpe_act, sfo_act} <= '0;
            {pre_done, ce_done, cpe_done, sfo_done, nerr_done} <= '0;
            post_go <= 1'b0; sfo_started <= 1'b0; sfo_idx <= '0; cap_sym <= '0;
            {n_pre, n_ce, n_cpe, n_sfo} <= '0; cap_nerr <= '0; cap_bad <= 1'b0; commit <= 1'b0;
        end
        else begin
            commit <= 1'b0;
            tmr <= tmr + 1'b1;
            if (!armed) begin
                if (enable && !commit && tmr >= period_ms * CLK_PER_MS) begin   // not in the commit cycle: last_buf not yet updated
                    armed <= 1'b1; tmr <= '0; wb <= 1'b0; cap_sym <= start_sym;     // single buffer (JSCC version)
                    {pre_act, ce_act, cpe_act, sfo_act} <= '0;
                    {pre_done, ce_done, cpe_done, sfo_done, nerr_done} <= '0;
                    post_go <= 1'b0; sfo_started <= 1'b0;
                    {n_pre, n_ce, n_cpe, n_sfo} <= '0;
                end
            end
            else if (!enable) begin                             // host is reading: abort, the buffer must not change
                armed <= 1'b0; tmr <= '0;
            end
            else if (tmr >= TIMEOUT_MS * CLK_PER_MS) begin      // abandon
                armed <= 1'b0; tmr <= '0; timeouts <= timeouts + 1'b1;
            end
            else begin
                // PRE
                if (pre_start) begin pre_act <= 1'b1; post_go <= 1'b1; end
                if (pre_wr) begin
                    n_pre <= n_pre + 1'b1;
                    if (n_pre == N_PRE-1) begin pre_act <= 1'b0; pre_done <= 1'b1; end
                end
                // CE / CPE / SFO
                if (ce_start)  ce_act  <= 1'b1;
                if (ce_wr)  begin n_ce  <= n_ce  + 1'b1; if (n_ce  == N_POST-1) begin ce_act  <= 1'b0; ce_done  <= 1'b1; end end
                if (cpe_start) cpe_act <= 1'b1;
                if (cpe_wr) begin n_cpe <= n_cpe + 1'b1; if (n_cpe == N_POST-1) begin cpe_act <= 1'b0; cpe_done <= 1'b1; end end
                if (sfo_start) begin sfo_act <= 1'b1; sfo_started <= 1'b1; sfo_idx <= sfo_frames; end
                if (sfo_wr) begin n_sfo <= n_sfo + 1'b1; if (n_sfo == N_POST-1) begin sfo_act <= 1'b0; sfo_done <= 1'b1; end end
                // bit errors of this frame: the frame_done with the same frame index
                if (sfo_started && !nerr_done && frame_done && fd_frames == sfo_idx) begin
                    cap_nerr <= frame_bit_err; cap_bad <= frame_bad; nerr_done <= 1'b1;
                end
                if (all_done) begin
                    armed <= 1'b0; commit <= 1'b1;
                end
            end
            if (commit) begin last_buf <= wb; seq <= seq + 1'b1; end
        end
    end

    //---------------------------------------------------------------- headers
    logic [31:0] hdr [0:1][0:15];
    always_ff @(posedge clk) begin
        if (armed && all_done) begin
            hdr[wb][0]  <= seq + 1'b1;
            hdr[wb][1]  <= 32'(cap_nerr);
            hdr[wb][2]  <= {cap_sym, 15'd0, cap_bad};
            hdr[wb][3]  <= {24'd0, agc_ctrl};
            hdr[wb][4]  <= bit_rcvd_cnt[31:0];   hdr[wb][5]  <= bit_rcvd_cnt[63:32];
            hdr[wb][6]  <= bit_err_cnt[31:0];    hdr[wb][7]  <= bit_err_cnt[63:32];
            hdr[wb][8]  <= frame_rcvd_cnt[31:0]; hdr[wb][9]  <= frame_rcvd_cnt[63:32];
            hdr[wb][10] <= frame_err_cnt[31:0];  hdr[wb][11] <= frame_err_cnt[63:32];
            hdr[wb][12] <= {21'd0, n_pre}; hdr[wb][13] <= {21'd0, n_ce};
            hdr[wb][14] <= {21'd0, n_cpe}; hdr[wb][15] <= {21'd0, n_sfo};
        end
    end

    //---------------------------------------------------------------- sample RAMs (simple dual port, 2 x 2048 x 24)
    // single buffer (JSCC version: frees 6 BRAM36). The host pauses capture (enable = 0) while it reads, and a
    // capture in progress is aborted then, so the committed snapshot cannot be overwritten during a read.
    (* ram_style = "block" *) logic [23:0] ram_pre [0:2047];
    (* ram_style = "block" *) logic [23:0] ram_ce  [0:2047];
    (* ram_style = "block" *) logic [23:0] ram_cpe [0:2047];
    (* ram_style = "block" *) logic [23:0] ram_sfo [0:2047];
    always_ff @(posedge clk) begin
        if (pre_wr) ram_pre[n_pre] <= pre_data;
        if (ce_wr)  ram_ce [n_ce ] <= ce_data;
        if (cpe_wr) ram_cpe[n_cpe] <= cpe_data;
        if (sfo_wr) ram_sfo[n_sfo] <= sfo_data;
    end

    //---------------------------------------------------------------- AXI4 read (INCR bursts), 2 clocks per beat
    typedef enum logic [1:0] {R_IDLE, R_ADDR, R_DATA} rst_t;
    rst_t        rs;
    logic [15:0] rword;     // current word index
    logic [7:0]  rleft;
    logic [23:0] q_pre, q_ce, q_cpe, q_sfo;
    logic [31:0] q_misc;
    logic [15:0] rword_d;
    always_ff @(posedge clk) begin
        q_pre <= ram_pre[rword[10:0]];
        q_ce  <= ram_ce [rword[10:0]];
        q_cpe <= ram_cpe[rword[10:0]];
        q_sfo <= ram_sfo[rword[10:0]];
        rword_d <= rword;
        case (rword[15:6])
            10'd0: case (rword[5:0])
                       6'd0: q_misc <= 32'h7E1E_0001;
                       6'd1: q_misc <= seq;
                       6'd2: q_misc <= {31'd0, last_buf};
                       6'd3: q_misc <= period_ms;
                       6'd4: q_misc <= timeouts;
                       6'd5: q_misc <= {31'd0, enable};
                       6'd6: q_misc <= 32'd4;
                       6'd7: q_misc <= {16'd0, start_sym};
                       6'd8: q_misc <= sscc_seq;
                       6'd9: q_misc <= {31'd0, sscc_last_buf};
                       6'd10: q_misc <= sscc_ovf;
                       6'd11: q_misc <= {16'd0, sscc_bytes};
                       6'd12: q_misc <= sscc_cw_fail;
                       6'd13: q_misc <= sscc_cw_iters;
                       default: q_misc <= 32'd0;
                   endcase
            10'd1: q_misc <= (rword[5:4] == 2'd0) ? hdr[0][rword[3:0]] : 32'd0;
            10'd2: q_misc <= (rword[5:4] == 2'd0) ? hdr[1][rword[3:0]] : 32'd0;
            default: q_misc <= 32'd0;
        endcase
    end
    wire [31:0] rdata_mux = (rword_d[15:12] == 4'h1) ? {8'd0, q_pre} :
                            (rword_d[15:12] == 4'h2) ? {8'd0, q_ce}  :
                            (rword_d[15:12] == 4'h3) ? {8'd0, q_cpe} :
                            (rword_d[15:12] == 4'h4) ? {8'd0, q_sfo} :
                            (rword_d[15:12] == 4'h5 || rword_d[15:12] == 4'h6) ? sscc_rd_q : q_misc;
    assign sscc_rd_addr = {rword[15:12] == 4'h6, rword[11:0]};
    assign s_axi_rresp   = 2'b00;
    assign s_axi_arready = (rs == R_IDLE);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin rs <= R_IDLE; s_axi_rvalid <= 1'b0; s_axi_rlast <= 1'b0; rword <= '0; rleft <= '0; s_axi_rdata <= '0; end
        else case (rs)
            R_IDLE: if (s_axi_arvalid) begin rword <= s_axi_araddr[17:2]; rleft <= s_axi_arlen; rs <= R_ADDR; end
            R_ADDR: rs <= R_DATA;                       // RAM / mux latency
            R_DATA: begin
                if (!s_axi_rvalid) begin
                    s_axi_rvalid <= 1'b1; s_axi_rdata <= rdata_mux; s_axi_rlast <= (rleft == 0);
                end
                else if (s_axi_rready) begin
                    s_axi_rvalid <= 1'b0; s_axi_rlast <= 1'b0;
                    if (rleft == 0) rs <= R_IDLE;
                    else begin rleft <= rleft - 1'b1; rword <= rword + 1'b1; rs <= R_ADDR; end
                end
            end
            default: rs <= R_IDLE;
        endcase
    end

    //---------------------------------------------------------------- AXI4 write (control regs only: period_ms, enable, start_sym)
    typedef enum logic [1:0] {W_IDLE, W_DATA, W_RESP} wst_t;
    wst_t        ws;
    logic [15:0] wword;
    assign s_axi_awready = (ws == W_IDLE);
    assign s_axi_wready  = (ws == W_DATA);
    assign s_axi_bresp   = 2'b00;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin ws <= W_IDLE; s_axi_bvalid <= 1'b0; wword <= '0; period_ms <= PERIOD_MS0; enable <= 1'b1; start_sym <= '0; end
        else case (ws)
            W_IDLE: if (s_axi_awvalid) begin wword <= s_axi_awaddr[17:2]; ws <= W_DATA; end
            W_DATA: if (s_axi_wvalid) begin
                        if (wword == 16'd3) period_ms <= (s_axi_wdata == 0) ? 32'd1 : s_axi_wdata;
                        if (wword == 16'd5) enable    <= s_axi_wdata[0];
                        if (wword == 16'd7) start_sym <= (s_axi_wdata > NSYM-20) ? 16'(NSYM-20) : s_axi_wdata[15:0];
                        wword <= wword + 1'b1;
                        if (s_axi_wlast) begin ws <= W_RESP; s_axi_bvalid <= 1'b1; end
                    end
            W_RESP: if (s_axi_bready) begin s_axi_bvalid <= 1'b0; ws <= W_IDLE; end
            default: ws <= W_IDLE;
        endcase
    end
endmodule
