`timescale 1ns / 1ps
////////////////////////////////////////////////////////////////////////////////
// jscc_rx_top: AD9361 -> OFDM RX PHY -> DeepJSCC decoder -> image sink (one FPGA).
//
//   ADC 20 MSPS
//     -> rx_baseband_top          100 MHz: frame detect/CFO, LTF timing, channel est., SFO, CPE,
//                                 PN descramble, 32768 x 24-bit {Q[11:0], I[11:0]} per frame
//     -> uram_frame_fifo          100 MHz, complete-frame buffer (2.25 frames, 6 URAM)
//     -> axis_async_fifo          100 -> 250 MHz, carries {tuser, tlast, tdata}
//     -> blk_rx_in_output         250 MHz, 256x256x3 uint8 RGB out (tlast per pixel, tuser per frame)
//
//   The frame buffer is required: the PHY bursts one frame in 2.748 ms while the decoder needs
//   ~33 ms per frame.  It never fills at link rate, so the drop path is left unconnected.
//
// PS build (OFDM_JSCC_PS_RX, PYNQ-ZU): block design ps_rx (Zynq US+ PS, 2 x AXI DMA S2MM) gets
//   * the decoded image (img_pack32 -> CDC -> img_dma), one shot or continuous on request
//   * raw ADC IQ (adc_capture -> cap_dma), immediate or at the next PHY frame start, 2048 samples pre-trigger
//   * telemetry snapshots of the RX equalizer stages (telemetry.sv, AXI4 slave at 0x8004_0000)
//   * control / status registers (rx_ps_regs.sv, AXI4-Lite at 0x8002_0000)
//   * SSCC baseline packets: sscc_rx (64-QAM soft demap, LDPC decoder, descrambler) decodes every PHY frame
//     as an SSCC frame too; the PS reads the packets through the telemetry slave (0x5000 / 0x6000) and keeps the
//     ones of the mode the TX is in (CRC)
//   AXI side on clk_100M of the PL MMCM; the 250 MHz network domain is unchanged.
////////////////////////////////////////////////////////////////////////////////
module jscc_rx_ps_top #(
    parameter int NSYM       = 683,        // OFDM data symbols per frame (must match the TX)
    parameter int FRAME_SYMS = 32768,      // DeepJSCC symbols per frame
    parameter int TXQ_EN     = 0           // TX quadrature calibration search: off (TX unused)
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


    input  wire        rst_n
);
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

    //------------------------------------------------------------------ ADC interface
    wire [11:0] rx_data_i, rx_data_q;
    wire        rx_valid, rx_ready;

    wire [11:0] tx_data_i, tx_data_q;
    wire        tx_valid, tx_ready;


    //------------------------------------------------------------------ OFDM RX PHY (100 MHz)
    wire [23:0] phy_tdata;
    wire        phy_tvalid, phy_tready, phy_tlast, phy_tuser;
    wire rx_soft_rst;
    wire rxb_rst_n = rst100_n & ~rx_soft_rst;    // RX soft reset (register) for the PHY + frame buffer
    wire [23:0] dbg_pre_data, dbg_ce_data, dbg_cpe_data, dbg_sfo_data;
    wire        dbg_pre_fire, dbg_ce_fire, dbg_cpe_fire, dbg_sfo_fire;
    rx_baseband_top #(.NSYM(NSYM), .FRAME_SYMS(FRAME_SYMS)) u_rx_baseband (
        .clk             (clk100),
        .rst_n           (rxb_rst_n),
        .s_in_tdata      ({rx_data_q, rx_data_i}),
        .s_in_tvalid     (rx_valid),
        .s_in_tready     (rx_ready),
        .m_out_tdata     (phy_tdata),
        .m_out_tlast     (phy_tlast),
        .m_out_tuser     (phy_tuser),
        .m_out_tvalid    (phy_tvalid),
        .m_out_tready    (phy_tready),
        // debug taps -> telemetry
        .dbg_pre_data    (dbg_pre_data),
        .dbg_pre_fire    (dbg_pre_fire),
        .dbg_ce_data     (dbg_ce_data),
        .dbg_ce_fire     (dbg_ce_fire),
        .dbg_cpe_data    (dbg_cpe_data),
        .dbg_cpe_fire    (dbg_cpe_fire),
        .dbg_sfo_data    (dbg_sfo_data),
        .dbg_sfo_fire    (dbg_sfo_fire)
    );

    //------------------------------------------------------------------ frame buffer (100 MHz)
    // Complete frames only, 10923 words/frame, 6 URAM = 24576 words = 2.25 frames.
    // PACKET_MODE=0 never back-pressures the PHY; at link rate it never fills, so the
    // drop counter is simply not connected.
    wire [31:0] fb_frames_in, fb_dropped;
    wire [23:0] fb_tdata;
    wire        fb_tvalid, fb_tready, fb_tlast, fb_tuser;
    uram_frame_fifo #(.FRAME_LEN(FRAME_SYMS), .DEPTH_WORDS(24576), .PACKET_MODE(0)) u_rx_frame_buf (
        .clk            (clk100),
        .rst_n          (rxb_rst_n),
        .s_tdata        (phy_tdata),
        .s_tvalid       (phy_tvalid),
        .s_tready       (phy_tready),
        .m_tdata        (fb_tdata),
        .m_tlast        (fb_tlast),
        .m_tuser        (fb_tuser),
        .m_tvalid       (fb_tvalid),
        .m_tready       (fb_tready),
        .frames_in      (fb_frames_in),
        .dropped_frames (fb_dropped)
    );

    //------------------------------------------------------------------ 100 -> 250 MHz CDC
    wire [23:0] dec_tdata;
    wire        dec_tvalid, dec_tready, dec_tlast;
    wire [0:0]  dec_tuser;
    axis_async_fifo #(.DATA_W(24), .USER_W(1), .DEPTH(32), .RAM_STYLE("distributed")) u_dec_cdc (
        .rst_n   (fifo_rst_n),
        .s_clk   (clk100),
        .s_tdata (fb_tdata), .s_tlast (fb_tlast), .s_tuser (fb_tuser),
        .s_tvalid(fb_tvalid), .s_tready(fb_tready),
        .m_clk   (clk250),
        .m_tdata (dec_tdata), .m_tlast (dec_tlast), .m_tuser (dec_tuser),
        .m_tvalid(dec_tvalid), .m_tready(dec_tready)
    );

    //------------------------------------------------------------------ decoder (250 MHz)

    wire [7:0]  m_img_tdata;
    wire        m_img_tvalid, m_img_tready, m_img_tlast;
    wire [0:0]  m_img_tuser;
    blk_rx_in_output u_decoder (
        .clk             (clk250),
        .rst_n           (rst250_n),
        .s_in_tdata      (dec_tdata),
        .s_in_tvalid     (dec_tvalid),
        .s_in_tready     (dec_tready),
        .s_in_tlast      (dec_tlast),
        .s_in_tuser      (dec_tuser),
        .m_out_tdata     (m_img_tdata),
        .m_out_tvalid    (m_img_tvalid),
        .m_out_tready    (m_img_tready),
        .m_out_tlast     (m_img_tlast),
        .m_out_tuser     (m_img_tuser)
    );


    //------------------------------------------------------------------ AD9361 (board) / loopback (sim)

    // RX only: the TX path stays idle
    assign tx_data_i = 12'd0;
    assign tx_data_q = 12'd0;
    assign tx_valid  = 1'b0;

    // AD9361 init + SPI from the PS (rx_ps_regs RF_CTRL / SPI_CMD / SPI_STAT)
    wire       rf_ps_mode, rf_resetb, rf_init_done, spi_wr_tgl, spi_rd_tgl, spi_rf_ready;
    wire [9:0] spi_addr; wire [7:0] spi_wdata, spi_rdata, spi_cnt;
    ad9361_top #(.TXQ_EN(TXQ_EN)) u_rf (
        .sys_clk        (clk100),
        .clk_20M        (clk20),
        .rst_n          (rst100_n),   // reset after the MMCM is locked
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
        .ps_mode        (rf_ps_mode),
        .ps_resetb      (rf_resetb),
        .ps_init_done   (rf_init_done),
        .ps_spi_addr    (spi_addr),
        .ps_spi_wdata   (spi_wdata),
        .ps_spi_wr_tgl  (spi_wr_tgl),
        .ps_spi_rd_tgl  (spi_rd_tgl),
        .ps_spi_rdata   (spi_rdata),
        .ps_spi_cnt     (spi_cnt),
        .ps_rf_ready    (spi_rf_ready)
    );


    //------------------------------------------------------------------ PS: image, ADC capture, telemetry, registers
    // decoded image (250 MHz) -> 32-bit words -> CDC -> img_dma
    wire        img_arm_toggle, img_continuous, img_pending, img_frame_toggle, img_sent_toggle;
    wire [31:0] pk_tdata;
    wire        pk_tlast, pk_tvalid, pk_tready;
    img_pack32 u_img_pack (
        .clk          (clk250),
        .rst_n        (rst250_n),
        .s_tdata      (m_img_tdata),
        .s_tuser      (m_img_tuser[0]),
        .s_tvalid     (m_img_tvalid),
        .s_tready     (m_img_tready),
        .m_tdata      (pk_tdata),
        .m_tlast      (pk_tlast),
        .m_tvalid     (pk_tvalid),
        .m_tready     (pk_tready),
        .arm_toggle   (img_arm_toggle),
        .continuous   (img_continuous),
        .frame_toggle (img_frame_toggle),
        .sent_toggle  (img_sent_toggle),
        .pending      (img_pending)
    );
    wire [31:0] img_tdata;
    wire        img_tlast, img_tvalid, img_tready;
    axis_async_fifo #(.DATA_W(32), .USER_W(1), .DEPTH(32), .RAM_STYLE("distributed")) u_img_cdc (
        .rst_n   (fifo_rst_n),
        .s_clk   (clk250),
        .s_tdata (pk_tdata), .s_tlast (pk_tlast), .s_tuser (1'b0),
        .s_tvalid(pk_tvalid), .s_tready(pk_tready),
        .m_clk   (clk100),
        .m_tdata (img_tdata), .m_tlast (img_tlast), .m_tuser (),
        .m_tvalid(img_tvalid), .m_tready(img_tready)
    );

    // PHY frame events (100 MHz)
    wire phy_fire = phy_tvalid & phy_tready;
    reg  phy_first;                                   // the next PHY symbol starts a frame
    always @(posedge clk100 or negedge rxb_rst_n)
        if (!rxb_rst_n) phy_first <= 1'b1;
        else if (phy_fire) phy_first <= phy_tlast;
    wire ev_frame_start = phy_fire & phy_first;
    wire ev_frame_end   = phy_fire & phy_tlast;

    // SSCC baseline decoder on the PHY output (100 MHz, never back-pressures the PHY)
    wire [12:0] sscc_rd_addr;
    wire [31:0] sscc_rd_q, sscc_seq, sscc_ovf, sscc_cw_fail, sscc_cw_iters;
    wire        sscc_last_buf;
    sscc_rx #(.PAY_BYTES(16360), .FRAME_SYMS(FRAME_SYMS)) u_sscc (
        .clk      (clk100),
        .rst_n    (rxb_rst_n),
        .s_tdata  (phy_tdata),
        .s_tlast  (phy_tlast),
        .s_fire   (phy_fire),
        .seq      (sscc_seq),
        .last_buf (sscc_last_buf),
        .fifo_ovf (sscc_ovf),
        .cw_fail  (sscc_cw_fail),
        .cw_iters (sscc_cw_iters),
        .rd_addr  (sscc_rd_addr),
        .rd_q     (sscc_rd_q)
    );

    // raw ADC capture
    wire [31:0] cap_len, cap_ovf, cap_tdata;
    wire [1:0]  cap_trig;
    wire        cap_arm, cap_busy, cap_tlast, cap_tvalid, cap_tready;
    adc_capture #(.PRE(2048)) u_adc_cap (
        .clk         (clk100),
        .rst_n       (rst100_n),
        .adc_data    ({rx_data_q, rx_data_i}),
        .adc_fire    (rx_valid & rx_ready),
        .frame_start (ev_frame_start),
        .arm         (cap_arm),
        .trig_mode   (cap_trig),
        .len         (cap_len),
        .busy        (cap_busy),
        .ovf_cnt     (cap_ovf),
        .m_tdata     (cap_tdata),
        .m_tlast     (cap_tlast),
        .m_tvalid    (cap_tvalid),
        .m_tready    (cap_tready)
    );

    // block design: PS + DMAs
    wire [31:0] reg_awaddr, reg_wdata, reg_araddr, reg_rdata;
    wire [1:0]  reg_bresp, reg_rresp;
    wire        reg_awvalid, reg_awready, reg_wvalid, reg_wready, reg_bvalid, reg_bready;
    wire        reg_arvalid, reg_arready, reg_rvalid, reg_rready;
    wire [31:0] tel_awaddr, tel_wdata, tel_araddr, tel_rdata;
    wire [7:0]  tel_awlen, tel_arlen;
    wire [1:0]  tel_bresp, tel_rresp;
    wire        tel_awvalid, tel_awready, tel_wlast, tel_wvalid, tel_wready, tel_bvalid, tel_bready;
    wire        tel_arvalid, tel_arready, tel_rlast, tel_rvalid, tel_rready;
    ps_rx_wrapper u_ps (
        .aclk              (clk100),
        .aresetn           (rst100_n),
        .pl_resetn0        (pl_resetn0),
        .S_AXIS_IMG_tdata  (img_tdata),
        .S_AXIS_IMG_tkeep  (4'hF),
        .S_AXIS_IMG_tlast  (img_tlast),
        .S_AXIS_IMG_tvalid (img_tvalid),
        .S_AXIS_IMG_tready (img_tready),
        .S_AXIS_CAP_tdata  (cap_tdata),
        .S_AXIS_CAP_tlast  (cap_tlast),
        .S_AXIS_CAP_tvalid (cap_tvalid),
        .S_AXIS_CAP_tready (cap_tready),
        .M_AXI_REG_awaddr  (reg_awaddr),  .M_AXI_REG_awprot (), .M_AXI_REG_awvalid (reg_awvalid), .M_AXI_REG_awready (reg_awready),
        .M_AXI_REG_wdata   (reg_wdata),   .M_AXI_REG_wstrb  (), .M_AXI_REG_wvalid  (reg_wvalid),  .M_AXI_REG_wready  (reg_wready),
        .M_AXI_REG_bresp   (reg_bresp),   .M_AXI_REG_bvalid (reg_bvalid), .M_AXI_REG_bready (reg_bready),
        .M_AXI_REG_araddr  (reg_araddr),  .M_AXI_REG_arprot (), .M_AXI_REG_arvalid (reg_arvalid), .M_AXI_REG_arready (reg_arready),
        .M_AXI_REG_rdata   (reg_rdata),   .M_AXI_REG_rresp  (reg_rresp), .M_AXI_REG_rvalid (reg_rvalid), .M_AXI_REG_rready (reg_rready),
        .M_AXI_TEL_awaddr  (tel_awaddr),  .M_AXI_TEL_awlen  (tel_awlen), .M_AXI_TEL_awburst (), .M_AXI_TEL_awcache (),
        .M_AXI_TEL_awlock  (), .M_AXI_TEL_awprot (), .M_AXI_TEL_awqos (), .M_AXI_TEL_awsize (), .M_AXI_TEL_awuser (),
        .M_AXI_TEL_awvalid (tel_awvalid), .M_AXI_TEL_awready (tel_awready),
        .M_AXI_TEL_wdata   (tel_wdata),   .M_AXI_TEL_wstrb  (), .M_AXI_TEL_wlast (tel_wlast),
        .M_AXI_TEL_wvalid  (tel_wvalid),  .M_AXI_TEL_wready (tel_wready),
        .M_AXI_TEL_bresp   (tel_bresp),   .M_AXI_TEL_bvalid (tel_bvalid), .M_AXI_TEL_bready (tel_bready),
        .M_AXI_TEL_araddr  (tel_araddr),  .M_AXI_TEL_arlen  (tel_arlen), .M_AXI_TEL_arburst (), .M_AXI_TEL_arcache (),
        .M_AXI_TEL_arlock  (), .M_AXI_TEL_arprot (), .M_AXI_TEL_arqos (), .M_AXI_TEL_arsize (), .M_AXI_TEL_aruser (),
        .M_AXI_TEL_arvalid (tel_arvalid), .M_AXI_TEL_arready (tel_arready),
        .M_AXI_TEL_rdata   (tel_rdata),   .M_AXI_TEL_rresp  (tel_rresp), .M_AXI_TEL_rlast (tel_rlast),
        .M_AXI_TEL_rvalid  (tel_rvalid),  .M_AXI_TEL_rready (tel_rready)
    );

    // control / status registers
    rx_ps_regs u_regs (
        .clk              (clk100),
        .rst_n            (rst100_n),
        .s_axi_awaddr     (reg_awaddr[7:0]), .s_axi_awvalid (reg_awvalid), .s_axi_awready (reg_awready),
        .s_axi_wdata      (reg_wdata),       .s_axi_wvalid  (reg_wvalid),  .s_axi_wready  (reg_wready),
        .s_axi_bresp      (reg_bresp),       .s_axi_bvalid  (reg_bvalid),  .s_axi_bready  (reg_bready),
        .s_axi_araddr     (reg_araddr[7:0]), .s_axi_arvalid (reg_arvalid), .s_axi_arready (reg_arready),
        .s_axi_rdata      (reg_rdata),       .s_axi_rresp   (reg_rresp),   .s_axi_rvalid  (reg_rvalid), .s_axi_rready (reg_rready),
        .rx_soft_rst      (rx_soft_rst),
        .img_arm_toggle   (img_arm_toggle),
        .img_continuous   (img_continuous),
        .img_pending      (img_pending),
        .cap_len          (cap_len),
        .cap_arm          (cap_arm),
        .cap_trig         (cap_trig),
        .cap_busy         (cap_busy),
        .cap_ovf          (cap_ovf),
        .ev_frame_start   (ev_frame_start),
        .ev_frame_end     (ev_frame_end),
        .ev_dec_sym       (fb_tvalid & fb_tready),
        .img_frame_toggle (img_frame_toggle),
        .img_sent_toggle  (img_sent_toggle),
        .fb_in            (fb_frames_in),
        .fb_drop          (fb_dropped),
        .agc              (ctrl_out),
        .locked           (locked),
        .rf_ps_mode       (rf_ps_mode),
        .rf_resetb        (rf_resetb),
        .rf_init_done     (rf_init_done),
        .spi_addr         (spi_addr),
        .spi_wdata        (spi_wdata),
        .spi_wr_tgl       (spi_wr_tgl),
        .spi_rd_tgl       (spi_rd_tgl),
        .spi_rdata        (spi_rdata),
        .spi_cnt          (spi_cnt),
        .spi_rf_ready     (spi_rf_ready)
    );

    // telemetry: equalizer stage snapshots (statistics: PHY frames / symbols; no bit errors in the JSCC build)
    reg [63:0] tm_frames, tm_syms;
    always @(posedge clk100 or negedge rxb_rst_n)
        if (!rxb_rst_n) begin tm_frames <= 64'd0; tm_syms <= 64'd0; end
        else begin tm_frames <= tm_frames + ev_frame_end; tm_syms <= tm_syms + phy_fire; end
    telemetry #(.NSYM(NSYM), .NERR_W(18), .TIMEOUT_MS(100)) u_telemetry (
        .clk            (clk100),
        .rst_n          (rxb_rst_n),
        .pre_data       (dbg_pre_data), .pre_fire (dbg_pre_fire),
        .ce_data        (dbg_ce_data),  .ce_fire  (dbg_ce_fire),
        .cpe_data       (dbg_cpe_data), .cpe_fire (dbg_cpe_fire),
        .sfo_data       (dbg_sfo_data), .sfo_fire (dbg_sfo_fire),
        .frame_done     (ev_frame_end), .frame_bad (1'b0), .frame_bit_err (18'd0),
        .bit_rcvd_cnt   (tm_syms), .bit_err_cnt (64'd0),
        .frame_rcvd_cnt (tm_frames), .frame_err_cnt (64'd0),
        .agc_ctrl       (ctrl_out),
        .sscc_rd_addr   (sscc_rd_addr),
        .sscc_rd_q      (sscc_rd_q),
        .sscc_seq       (sscc_seq),
        .sscc_last_buf  (sscc_last_buf),
        .sscc_ovf       (sscc_ovf),
        .sscc_bytes     (16'd16360),
        .sscc_cw_fail   (sscc_cw_fail),
        .sscc_cw_iters  (sscc_cw_iters),
        .s_axi_awaddr   (tel_awaddr), .s_axi_awlen (tel_awlen), .s_axi_awvalid (tel_awvalid), .s_axi_awready (tel_awready),
        .s_axi_wdata    (tel_wdata),  .s_axi_wlast (tel_wlast), .s_axi_wvalid  (tel_wvalid),  .s_axi_wready  (tel_wready),
        .s_axi_bresp    (tel_bresp),  .s_axi_bvalid (tel_bvalid), .s_axi_bready (tel_bready),
        .s_axi_araddr   (tel_araddr), .s_axi_arlen (tel_arlen), .s_axi_arvalid (tel_arvalid), .s_axi_arready (tel_arready),
        .s_axi_rdata    (tel_rdata),  .s_axi_rresp (tel_rresp), .s_axi_rlast (tel_rlast),
        .s_axi_rvalid   (tel_rvalid), .s_axi_rready (tel_rready)
    );

endmodule
