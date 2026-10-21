`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// SSCC baseline RX: equalized, PN-descrambled 64-QAM symbols of the PHY (32768 per frame, tlast) -> JPEG packet
//   -> sync FIFO           the PHY cannot be back-pressured (symbols arrive ~8 clocks apart, bursts of 48)
//   -> qam64_softdemap     max-log LLRs, 7-bit two's complement (> 0: bit 0), 6 per symbol
//   -> ldpc_feed           exactly CW * 1944 LLRs per frame (zeros pad a short frame, the PAD tail and any extra
//                          LLRs before tlast are dropped)
//   -> ldpc_dec            802.11n LDPC n = 1944 rate 2/3, layered min-sum, <= 16 iterations (ldpc_dec.sv)
//   -> sscc_rx_bits        descramble, bytes, double-buffered packet RAM (PAY_BYTES), seq / last_buf
//   The PS reads the packet RAM through the telemetry AXI slave (rd_addr / rd_q, one clock latency).
//   Frame = CW = 101 codewords (101 * 1296 = 130896 information bits = 16360 packet bytes + 16 zero tail bits).
//////////////////////////////////////////////////////////////////////////////////
module sscc_rx #(
    parameter int PAY_BYTES  = 16360,
    parameter int FRAME_SYMS = 32768,
    parameter int CW         = 101
)(
    input  logic        clk,
    input  logic        rst_n,
    input  logic [23:0] s_tdata,             // tap of the PHY output (no back-pressure)
    input  logic        s_tlast,
    input  logic        s_fire,
    output logic [31:0] seq,                 // completed packets
    output logic        last_buf,            // buffer of the newest packet
    output logic [31:0] fifo_ovf,            // symbols lost at the input FIFO (should stay 0)
    output logic [31:0] cw_fail,             // codewords not converged (cumulative)
    output logic [31:0] cw_iters,            // decoder iterations (cumulative)
    input  logic [12:0] rd_addr,             // {buffer, word}
    output logic [31:0] rd_q
);
    localparam int FRAME_LLR = FRAME_SYMS * 6;
    // input FIFO
    logic [24:0] f_q; logic f_valid, f_ready;
    sscc_sync_fifo #(.W(25), .DEPTH(1024)) u_fifo (
        .clk, .rst_n, .wr_data({s_tlast, s_tdata}), .wr_en(s_fire), .ovf(fifo_ovf),
        .rd_data(f_q), .rd_valid(f_valid), .rd_ready(f_ready));
    logic [6:0] d_tdata; logic d_tlast, d_tvalid, d_tready;
    qam64_softdemap u_demap (
        .clk, .rst_n, .s_tdata(f_q[23:0]), .s_tlast(f_q[24]), .s_tvalid(f_valid), .s_tready(f_ready),
        .m_tdata(d_tdata), .m_tlast(d_tlast), .m_tvalid(d_tvalid), .m_tready(d_tready));
    logic [6:0] l_tdata; logic l_tvalid, l_tready;
    ldpc_feed #(.N(CW * 1944)) u_feed (
        .clk, .rst_n, .s_tdata(d_tdata), .s_tlast(d_tlast), .s_tvalid(d_tvalid), .s_tready(d_tready),
        .m_tdata(l_tdata), .m_tvalid(l_tvalid), .m_tready(l_tready));
    logic o_bit, o_valid, c_done, c_ok;
    logic [4:0] c_iter;
    ldpc_dec u_dec (
        .clk, .rst_n, .s_llr(l_tdata), .s_valid(l_tvalid), .s_ready(l_tready),
        .m_bit(o_bit), .m_valid(o_valid), .cw_done(c_done), .cw_ok(c_ok), .cw_iter(c_iter));
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin cw_fail <= '0; cw_iters <= '0; end
        else if (c_done) begin cw_fail <= cw_fail + 32'(!c_ok); cw_iters <= cw_iters + 32'(c_iter); end
    end
    sscc_rx_bits #(.PAY_BYTES(PAY_BYTES), .FRAME_OUT(CW * 1296)) u_bits (
        .clk, .rst_n, .s_bit(o_bit), .s_valid(o_valid), .seq, .last_buf, .rd_addr, .rd_q);
endmodule


// simple synchronous FIFO, writes are never refused (overflow counted), first-word-fall-through read side
module sscc_sync_fifo #(
    parameter int W = 25,
    parameter int DEPTH = 1024
)(
    input  logic         clk,
    input  logic         rst_n,
    input  logic [W-1:0] wr_data,
    input  logic         wr_en,
    output logic [31:0]  ovf,
    output logic [W-1:0] rd_data,
    output logic         rd_valid,
    input  logic         rd_ready
);
    localparam int AW = $clog2(DEPTH);
    (* ram_style = "block" *) logic [W-1:0] mem [0:DEPTH-1];
    logic [AW:0] wp, rp;
    wire  full  = (wp - rp) == DEPTH;
    wire  empty = (wp == rp);
    always_ff @(posedge clk) if (wr_en && !full) mem[wp[AW-1:0]] <= wr_data;
    // output register stage (FWFT): q holds mem[rp] once read
    logic q_valid;
    wire  rd_mem = !empty && (!q_valid || rd_ready);
    always_ff @(posedge clk) if (rd_mem) rd_data <= mem[rp[AW-1:0]];
    assign rd_valid = q_valid;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin wp <= '0; rp <= '0; q_valid <= 1'b0; ovf <= '0; end
        else begin
            if (wr_en) begin if (full) ovf <= ovf + 1'b1; else wp <= wp + 1'b1; end
            if (rd_mem) begin rp <= rp + 1'b1; q_valid <= 1'b1; end
            else if (rd_ready) q_valid <= 1'b0;
        end
    end
endmodule


// 64-QAM soft demapper (Gray, A = 158; levels A*{+-1,+-3,+-5,+-7}), per axis y (max-log):
//   b_msb: y,  b_mid: 4A - |y|,  b_lsb: 2A - ||y| - 4A|      (> 0 means bit 1, see qam64_map)
// LLR = -sign * min(|L| >> SHIFT, 2^(LW-1)-1), two's complement, > 0 means bit 0 (ldpc_ref.demap).
// Out: 6 LLRs per symbol, I bits {0,1,2} then Q bits {3,4,5}; tlast on the last one of a frame.
module qam64_softdemap #(
    parameter int SHIFT = 3,
    parameter int LW    = 7
)(
    input  logic          clk,
    input  logic          rst_n,
    input  logic [23:0]   s_tdata,
    input  logic          s_tlast,
    input  logic          s_tvalid,
    output logic          s_tready,
    output logic [LW-1:0] m_tdata,
    output logic          m_tlast,
    output logic          m_tvalid,
    input  logic          m_tready
);
    localparam int A = 158;
    localparam logic [14:0] MAXM = 2 ** (LW - 1) - 1;
    function automatic logic [LW-1:0] llr(input logic signed [14:0] L);
        logic [14:0] a;
        a = L[14] ? 15'(-L) : 15'(L);
        a = a >> SHIFT;
        if (a > MAXM) a = MAXM;
        return (L > 0) ? LW'(-$signed({1'b0, a})) : LW'(a);
    endfunction
    function automatic logic [3*LW-1:0] soft3(input logic signed [11:0] y);    // {msb, mid, lsb}
        logic signed [14:0] ys, ay, l1, l2, t;
        ys = y; ay = (ys < 0) ? -ys : ys;
        l1 = 4 * A - ay;
        t  = ay - 4 * A; l2 = 2 * A - ((t < 0) ? -t : t);
        return {llr(ys), llr(l1), llr(l2)};
    endfunction
    logic [LW-1:0] sv [0:5];
    logic       hold, hlast;
    logic [2:0] k;
    assign m_tvalid = hold;
    assign m_tdata  = sv[k];
    assign m_tlast  = hlast & (k == 3'd5);
    assign s_tready = ~hold | (m_tready & (k == 3'd5));
    logic [3*LW-1:0] si, sq;
    assign si = soft3(s_tdata[11:0]);
    assign sq = soft3(s_tdata[23:12]);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin hold <= 1'b0; hlast <= 1'b0; k <= '0; end
        else begin
            if (hold & m_tready) begin
                if (k == 3'd5) begin hold <= 1'b0; k <= '0; end
                else k <= k + 1'b1;
            end
            if (s_tvalid & s_tready) begin
                hold <= 1'b1; k <= '0; hlast <= s_tlast;
                {sv[0], sv[1], sv[2]} <= si;
                {sv[3], sv[4], sv[5]} <= sq;
            end
        end
    end
endmodule


// decoder input: exactly N LLRs per frame (zeros = erasures pad a frame that ended early); the rest of the frame
// (the PAD tail bits, or extra LLRs) is dropped up to tlast
module ldpc_feed #(
    parameter int N = 101 * 1944
)(
    input  logic       clk,
    input  logic       rst_n,
    input  logic [6:0] s_tdata,
    input  logic       s_tlast,
    input  logic       s_tvalid,
    output logic       s_tready,
    output logic [6:0] m_tdata,
    output logic       m_tvalid,
    input  logic       m_tready
);
    typedef enum logic [1:0] {PASS, PAD, DROP} st_t;
    st_t st;
    logic [$clog2(N)-1:0] cnt;
    always_comb begin
        case (st)
            PASS:    begin m_tdata = s_tdata; m_tvalid = s_tvalid; s_tready = m_tready; end
            PAD:     begin m_tdata = '0;      m_tvalid = 1'b1;     s_tready = 1'b0;     end
            default: begin m_tdata = '0;      m_tvalid = 1'b0;     s_tready = 1'b1;     end
        endcase
    end
    wire fire = m_tvalid & m_tready;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin st <= PASS; cnt <= '0; end
        else case (st)
            PASS: if (fire) begin
                if (cnt == N - 1) begin cnt <= '0; st <= s_tlast ? PASS : DROP; end
                else begin cnt <= cnt + 1'b1; if (s_tlast) st <= PAD; end
            end
            PAD: if (fire) begin
                if (cnt == N - 1) begin cnt <= '0; st <= PASS; end
                else cnt <= cnt + 1'b1;
            end
            default: if (s_tvalid & s_tlast) st <= PASS;
        endcase
    end
endmodule


// decoded bits -> descramble -> bytes (MSB first) -> 32-bit words (byte 0 in [7:0]) -> packet RAM (2 buffers).
// FRAME_OUT decoder outputs per frame; the first 8*PAY_BYTES are the packet, the rest is tail.
module sscc_rx_bits #(
    parameter int PAY_BYTES = 16360,
    parameter int FRAME_OUT = 101 * 1296,
    parameter logic [6:0] SEED = 7'b1011101
)(
    input  logic        clk,
    input  logic        rst_n,
    input  logic        s_bit,
    input  logic        s_valid,
    output logic [31:0] seq,
    output logic        last_buf,
    input  logic [12:0] rd_addr,
    output logic [31:0] rd_q
);
    localparam int PAY_BITS = PAY_BYTES * 8;
    localparam int WORDS    = PAY_BYTES / 4;              // per buffer
    (* ram_style = "block" *) logic [31:0] ram [0:2*WORDS-1];
    logic [$clog2(FRAME_OUT)-1:0] obit;
    logic [6:0]  st;
    logic [31:0] word;
    logic [4:0]  wbit;                                    // bit within the word: byte (wbit[4:3]), bit 7..0
    logic [$clog2(WORDS)-1:0] widx;
    logic        wb;
    wire  fb  = st[6] ^ st[3];
    wire  pay = obit < PAY_BITS;
    wire  b   = s_bit ^ fb;
    logic        we;
    logic [31:0] wdata;
    logic [$clog2(2*WORDS)-1:0] waddr;
    always_ff @(posedge clk) begin
        if (we) ram[waddr] <= wdata;
        rd_q <= ram[(rd_addr[12] ? WORDS : 0) + rd_addr[11:0]];
    end
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            obit <= '0; st <= SEED; word <= '0; wbit <= '0; widx <= '0; wb <= 1'b0;
            seq <= '0; last_buf <= 1'b0; we <= 1'b0;
        end else begin
            we <= 1'b0;
            if (s_valid) begin
                if (pay) begin
                    st <= {st[5:0], fb};
                    // byte n of the word lives in bits [8n+7 : 8n], its MSB arrives first
                    word[{wbit[4:3], 3'd7 - wbit[2:0]}] <= b;
                    wbit <= wbit + 1'b1;
                    if (wbit == 5'd31) begin
                        we <= 1'b1; waddr <= (wb ? WORDS : 0) + widx;
                        wdata <= word; wdata[{2'd3, 3'd0}] <= b;    // the last bit (byte 3, bit 0) of this word
                        widx <= widx + 1'b1;
                    end
                end
                // commit with the packet's last bit (counting continues to FRAME_OUT for the frame alignment)
                if (obit == PAY_BITS - 1) begin last_buf <= wb; wb <= ~wb; seq <= seq + 1'b1; end
                if (obit == FRAME_OUT - 1) begin
                    obit <= '0; st <= SEED; wbit <= '0; widx <= '0;
                end else obit <= obit + 1'b1;
            end
        end
    end
endmodule
