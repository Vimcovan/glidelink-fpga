`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// SSCC baseline TX (separate source / channel coding), the classic counterpart of the DeepJSCC encoder:
//   PS bytes (JPEG packet, PAY_BYTES per frame)
//     -> sscc_tx_bits     MSB first, 802.11 scrambler (x^7 + x^4 + 1, restarted every frame), then
//                         FRAME_BITS - 8*PAY_BYTES zero tail bits
//     -> ldpc_enc         802.11n LDPC n = 1944, rate 2/3: CW = 101 codewords, then 264 zero pad bits (ldpc_enc.sv)
//     -> qam64_map        Gray 64-QAM, levels 158 * {+-1, +-3, +-5, +-7} = the JSCC / PRBS constellation
//   = FRAME_SYMS 24-bit {Q[11:0], I[11:0]} symbols per frame with tlast, the same contract as the encoder output.
//   FRAME_BITS = 101 * 1296 = 130896 information bits; 101 * 1944 + 264 = 196608 coded bits = 32768 symbols.
//////////////////////////////////////////////////////////////////////////////////
module sscc_tx #(
    parameter int PAY_BYTES  = 16360,
    parameter int FRAME_SYMS = 32768,
    parameter int CW         = 101
)(
    input  logic        clk,
    input  logic        rst_n,
    input  logic [7:0]  s_tdata,
    input  logic        s_tvalid,
    output logic        s_tready,
    output logic [23:0] m_tdata,
    output logic        m_tlast,
    output logic        m_tvalid,
    input  logic        m_tready
);
    localparam int FRAME_BITS = CW * 1296;
    logic b_tdata, b_tlast, b_tvalid, b_tready;
    sscc_tx_bits #(.PAY_BYTES(PAY_BYTES), .FRAME_BITS(FRAME_BITS)) u_bits (
        .clk, .rst_n, .s_tdata, .s_tvalid, .s_tready,
        .m_tdata(b_tdata), .m_tlast(b_tlast), .m_tvalid(b_tvalid), .m_tready(b_tready));
    logic [5:0] c_tdata; logic c_tlast, c_tvalid, c_tready;
    ldpc_enc #(.CW(CW), .PAD(FRAME_SYMS * 6 - CW * 1944)) u_ldpc (
        .clk, .rst_n, .s_tdata(b_tdata), .s_tlast(b_tlast), .s_tvalid(b_tvalid), .s_tready(b_tready),
        .m_tdata(c_tdata), .m_tlast(c_tlast), .m_tvalid(c_tvalid), .m_tready(c_tready));
    qam64_map u_map (
        .clk, .rst_n, .s_tdata(c_tdata), .s_tlast(c_tlast), .s_tvalid(c_tvalid), .s_tready(c_tready),
        .m_tdata, .m_tlast, .m_tvalid, .m_tready);
endmodule


// bytes -> scrambled bits (MSB first) + zero tail; one bit per beat, tlast on the last bit of a frame
module sscc_tx_bits #(
    parameter int PAY_BYTES  = 16360,
    parameter int FRAME_BITS = 101 * 1296,
    parameter logic [6:0] SEED = 7'b1011101          // same seed as the original OFDM scrambler
)(
    input  logic       clk,
    input  logic       rst_n,
    input  logic [7:0] s_tdata,
    input  logic       s_tvalid,
    output logic       s_tready,
    output logic       m_tdata,
    output logic       m_tlast,
    output logic       m_tvalid,
    input  logic       m_tready
);
    localparam int PAY_BITS = PAY_BYTES * 8;
    logic [$clog2(FRAME_BITS)-1:0] bcnt;
    logic [2:0] bidx;                                  // bit of the current byte, 7 first
    logic [6:0] st;
    wire in_pay = (bcnt < PAY_BITS);
    wire fb     = st[6] ^ st[3];
    assign m_tvalid = in_pay ? s_tvalid : 1'b1;
    assign m_tdata  = in_pay & (s_tdata[bidx] ^ fb);
    assign m_tlast  = (bcnt == FRAME_BITS - 1);
    assign s_tready = in_pay & m_tready & (bidx == 3'd0);  // a byte is consumed with its last bit
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin bcnt <= '0; bidx <= 3'd7; st <= SEED; end
        else if (m_tvalid & m_tready) begin
            if (m_tlast) begin bcnt <= '0; bidx <= 3'd7; st <= SEED; end
            else begin
                bcnt <= bcnt + 1'b1;
                if (in_pay) begin st <= {st[5:0], fb}; bidx <= bidx - 1'b1; end
            end
        end
    end
endmodule


// Gray 64-QAM: I from bits {0,1,2}, Q from bits {3,4,5} (bit 0 first = MSB of the Gray label),
// level 158 * (2k - 7), k = binary of the Gray label (same mapping as iq_prbs_source)
module qam64_map (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [5:0]  s_tdata,
    input  logic        s_tlast,
    input  logic        s_tvalid,
    output logic        s_tready,
    output logic [23:0] m_tdata,
    output logic        m_tlast,
    output logic        m_tvalid,
    input  logic        m_tready
);
    function automatic logic [11:0] lvl(input logic [2:0] g);
        logic [2:0] k;
        k[2] = g[2]; k[1] = g[2] ^ g[1]; k[0] = g[2] ^ g[1] ^ g[0];
        return 12'($signed({1'b0, k, 1'b0}) - 7) * 12'sd158;
    endfunction
    assign s_tready = ~m_tvalid | m_tready;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin m_tvalid <= 1'b0; m_tdata <= '0; m_tlast <= 1'b0; end
        else if (s_tvalid & s_tready) begin
            m_tdata  <= {lvl({s_tdata[3], s_tdata[4], s_tdata[5]}), lvl({s_tdata[0], s_tdata[1], s_tdata[2]})};
            m_tlast  <= s_tlast; m_tvalid <= 1'b1;
        end
        else if (m_tready) m_tvalid <= 1'b0;
    end
endmodule
