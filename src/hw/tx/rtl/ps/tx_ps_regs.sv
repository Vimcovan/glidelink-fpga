`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// tx_ps_regs (100 MHz): AXI4-Lite register block of the PS build of the TX board.
//   byte  name          access  meaning
//   0x00  ID            RO      0x4A535458 ("JSTX")
//   0x04  VERSION       RO      3 (2: AD9361 SPI access, 3: SSCC baseline)
//   0x08  CTRL          RW      [0] symbol source: 0 = DeepJSCC encoder, 1 = PRBS 64-QAM self test (frame aligned)
//                               [1] clear counters (write 1, self clearing)
//                               [2] SSCC baseline: PS bytes -> sscc_tx (JPEG packets) instead of the encoder
//   0x0C  SRC_ACTIVE    RO      [0] source actually in use (changes at a frame boundary), [2] SSCC in use
//   0x10  IMG_BYTES     RO      image bytes handed to the encoder
//   0x14  SYMS          RO      IQ symbols handed to the OFDM baseband
//   0x18  SYM_FRAMES    RO      symbol frames (tlast) handed to the OFDM baseband
//   0x1C  TX_FRAMES     RO      frames sent to the DAC (20 MSPS gate)
//   0x20  FB_FRAMES     RO      frames entering the DAC frame buffer (since reset)
//   0x24  TLAST_ERR     RO      symbol frames whose tlast was not on symbol 32767
//   0x28  STATUS        RO      [0] 250 MHz MMCM locked [1] switch SW0: TX LVDS pairs inverted (tx_swap)
//   0x2C  SPI_CMD       RW      AD9361 register access (after rf_ready): [9:0] address, [17:10] write data,
//                               [24] = 1: start a write, [25] = 1: start a read (bits 24/25 read back 0)
//   0x30  SPI_STAT      RO      [7:0] last read data, [15:8] finished transfers (wraps), [16] rf_ready
//                               (the PS waits for [15:8] to advance before the next SPI_CMD)
//   0x34  SSCC_FRAMES   RO      SSCC frames (32768 symbols) produced
//////////////////////////////////////////////////////////////////////////////////
module tx_ps_regs (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [7:0]  s_axi_awaddr, input logic s_axi_awvalid, output logic s_axi_awready,
    input  logic [31:0] s_axi_wdata,  input logic s_axi_wvalid,  output logic s_axi_wready,
    output logic [1:0]  s_axi_bresp,  output logic s_axi_bvalid, input  logic s_axi_bready,
    input  logic [7:0]  s_axi_araddr, input logic s_axi_arvalid, output logic s_axi_arready,
    output logic [31:0] s_axi_rdata,  output logic [1:0] s_axi_rresp, output logic s_axi_rvalid, input logic s_axi_rready,
    output logic        src_sel,
    input  logic        src_active,          // async (250 MHz)
    input  logic        ev_img_byte,
    input  logic        ev_sym,
    input  logic        ev_sym_frame,
    input  logic        ev_tx_frame,
    input  logic        ev_tlast_err,
    input  logic [31:0] fb_frames,
    input  logic        tx_swap,              // async (board switch)
    input  logic        locked,              // async
    // AD9361 SPI debug engine (ad9361_top, clk_20M)
    output logic [9:0]  spi_addr,
    output logic [7:0]  spi_wdata,
    output logic        spi_wr_tgl,
    output logic        spi_rd_tgl,
    input  logic [7:0]  spi_rdata,           // async (20 MHz), stable once spi_cnt has advanced
    input  logic [7:0]  spi_cnt,             // async
    input  logic        spi_rf_ready,        // async
    // SSCC baseline (100 MHz)
    output logic        sscc_sel,
    input  logic        sscc_active,
    input  logic        ev_sscc_frame
);
    logic [31:0] c_sscc;
    (* ASYNC_REG = "TRUE" *) logic [1:0][16:0] spi_s;
    always_ff @(posedge clk) spi_s <= {spi_s[0], {spi_rf_ready, spi_cnt, spi_rdata}};
    (* ASYNC_REG = "TRUE" *) logic [1:0] act_s, lock_s, swap_s;
    always_ff @(posedge clk) begin act_s <= {act_s[0], src_active}; lock_s <= {lock_s[0], locked}; swap_s <= {swap_s[0], tx_swap}; end
    logic clr;
    logic [31:0] c_img, c_sym, c_symf, c_txf, c_terr;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)   begin c_img <= '0; c_sym <= '0; c_symf <= '0; c_txf <= '0; c_terr <= '0; c_sscc <= '0; end
        else if (clr) begin c_img <= '0; c_sym <= '0; c_symf <= '0; c_txf <= '0; c_terr <= '0; c_sscc <= '0; end
        else begin
            c_img  <= c_img  + ev_img_byte;
            c_sym  <= c_sym  + ev_sym;
            c_symf <= c_symf + ev_sym_frame;
            c_txf  <= c_txf  + ev_tx_frame;
            c_terr <= c_terr + ev_tlast_err;
            c_sscc <= c_sscc + ev_sscc_frame;
        end
    end
    logic [7:0] awa; logic aw_ok, w_ok; logic [31:0] wd;
    assign s_axi_awready = ~aw_ok;
    assign s_axi_wready  = ~w_ok;
    assign s_axi_bresp   = 2'b00;
    assign s_axi_rresp   = 2'b00;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin aw_ok <= 1'b0; w_ok <= 1'b0; awa <= '0; wd <= '0; s_axi_bvalid <= 1'b0; src_sel <= 1'b0; clr <= 1'b0;
                          spi_addr <= '0; spi_wdata <= '0; spi_wr_tgl <= 1'b0; spi_rd_tgl <= 1'b0; sscc_sel <= 1'b0; end
        else begin
            clr <= 1'b0;
            if (s_axi_awvalid & ~aw_ok) begin aw_ok <= 1'b1; awa <= s_axi_awaddr; end
            if (s_axi_wvalid  & ~w_ok)  begin w_ok  <= 1'b1; wd  <= s_axi_wdata;  end
            if (aw_ok & w_ok & ~s_axi_bvalid) begin
                if (awa[7:2] == 6'h02) begin src_sel <= wd[0]; clr <= wd[1]; sscc_sel <= wd[2]; end
                if (awa[7:2] == 6'h0B) begin
                    spi_addr <= wd[9:0]; spi_wdata <= wd[17:10];
                    if (wd[24]) spi_wr_tgl <= ~spi_wr_tgl;
                    else if (wd[25]) spi_rd_tgl <= ~spi_rd_tgl;
                end
                s_axi_bvalid <= 1'b1;
            end
            if (s_axi_bvalid & s_axi_bready) begin s_axi_bvalid <= 1'b0; aw_ok <= 1'b0; w_ok <= 1'b0; end
        end
    end
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin s_axi_arready <= 1'b0; s_axi_rvalid <= 1'b0; s_axi_rdata <= '0; end
        else begin
            s_axi_arready <= 1'b0;
            if (s_axi_arvalid & ~s_axi_arready & ~s_axi_rvalid) begin
                s_axi_arready <= 1'b1;
                s_axi_rvalid  <= 1'b1;
                case (s_axi_araddr[7:2])
                    6'h00: s_axi_rdata <= 32'h4A53_5458;
                    6'h01: s_axi_rdata <= 32'd3;
                    6'h02: s_axi_rdata <= {29'd0, sscc_sel, 1'b0, src_sel};
                    6'h03: s_axi_rdata <= {29'd0, sscc_active, 1'b0, act_s[1]};
                    6'h04: s_axi_rdata <= c_img;
                    6'h05: s_axi_rdata <= c_sym;
                    6'h06: s_axi_rdata <= c_symf;
                    6'h07: s_axi_rdata <= c_txf;
                    6'h08: s_axi_rdata <= fb_frames;
                    6'h09: s_axi_rdata <= c_terr;
                    6'h0A: s_axi_rdata <= {30'd0, swap_s[1], lock_s[1]};
                    6'h0B: s_axi_rdata <= {14'd0, spi_wdata, spi_addr};
                    6'h0C: s_axi_rdata <= {15'd0, spi_s[1]};
                    6'h0D: s_axi_rdata <= c_sscc;
                    default: s_axi_rdata <= 32'hDEAD_BEEF;
                endcase
            end
            else if (s_axi_rvalid & s_axi_rready) s_axi_rvalid <= 1'b0;
        end
    end
endmodule
