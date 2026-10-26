`timescale 1ns / 1ps
////////////////////////////////////////////////////////////////////////////////
// jscc_tx_top: DeepJSCC encoder -> OFDM TX PHY -> AD9361 (one FPGA).
//
//   s_img_*  uint8 NHWC RGB element stream, 250 MHz
//     -> blk_enc_0_latent_idx     250 MHz, 32768 x 24-bit {Q[11:0], I[11:0]} symbols per frame
//     -> axis_async_fifo          250 -> 100 MHz, carries {tlast, tdata}
//     -> tx_baseband_jscc         100 MHz: PN sign scramble, pilots, IFFT/CP, preamble, frame buffer
//     -> 20 MSPS tick gate        -> AD9361 DAC
//
// PS build (OFDM_JSCC_PS_TX, PYNQ-ZU): the image comes from the PS (img_dma MM2S -> axis_unpack8 -> CDC ->
//   encoder); a frame-aligned mux selects the encoder or a PRBS 64-QAM self-test source (register CTRL[0]);
//   status / counters in tx_ps_regs (AXI4-Lite at 0x8002_0000). AXI side on clk_100M; 250 MHz domain unchanged.
//   SSCC baseline (CTRL[2] = 1): the PS bytes (JPEG packets, 16360 bytes per frame) go to sscc_tx (scrambler,
//   802.11n LDPC rate 2/3, 64-QAM, 100 MHz) instead of the encoder, and a second frame-aligned mux
//   at 100 MHz hands its symbols to the same OFDM baseband (same frame, same constellation, same power).
////////////////////////////////////////////////////////////////////////////////
module jscc_tx_ps_top #(
    parameter int NSYM       = 683,        // OFDM data symbols per frame (must match the RX)
    parameter int FRAME_SYMS = 32768,      // DeepJSCC symbols per frame (zero padded to NSYM*48)
    parameter int GAP_CYCLES = 5000,       // idle clocks between frames @100 MHz (50 us)
    parameter int TICK_DIV   = 4,          // tick every TICK_DIV+1 cycles -> 100 MHz/5 = 20 MSPS
    parameter int LEAD_N     = 8           // samples pushed back-to-back at the start of a frame
)(

    input  wire        sysclk_p,
    input  wire        sysclk_n,

    output wire        spi_clk,
    output wire        spi_csn,
    output wire        spi_mosi,
    input  wire        spi_miso,

    input  wire        rx_clk_in_p,
    input  wire        rx_clk_in_n,
    input  wire [5:0]  rx_data_in_n,
    input  wire [5:0]  rx_data_in_p,
    input  wire        rx_frame_in_n,
    input  wire        rx_frame_in_p,
    output wire        tx_clk_out_n,
    output wire        tx_clk_out_p,
    output wire [5:0]  tx_data_out_n,
    output wire [5:0]  tx_data_out_p,
    output wire        tx_frame_out_n,
    output wire        tx_frame_out_p,
    output wire        en_agc,
    output wire        enable,
    output wire        txnrx,
    output wire        resetb,
    output wire        sync_in,
    output wire [3:0]  ctrl_in,
    input  wire [7:0]  ctrl_out,
    output wire        led,
    input  wire        sw_tx_swap,   // DIP switch SW0: TX LVDS pair polarity of the daughter card (ad9361_lvds_mode)


    input  wire        rst_n
);
    localparam [15:0] FRAME_LEN = 16'd320 + 16'd80*NSYM;   // 54960 samples
    localparam [15:0] GAP_W     = GAP_CYCLES;
    localparam [15:0] LEAD_W    = LEAD_N;
    localparam [2:0]  TICK_LAST = TICK_DIV;

    //------------------------------------------------------------------ clocks / reset

    wire clk250, clk100, clk20, locked;
    clk_gen u_clk (
        .clk_250M   (clk250),
        .clk_100M   (clk100),
        .clk_20M    (clk20),
        .reset      (1'b0),
        .locked     (locked),
        .clk_in1_p  (sysclk_p),
        .clk_in1_n  (sysclk_n)
    );


    // reset released only after the MMCM is locked, then synchronised in each domain
    wire pl_resetn0;                        // PS fabric reset (PYNQ)
    wire clk_reset_n = rst_n & locked & pl_resetn0;
    reg [1:0] rst250_pipe, rst100_pipe;
    always @(posedge clk250 or negedge clk_reset_n)
        if (!clk_reset_n) rst250_pipe <= 2'b00;
        else              rst250_pipe <= {rst250_pipe[0], 1'b1};
    always @(posedge clk100 or negedge clk_reset_n)
        if (!clk_reset_n) rst100_pipe <= 2'b00;
        else              rst100_pipe <= {rst100_pipe[0], 1'b1};
    wire rst250_n = rst250_pipe[1];
    wire rst100_n = rst100_pipe[1];
    // xpm_fifo_axis has a single asynchronous reset: release it only after BOTH endpoint
    // domains have finished their local reset synchronisation
    wire fifo_rst_n = rst250_n & rst100_n;

    //------------------------------------------------------------------ DAC interface
    wire [11:0] tx_data_i, tx_data_q;
    wire        tx_valid, tx_ready;

    wire [11:0] rx_data_i, rx_data_q;
    wire        rx_valid, rx_ready;


    //------------------------------------------------------------------ encoder (250 MHz)
    wire [23:0] enc_tdata;
    wire        enc_tvalid, enc_tready, enc_tlast;
    wire [0:0]  enc_tuser;
    // image from the PS (img_dma MM2S, 100 MHz) -> bytes -> CDC -> encoder (250 MHz)
    wire [31:0] dma_tdata;
    wire        dma_tvalid, dma_tready;
    wire [7:0]  ub_tdata;
    wire        ub_tvalid, ub_tready;
    axis_unpack8 u_unpack (
        .clk      (clk100),
        .rst_n    (rst100_n),
        .s_tdata  (dma_tdata), .s_tvalid (dma_tvalid), .s_tready (dma_tready),
        .m_tdata  (ub_tdata),  .m_tvalid (ub_tvalid),  .m_tready (ub_tready)
    );
    wire [7:0]  s_img_tdata;
    wire        s_img_tvalid, s_img_tready;
    // byte demux: SSCC packets or encoder images (the PS switches only between packets / images)
    wire        sscc_sel;
    wire        ui_tready, us_tready;
    assign ub_tready = sscc_sel ? us_tready : ui_tready;
    axis_async_fifo #(.DATA_W(8), .USER_W(1), .DEPTH(32), .RAM_STYLE("distributed")) u_img_cdc (
        .rst_n   (fifo_rst_n),
        .s_clk   (clk100),
        .s_tdata (ub_tdata), .s_tlast (1'b0), .s_tuser (1'b0),
        .s_tvalid(ub_tvalid & ~sscc_sel), .s_tready(ui_tready),
        .m_clk   (clk250),
        .m_tdata (s_img_tdata), .m_tlast (), .m_tuser (),
        .m_tvalid(s_img_tvalid), .m_tready(s_img_tready)
    );

    wire [23:0] e_tdata;
    wire        e_tvalid, e_tready, e_tlast;
    wire [0:0]  e_tuser;
    blk_enc_0_latent_idx u_encoder (
        .clk             (clk250),
        .rst_n           (rst250_n),
        .s_in_tdata      ({4'b0, s_img_tdata}),  // uint8 pixel -> int12 activation (input scale 1/255)
        .s_in_tvalid     (s_img_tvalid),
        .s_in_tready     (s_img_tready),
        .s_in_tlast      (1'b0),                 // unused: the encoder tags its own NHWC side band
        .s_in_tuser      (1'b0),
        .m_out_tdata     (e_tdata),
        .m_out_tvalid    (e_tvalid),
        .m_out_tready    (e_tready),
        .m_out_tlast     (e_tlast),
        .m_out_tuser     (e_tuser)
    );
    // PRBS 64-QAM self test source (same contract as the encoder output)
    wire [23:0] p_tdata;
    wire        p_tvalid, p_tready, p_tlast, p_tuser;
    iq_prbs_source #(.FRAME_SYMS(FRAME_SYMS)) u_prbs (
        .clk      (clk250),
        .rst_n    (rst250_n),
        .m_tdata  (p_tdata),
        .m_tlast  (p_tlast),
        .m_tuser  (p_tuser),
        .m_tvalid (p_tvalid),
        .m_tready (p_tready)
    );
    wire src_sel, src_active;
    frame_src_mux #(.W(24)) u_src_mux (
        .clk        (clk250),
        .rst_n      (rst250_n),
        .sel_async  (src_sel),
        .s0_tdata   (e_tdata), .s0_tlast (e_tlast), .s0_tuser (e_tuser[0]), .s0_tvalid (e_tvalid), .s0_tready (e_tready),
        .s1_tdata   (p_tdata), .s1_tlast (p_tlast), .s1_tuser (p_tuser),    .s1_tvalid (p_tvalid), .s1_tready (p_tready),
        .m_tdata    (enc_tdata), .m_tlast (enc_tlast), .m_tuser (enc_tuser[0]), .m_tvalid (enc_tvalid), .m_tready (enc_tready),
        .sel_active (src_active)
    );

    //------------------------------------------------------------------ 250 -> 100 MHz CDC
    wire [23:0] cdc_tdata;
    wire        cdc_tvalid, cdc_tready, cdc_tlast;
    wire [0:0]  cdc_tuser;
    axis_async_fifo #(.DATA_W(24), .USER_W(1), .DEPTH(32), .RAM_STYLE("distributed")) u_iq_cdc (
        .rst_n   (fifo_rst_n),
        .s_clk   (clk250),
        .s_tdata (enc_tdata), .s_tlast (enc_tlast), .s_tuser (enc_tuser),
        .s_tvalid(enc_tvalid), .s_tready(enc_tready),
        .m_clk   (clk100),
        .m_tdata (cdc_tdata), .m_tlast (cdc_tlast), .m_tuser (cdc_tuser),
        .m_tvalid(cdc_tvalid), .m_tready(cdc_tready)
    );

    //------------------------------------------------------------------ SSCC baseline (100 MHz)
    wire [23:0] sx_tdata;
    wire        sx_tvalid, sx_tready, sx_tlast;
    sscc_tx #(.PAY_BYTES(16360), .FRAME_SYMS(FRAME_SYMS)) u_sscc (
        .clk      (clk100),
        .rst_n    (rst100_n),
        .s_tdata  (ub_tdata), .s_tvalid (ub_tvalid & sscc_sel), .s_tready (us_tready),
        .m_tdata  (sx_tdata), .m_tlast  (sx_tlast), .m_tvalid (sx_tvalid), .m_tready (sx_tready)
    );
    wire [23:0] bbi_tdata;
    wire        bbi_tvalid, bbi_tready, bbi_tlast, bbi_tuser, sscc_active;
    frame_src_mux #(.W(24)) u_sscc_mux (
        .clk        (clk100),
        .rst_n      (rst100_n),
        .sel_async  (sscc_sel),
        .s0_tdata   (cdc_tdata), .s0_tlast (cdc_tlast), .s0_tuser (cdc_tuser[0]), .s0_tvalid (cdc_tvalid), .s0_tready (cdc_tready),
        .s1_tdata   (sx_tdata),  .s1_tlast (sx_tlast),  .s1_tuser (sx_tlast),     .s1_tvalid (sx_tvalid),  .s1_tready (sx_tready),
        .m_tdata    (bbi_tdata), .m_tlast (bbi_tlast), .m_tuser (bbi_tuser), .m_tvalid (bbi_tvalid), .m_tready (bbi_tready),
        .sel_active (sscc_active)
    );

    //------------------------------------------------------------------ OFDM baseband (100 MHz)
    wire        tx_tlast_err;
    wire [31:0] tx_frames_buffered;
    wire [23:0] bb_tdata;
    wire        bb_tvalid, bb_tlast, bb_tready;
    tx_baseband_jscc #(.NSYM(NSYM), .FRAME_SYMS(FRAME_SYMS)) u_baseband (
        .clk             (clk100),
        .rst_n           (rst100_n),
        .s_in_tdata      (bbi_tdata),
        .s_in_tlast      (bbi_tlast),
        .s_in_tvalid     (bbi_tvalid),
        .s_in_tready     (bbi_tready),
        .m_out_tdata     (bb_tdata),
        .m_out_tvalid    (bb_tvalid),
        .m_out_tlast     (bb_tlast),
        .m_out_tready    (bb_tready),
        .tlast_err       (tx_tlast_err),
        .frames_buffered (tx_frames_buffered)
    );

    //------------------------------------------------------------------ 20 MSPS tick gate
    // The frame buffer only releases complete frames, so back-pressure never drops a sample.
    reg  [1:0]  src_st;                    // 0 = GAP, 1 = SEND_WAIT, 2 = SEND
    reg  [15:0] src_cnt, gap_cnt;
    reg  [2:0]  tick_cnt;

    wire tick      = (tick_cnt == TICK_LAST);
    wire wr_accept = tx_valid & tx_ready;
    wire tx_offer  = (src_st == 2'd2) && ((src_cnt < LEAD_W) || tick);

    assign bb_tready = tx_offer & tx_ready;
    assign tx_valid  = bb_tvalid & tx_offer;
    assign tx_data_i = bb_tdata[11:0];
    assign tx_data_q = bb_tdata[23:12];

    always @(posedge clk100 or negedge rst100_n) begin
        if (!rst100_n)           tick_cnt <= 3'd0;
        else if (src_st != 2'd2) tick_cnt <= 3'd0;
        else if (tick)           tick_cnt <= 3'd0;
        else                     tick_cnt <= tick_cnt + 1'b1;
    end

    always @(posedge clk100 or negedge rst100_n) begin
        if (!rst100_n) begin
            src_st  <= 2'd0;
            src_cnt <= 16'd0;
            gap_cnt <= 16'd0;
        end else begin
            case (src_st)
            2'd0: begin                                        // GAP: write nothing
                if (gap_cnt == GAP_W - 16'd1) begin
                    gap_cnt <= 16'd0;
                    src_cnt <= 16'd0;
                    src_st  <= 2'd1;
                end else gap_cnt <= gap_cnt + 1'b1;
            end
            2'd1: src_st <= 2'd2;                              // SEND_WAIT: one cycle
            default: if (wr_accept) begin                      // SEND: FRAME_LEN samples
                if (src_cnt == FRAME_LEN - 16'd1) begin
                    src_st  <= 2'd0;
                    src_cnt <= 16'd0;
                    gap_cnt <= 16'd0;
                end else src_cnt <= src_cnt + 1'b1;
            end
            endcase
        end
    end

    //------------------------------------------------------------------ AD9361 (board) / loopback (sim)

    // TX only: keep the RX path drained so the ADC FIFO cannot fill up
    assign rx_ready = 1'b1;

    // AD9361 SPI access from the PS (tx_ps_regs SPI_CMD / SPI_STAT), e.g. TX attenuation for the demo
    wire [9:0] spi_addr; wire [7:0] spi_wdata, spi_rdata, spi_cnt; wire spi_wr_tgl, spi_rd_tgl, spi_rf_ready;
    ad9361_top u_rf (
        .sys_clk        (clk100),
        .clk_20M        (clk20),
        .rst_n          (rst100_n),   // reset after the MMCM is locked
        .tx_swap        (sw_tx_swap),
        .led            (led),
        .rx_data_i      (rx_data_i),
        .rx_data_q      (rx_data_q),
        .rx_valid       (rx_valid),
        .rx_ready       (rx_ready),
        .tx_data_i      (tx_data_i),
        .tx_data_q      (tx_data_q),
        .tx_valid       (tx_valid),
        .tx_ready       (tx_ready),
        .spi_clk        (spi_clk),
        .spi_csn        (spi_csn),
        .spi_mosi       (spi_mosi),
        .spi_miso       (spi_miso),
        .rx_clk_in_p    (rx_clk_in_p),
        .rx_clk_in_n    (rx_clk_in_n),
        .rx_data_in_n   (rx_data_in_n),
        .rx_data_in_p   (rx_data_in_p),
        .rx_frame_in_n  (rx_frame_in_n),
        .rx_frame_in_p  (rx_frame_in_p),
        .tx_clk_out_n   (tx_clk_out_n),
        .tx_clk_out_p   (tx_clk_out_p),
        .tx_data_out_n  (tx_data_out_n),
        .tx_data_out_p  (tx_data_out_p),
        .tx_frame_out_n (tx_frame_out_n),
        .tx_frame_out_p (tx_frame_out_p),
        .en_agc         (en_agc),
        .enable         (enable),
        .txnrx          (txnrx),
        .resetb         (resetb),
        .sync_in        (sync_in),
        .ctrl_in        (ctrl_in),
        .ctrl_out       (ctrl_out),
        .ps_spi_addr    (spi_addr),
        .ps_spi_wdata   (spi_wdata),
        .ps_spi_wr_tgl  (spi_wr_tgl),
        .ps_spi_rd_tgl  (spi_rd_tgl),
        .ps_spi_rdata   (spi_rdata),
        .ps_spi_cnt     (spi_cnt),
        .ps_rf_ready    (spi_rf_ready)
    );


    //------------------------------------------------------------------ PS: image DMA + registers
    wire [31:0] reg_awaddr, reg_wdata, reg_araddr, reg_rdata;
    wire [1:0]  reg_bresp, reg_rresp;
    wire        reg_awvalid, reg_awready, reg_wvalid, reg_wready, reg_bvalid, reg_bready;
    wire        reg_arvalid, reg_arready, reg_rvalid, reg_rready;
    ps_tx_wrapper u_ps (
        .aclk              (clk100),
        .aresetn           (rst100_n),
        .pl_resetn0        (pl_resetn0),
        .M_AXIS_IMG_tdata  (dma_tdata),
        .M_AXIS_IMG_tkeep  (),
        .M_AXIS_IMG_tlast  (),
        .M_AXIS_IMG_tvalid (dma_tvalid),
        .M_AXIS_IMG_tready (dma_tready),
        .M_AXI_REG_awaddr  (reg_awaddr),  .M_AXI_REG_awprot (), .M_AXI_REG_awvalid (reg_awvalid), .M_AXI_REG_awready (reg_awready),
        .M_AXI_REG_wdata   (reg_wdata),   .M_AXI_REG_wstrb  (), .M_AXI_REG_wvalid  (reg_wvalid),  .M_AXI_REG_wready  (reg_wready),
        .M_AXI_REG_bresp   (reg_bresp),   .M_AXI_REG_bvalid (reg_bvalid), .M_AXI_REG_bready (reg_bready),
        .M_AXI_REG_araddr  (reg_araddr),  .M_AXI_REG_arprot (), .M_AXI_REG_arvalid (reg_arvalid), .M_AXI_REG_arready (reg_arready),
        .M_AXI_REG_rdata   (reg_rdata),   .M_AXI_REG_rresp  (reg_rresp), .M_AXI_REG_rvalid (reg_rvalid), .M_AXI_REG_rready (reg_rready)
    );
    wire cdc_fire = bbi_tvalid & bbi_tready;           // symbols into the OFDM baseband (JSCC, PRBS or SSCC)
    reg  tx_frame_ev;                                  // a frame finished being written to the DAC side
    always @(posedge clk100 or negedge rst100_n)
        if (!rst100_n) tx_frame_ev <= 1'b0;
        else tx_frame_ev <= (src_st == 2'd2) && wr_accept && (src_cnt == FRAME_LEN - 16'd1);
    tx_ps_regs u_regs (
        .clk           (clk100),
        .rst_n         (rst100_n),
        .s_axi_awaddr  (reg_awaddr[7:0]), .s_axi_awvalid (reg_awvalid), .s_axi_awready (reg_awready),
        .s_axi_wdata   (reg_wdata),       .s_axi_wvalid  (reg_wvalid),  .s_axi_wready  (reg_wready),
        .s_axi_bresp   (reg_bresp),       .s_axi_bvalid  (reg_bvalid),  .s_axi_bready  (reg_bready),
        .s_axi_araddr  (reg_araddr[7:0]), .s_axi_arvalid (reg_arvalid), .s_axi_arready (reg_arready),
        .s_axi_rdata   (reg_rdata),       .s_axi_rresp   (reg_rresp),   .s_axi_rvalid  (reg_rvalid), .s_axi_rready (reg_rready),
        .src_sel       (src_sel),
        .src_active    (src_active),
        .ev_img_byte   (ub_tvalid & ub_tready),
        .ev_sym        (cdc_fire),
        .ev_sym_frame  (cdc_fire & bbi_tlast),
        .ev_tx_frame   (tx_frame_ev),
        .ev_tlast_err  (tx_tlast_err),
        .fb_frames     (tx_frames_buffered),
        .locked        (locked),
        .tx_swap       (sw_tx_swap),
        .spi_addr      (spi_addr),
        .spi_wdata     (spi_wdata),
        .spi_wr_tgl    (spi_wr_tgl),
        .spi_rd_tgl    (spi_rd_tgl),
        .spi_rdata     (spi_rdata),
        .spi_cnt       (spi_cnt),
        .spi_rf_ready  (spi_rf_ready),
        .sscc_sel      (sscc_sel),
        .sscc_active   (sscc_active),
        .ev_sscc_frame (sx_tvalid & sx_tready & sx_tlast)
    );

endmodule
