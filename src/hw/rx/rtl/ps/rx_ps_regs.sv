`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// rx_ps_regs (100 MHz): AXI4-Lite register block of the PS build of the RX board.
//   byte  name          access  meaning
//   0x00  ID            RO      0x4A535258 ("JSRX")
//   0x04  VERSION       RO      2 (2: AD9361 init and SPI from the PS)
//   0x08  CTRL          RW      [0] RX soft reset (level, RX PHY + frame buffer)  [1] clear counters (write 1, self clearing)
//   0x0C  IMG_CTRL      RW      [0] write 1: forward the next complete decoded frame (one shot)  [1] continuous forwarding
//   0x10  IMG_STATUS    RO      [0] one-shot request pending
//   0x14  CAP_LEN       RW      raw ADC capture length (samples)
//   0x18  CAP_CTRL      RW      [0] write 1: arm  [2:1] trigger: 0 immediate, 1 next PHY frame start
//   0x1C  CAP_STATUS    RO      [0] busy (armed / capturing)
//   0x20  SYNC_CNT      RO      frames started by the RX PHY (first symbol out)
//   0x24  PHY_FRAMES    RO      frames completed by the RX PHY (last symbol out)
//   0x28  FB_IN         RO      frames entering the frame buffer
//   0x2C  FB_DROP       RO      frames dropped by the frame buffer (decoder too slow)
//   0x30  DEC_SYMS      RO      symbols handed to the decoder CDC (low 32 bits)
//   0x34  IMG_FRAMES    RO      frames completed by the decoder
//   0x38  IMG_SENT      RO      frames forwarded to the PS
//   0x3C  AGC           RO      AD9361 CTRL_OUT (pointer 0x035=0x16: [7] RX1 gain lock, [6:0] gain index)
//   0x40  STATUS        RO      [0] 250 MHz MMCM locked [1] switch SW0: TX LVDS pairs inverted (tx_swap)
//   0x44  CAP_OVF       RO      ADC samples lost by the capture (output busy)
//   0x48  RF_CTRL       RW      [0] ps_mode (reset 1: AD9361 init by the PS; 0: PL LUT init) [1] AD9361 RESETB (reset 0)
//                               [2] init done (reset 0: RX FIFOs held, ENABLE/TXNRX low; set it after the PS init)
//   0x4C  SPI_CMD       RW      [9:0] address, [17:10] write data, [24] = 1: write, [25] = 1: read (as tx_ps_regs)
//   0x50  SPI_STAT      RO      [7:0] last read data, [15:8] finished transfers (wraps), [16] rf_ready
//   Counters (0x20..0x38) are cleared by CTRL[1] (and reset); FB_* are the frame buffer's own counters since reset.
//////////////////////////////////////////////////////////////////////////////////
module rx_ps_regs (
    input  logic        clk,
    input  logic        rst_n,
    // AXI4-Lite slave
    input  logic [7:0]  s_axi_awaddr, input logic s_axi_awvalid, output logic s_axi_awready,
    input  logic [31:0] s_axi_wdata,  input logic s_axi_wvalid,  output logic s_axi_wready,
    output logic [1:0]  s_axi_bresp,  output logic s_axi_bvalid, input  logic s_axi_bready,
    input  logic [7:0]  s_axi_araddr, input logic s_axi_arvalid, output logic s_axi_arready,
    output logic [31:0] s_axi_rdata,  output logic [1:0] s_axi_rresp, output logic s_axi_rvalid, input logic s_axi_rready,
    // control
    output logic        rx_soft_rst,
    output logic        img_arm_toggle,
    output logic        img_continuous,
    input  logic        img_pending,          // async (250 MHz)
    output logic [31:0] cap_len,
    output logic        cap_arm,              // pulse
    output logic [1:0]  cap_trig,
    input  logic        cap_busy,
    input  logic [31:0] cap_ovf,
    // events / status (100 MHz pulses unless noted)
    input  logic        ev_frame_start,
    input  logic        ev_frame_end,
    input  logic        ev_dec_sym,
    input  logic        img_frame_toggle,     // async (250 MHz)
    input  logic        img_sent_toggle,      // async (250 MHz)
    input  logic [31:0] fb_in, input logic [31:0] fb_drop,
    input  logic [7:0]  agc,                  // async
    input  logic        tx_swap,              // async (board switch)
    input  logic        locked,               // async
    // AD9361 init / SPI (ad9361_top, clk_20M)
    output logic        rf_ps_mode,
    output logic        rf_resetb,
    output logic        rf_init_done,
    output logic [9:0]  spi_addr,
    output logic [7:0]  spi_wdata,
    output logic        spi_wr_tgl,
    output logic        spi_rd_tgl,
    input  logic [7:0]  spi_rdata,            // async, stable once spi_cnt has advanced
    input  logic [7:0]  spi_cnt,              // async
    input  logic        spi_rf_ready          // async
);
    (* ASYNC_REG = "TRUE" *) logic [1:0][16:0] spi_s;
    always_ff @(posedge clk) spi_s <= {spi_s[0], {spi_rf_ready, spi_cnt, spi_rdata}};
    //---------------------------------------------------------------- async inputs
    (* ASYNC_REG = "TRUE" *) logic [2:0] ft_s, st_s;
    (* ASYNC_REG = "TRUE" *) logic [1:0] pend_s, lock_s, swap_s;
    (* ASYNC_REG = "TRUE" *) logic [7:0] agc_s1, agc_s2;
    always_ff @(posedge clk) begin
        ft_s <= {ft_s[1:0], img_frame_toggle}; st_s <= {st_s[1:0], img_sent_toggle};
        pend_s <= {pend_s[0], img_pending}; lock_s <= {lock_s[0], locked}; swap_s <= {swap_s[0], tx_swap};
        agc_s1 <= agc; agc_s2 <= agc_s1;
    end
    //---------------------------------------------------------------- counters
    logic clr;
    logic [31:0] c_sync, c_phy, c_dec, c_img, c_sent;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin c_sync <= '0; c_phy <= '0; c_dec <= '0; c_img <= '0; c_sent <= '0; end
        else if (clr) begin c_sync <= '0; c_phy <= '0; c_dec <= '0; c_img <= '0; c_sent <= '0; end
        else begin
            c_sync <= c_sync + ev_frame_start;
            c_phy  <= c_phy  + ev_frame_end;
            c_dec  <= c_dec  + ev_dec_sym;
            c_img  <= c_img  + (ft_s[2] ^ ft_s[1]);
            c_sent <= c_sent + (st_s[2] ^ st_s[1]);
        end
    end
    //---------------------------------------------------------------- AXI4-Lite
    logic [7:0] awa; logic aw_ok, w_ok; logic [31:0] wd;
    assign s_axi_awready = ~aw_ok;
    assign s_axi_wready  = ~w_ok;
    assign s_axi_bresp   = 2'b00;
    assign s_axi_rresp   = 2'b00;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            aw_ok <= 1'b0; w_ok <= 1'b0; awa <= '0; wd <= '0; s_axi_bvalid <= 1'b0;
            rx_soft_rst <= 1'b0; clr <= 1'b0; img_arm_toggle <= 1'b0; img_continuous <= 1'b0;
            cap_len <= 32'd65536; cap_arm <= 1'b0; cap_trig <= 2'd0;
            rf_ps_mode <= 1'b1; rf_resetb <= 1'b0; rf_init_done <= 1'b0;
            spi_addr <= '0; spi_wdata <= '0; spi_wr_tgl <= 1'b0; spi_rd_tgl <= 1'b0;
        end
        else begin
            clr <= 1'b0; cap_arm <= 1'b0;
            if (s_axi_awvalid & ~aw_ok) begin aw_ok <= 1'b1; awa <= s_axi_awaddr; end
            if (s_axi_wvalid  & ~w_ok)  begin w_ok  <= 1'b1; wd  <= s_axi_wdata;  end
            if (aw_ok & w_ok & ~s_axi_bvalid) begin
                case (awa[7:2])
                    6'h02: begin rx_soft_rst <= wd[0]; clr <= wd[1]; end
                    6'h03: begin if (wd[0]) img_arm_toggle <= ~img_arm_toggle; img_continuous <= wd[1]; end
                    6'h05: cap_len <= wd;
                    6'h06: begin cap_arm <= wd[0]; cap_trig <= wd[2:1]; end
                    6'h12: begin rf_ps_mode <= wd[0]; rf_resetb <= wd[1]; rf_init_done <= wd[2]; end
                    6'h13: begin
                        spi_addr <= wd[9:0]; spi_wdata <= wd[17:10];
                        if (wd[24]) spi_wr_tgl <= ~spi_wr_tgl;
                        else if (wd[25]) spi_rd_tgl <= ~spi_rd_tgl;
                    end
                    default: ;
                endcase
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
                    6'h00: s_axi_rdata <= 32'h4A53_5258;
                    6'h01: s_axi_rdata <= 32'd2;
                    6'h02: s_axi_rdata <= {31'd0, rx_soft_rst};
                    6'h03: s_axi_rdata <= {30'd0, img_continuous, 1'b0};
                    6'h04: s_axi_rdata <= {31'd0, pend_s[1]};
                    6'h05: s_axi_rdata <= cap_len;
                    6'h06: s_axi_rdata <= {29'd0, cap_trig, 1'b0};
                    6'h07: s_axi_rdata <= {31'd0, cap_busy};
                    6'h08: s_axi_rdata <= c_sync;
                    6'h09: s_axi_rdata <= c_phy;
                    6'h0A: s_axi_rdata <= fb_in;
                    6'h0B: s_axi_rdata <= fb_drop;
                    6'h0C: s_axi_rdata <= c_dec;
                    6'h0D: s_axi_rdata <= c_img;
                    6'h0E: s_axi_rdata <= c_sent;
                    6'h0F: s_axi_rdata <= {24'd0, agc_s2};
                    6'h10: s_axi_rdata <= {30'd0, swap_s[1], lock_s[1]};
                    6'h11: s_axi_rdata <= cap_ovf;
                    6'h12: s_axi_rdata <= {29'd0, rf_init_done, rf_resetb, rf_ps_mode};
                    6'h13: s_axi_rdata <= {14'd0, spi_wdata, spi_addr};
                    6'h14: s_axi_rdata <= {15'd0, spi_s[1]};
                    default: s_axi_rdata <= 32'hDEAD_BEEF;
                endcase
            end
            else if (s_axi_rvalid & s_axi_rready) s_axi_rvalid <= 1'b0;
        end
    end
endmodule
