`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/03/13 22:54:15
// Design Name: 
// Module Name: ad9361_top
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////


module ad9361_top#(
    parameter TXQ_EN = 1        // 1: run TX quad-cal RX-NCO phase search after LUT init; 0: skip (RX-only build, TX powered down in LUT)
)
(
    input           sys_clk                  ,//������ʱ�� 100MHz (����/TX FIFO д��/RX FIFO ����/ILA ͬ��)
    input           clk_20M                  ,//SPIʱ��
    input           rst_n                    ,
    input           tx_swap                  ,   // board switch: TX data-pair polarity (ad9361_lvds_mode)
    output          led                      ,
    //��������
    output[11:0]    rx_data_i                ,
    output[11:0]    rx_data_q                ,
    output          rx_valid                 ,
    input           rx_ready                 ,
    //��������
    input[11:0]     tx_data_i                ,
    input[11:0]     tx_data_q                ,
    input           tx_valid                 ,
    output          tx_ready                 ,

	//AD9361 SPI�ӿ�
    output          spi_clk                  ,
    output          spi_csn                  ,
    output          spi_mosi                 ,
    input           spi_miso                 ,
    //AD9361 ���ֽӿ�
    input           rx_clk_in_p              ,
    input           rx_clk_in_n              ,
    input[5:0]      rx_data_in_n             ,
    input[5:0]      rx_data_in_p             ,
    input           rx_frame_in_n            ,
    input           rx_frame_in_p            ,
    output          tx_clk_out_n             ,
    output          tx_clk_out_p             ,
    output[5:0]     tx_data_out_n            ,
    output[5:0]     tx_data_out_p            ,
    output          tx_frame_out_n           ,
    output          tx_frame_out_p           ,
    //AD9361 IO
    output          en_agc                   ,
    output reg     enable                   ,
    output reg     txnrx                    ,
    output          resetb                   ,
    output          sync_in                  ,
    output[3:0]     ctrl_in                  ,
    input[7:0]      ctrl_out                 ,
    // AD9361 init from the PS (rx_ps_regs RF_CTRL / SPI_CMD / SPI_STAT, sys_clk domain, synchronised here):
    //   ps_mode = 1: the PL LUT engine is held in reset, RESETB = ps_resetb, init done = ps_init_done, the SPI
    //   belongs to the PS debug engine from the start. ps_mode = 0: the original PL LUT init (fallback).
    input           ps_mode                  ,
    input           ps_resetb                ,
    input           ps_init_done             ,
    input[9:0]      ps_spi_addr              ,
    input[7:0]      ps_spi_wdata             ,
    input           ps_spi_wr_tgl            ,
    input           ps_spi_rd_tgl            ,
    output[7:0]     ps_spi_rdata             ,
    output[7:0]     ps_spi_cnt               ,
    output          ps_rf_ready
);
    
    wire               [  11: 0]        dac_data_i                 ;
    wire               [  11: 0]        dac_data_q                 ;
    wire               [  11: 0]        fifo_dout_i                ;
    wire               [  11: 0]        fifo_dout_q                ;

    wire               [  11: 0]        adc_data_i                 ;
    wire               [  11: 0]        adc_data_q                 ;

    wire                                data_clk                    ;
    wire                                adc_status                  ;
    wire                                dac_valid                   ;
    wire                                dac_ready                   ;
    wire                                adc_valid                   ;

    wire                                ad9361_config_start         ;
    wire                                ad9361_config_read          ;
    wire                                ad9361_config_write         ;
    wire               [   9: 0]        ad9361_config_address       ;
    wire               [   7: 0]        ad9361_config_writedata     ;
    wire               [   7: 0]        ad9361_config_readdata      ;
    wire                                ad9361_config_waitrequest   ;
    wire                                ad9361_config_init_done     ;   // effective: PL LUT or PS init finished
    wire                                cfg_done_lut                ;   // PL LUT engine finished
    wire                                cfg_resetb                  ;   // RESETB from the PL LUT engine
    (* ASYNC_REG = "TRUE" *) reg [1:0]  psm_s, psr_s, psd_s         ;
    always @ (posedge clk_20M or negedge rst_n) begin
        if (!rst_n) begin psm_s <= 2'b11; psr_s <= 2'b00; psd_s <= 2'b00; end
        else begin psm_s <= {psm_s[0], ps_mode}; psr_s <= {psr_s[0], ps_resetb}; psd_s <= {psd_s[0], ps_init_done}; end
    end
    wire                                ps_mode_s   = psm_s[1]      ;
    assign ad9361_config_init_done = ps_mode_s ? psd_s[1] : cfg_done_lut;
    assign resetb                  = ps_mode_s ? psr_s[1] : cfg_resetb;
    wire               [   3: 0]        ENSM_State                  ;

    //---- �۲��� (���� ILA, ��Ӱ���κι���) ----
    wire               [   5: 0]        dbg_tx_data_p               ;
    wire               [   5: 0]        dbg_tx_data_n               ;
    wire                                dbg_tx_frame                ;
    wire               [   3: 0]        dbg_ctrl4                   ;
    reg                [   3: 0]        dbg_ctrl4_r                 ;
    wire               [  15: 0]        dbg_fifo_dout_q             ;

/*************************** ledָʾ�� ********************************************/	

	// led: see "assign led" next to rf_ready below									//�Ĵ���������ɺ�led����
	

/*************************** FIFO reset *****************************************/
    // fifo_async = Independent_Clocks + Safety Circuit: reset must be seen with BOTH clocks running.
    // data_clk only exists after the AD9361 BBPLL/LVDS is configured by the LUT, so a reset released at
    // FPGA config time (rst_n pin) can leave wr/rd_rst_busy hung -> TX FIFO stuck full (seen 2026-09-26).
    // Hold both FIFOs in reset until LUT init is done, then 256 more clk_20M cycles.
    reg                [   7: 0]        fifo_rst_cnt                ;
    reg                                 fifo_rst                    ;
    always @ (posedge clk_20M or negedge rst_n) begin
        if (!rst_n) begin
            fifo_rst_cnt <= 8'd0;
            fifo_rst     <= 1'b1;
        end
        else if (!ad9361_config_init_done) begin
            fifo_rst_cnt <= 8'd0;
            fifo_rst     <= 1'b1;
        end
        else if (fifo_rst_cnt != 8'hFF) fifo_rst_cnt <= fifo_rst_cnt + 1'b1;
        else                             fifo_rst     <= 1'b0;
    end

/*************************** dac�������� ****************************************/

    wire tx_wr_en;
    wire tx_rd_en;
    wire tx_fifo_full;
	wire tx_fifo_empty;

    //---- ͻ������: �ӿڲ�ֻ�ش�һ�����⡪��FIFO ����û������ ----
    //     empty=0 -> �� fifo_dout (��Ч����)
    //     empty=1 -> �� 0         (��Ĭ)
    // dac_valid �� 1: DAC ������������ͣ (LVDS �� frame �����ճ�ÿ 2 ��һ��);
    // ��Ĭ��"���������� 0"����, ����ͣ֡, Ҳ���� ENSM ����Ƶ��·��
    // һ֡�ж೤��֡������, ȫ�����ⲿ����Դ���� ���� ��ģ�鲻�����㡢����״̬��
	assign dac_valid = 1'b1;
	assign tx_ready  = ~tx_fifo_full;
    assign tx_wr_en  = tx_valid & tx_ready;
    assign tx_rd_en  = dac_ready & ~tx_fifo_empty;

    // ��ע��: FIFO �� FWFT, ��ʱ dout �ǲ�����ֵ, ������ʽ���� (openwifi tx_iq_intf ͬ����)
    assign dac_data_i = tx_fifo_empty ? 12'd0 : fifo_dout_i;
    assign dac_data_q = tx_fifo_empty ? 12'd0 : fifo_dout_q;

	//�첽FIFO��ʱ����
	fifo_async u_dac_fifo (
      .rst(fifo_rst),                  // input wire rst
      .wr_clk(sys_clk),            // input wire wr_clk (sys_clk=100MHz, �� tx_valid/tx_data ͬ��)
      .rd_clk(data_clk),            // input wire rd_clk
      .din({tx_data_q,tx_data_i}),                  // input wire [23 : 0] din
      .wr_en(tx_wr_en),              // input wire wr_en
      .rd_en(tx_rd_en),              // input wire rd_en
      .dout({fifo_dout_q,fifo_dout_i}),              // output wire [23 : 0] dout
      .full(tx_fifo_full),                // output wire full
      .empty(tx_fifo_empty),              // output wire empty
      .wr_rst_busy(),  // output wire wr_rst_busy
      .rd_rst_busy()  // output wire rd_rst_busy
    );
	
    // �۲��ô�� (���۲�) ���� �� ila_txph һ���Ƴ�

/*************************** adc��������  ****************************************/
    wire rx_wr_en;
    wire rx_rd_en;
    wire rx_fifo_full;
    wire rx_fifo_empty;

	assign rx_valid = ~rx_fifo_empty;
	assign rx_rd_en = rx_valid & rx_ready;
	assign rx_wr_en = ~rx_fifo_full & adc_valid;
	//�첽FIFO��ʱ����
	fifo_async u_adc_fifo (
      .rst(fifo_rst),                  // input wire rst
      .wr_clk(data_clk),            // input wire wr_clk
      .rd_clk(sys_clk),            // input wire rd_clk
      .din({adc_data_q,adc_data_i}),                  // input wire [23 : 0] din
      .wr_en(rx_wr_en),              // input wire wr_en
      .rd_en(rx_rd_en),              // input wire rd_en
      .dout({rx_data_q,rx_data_i}),                // output wire [23 : 0] dout
      .full(rx_fifo_full),                // output wire full
      .empty(rx_fifo_empty),              // output wire empty
      .wr_rst_busy(),  // output wire wr_rst_busy
      .rd_rst_busy()  // output wire rd_rst_busy
    );

/*************************** ad9361 ��ؿ��� **************************************/	
	//������ƣ�����Ĵ������õ���AGC�Զ�������ƣ��������2�в�������;
	//         ������õ���MGC�����������üĴ���������оƬ�ֲ��޸���������
	assign en_agc		 = 1'b0;
	assign ctrl_in	     = 4'b0000;

	// sync_in�����ڶ�Ƭͬ������������û��ʹ�ò�������
	assign sync_in	     = 1'b1;


	// �շ����ƣ��Ĵ���ǿ������Ϊ FDD Independent ,  FDD level mode 
	// �շ�ʹ�ܣ�txnrx=1,enable=1   ����ʹ�ܣ�txnrx=1,enable=0  ����ʹ�ܣ�txnrx=0,enable=1
	// 2R2T�շ������������ad9361��һ�����ڷ���һ�������գ��������һ��ad9361�໥���Ź����²�������ʧ��
	// ����Ʋ����շ�: ��ʼ����ɺ�̶� txnrx=1/enable=1, ֮��һ�ɲ�����
	// "��Ĭ"�ɻ��������� 0 ���, ����Ҫͨ�� ENSM ������Ƶ��·��
	// ע: �������Ĵ�����ʱ���� clk_20M ���� �� init_done �Ĳ�����(clk_20M)��ͬ, ��Ȼͬ��;
	//     ԭ���� sys_clk(200MHz) ���ڰ�һ�� 20MHz ���ƽֱ���ͽ� 200MHz ��(��ͬ����),
	//     �ĳ�ͬ�����������������, Ҳ����Ƶʹ�ܲ����� 200MHz ʱ���Ƿ���ڡ�
	always @ (posedge clk_20M or negedge rst_n )	begin		
		if (!rst_n) begin
			txnrx <= 1'b0; 
			enable <= 1'b0;
		end
		else begin  
			if ( ad9361_config_init_done == 1'b1 ) begin       
				txnrx <= 1'b1; 
				enable <= 1'b1;
			end  
		end
	end
    

/*************************** ģ������  **************************************/
    ad9361_lvds_mode u_ad9361_lvds_mode(
		.clk_100M                           (sys_clk                  ),
		.tx_swap                            (tx_swap                 ),
		.data_clk                           (data_clk                  ),
		.rx_clk_in_p                        (rx_clk_in_p             ),
		.rx_clk_in_n                        (rx_clk_in_n             ),
		.rx_data_in_p                       (rx_data_in_p            ),
		.rx_data_in_n                       (rx_data_in_n            ),
		.rx_frame_in_p                      (rx_frame_in_p           ),
		.rx_frame_in_n                      (rx_frame_in_n           ),
		.tx_clk_out_p                       (tx_clk_out_p            ),
		.tx_clk_out_n                       (tx_clk_out_n            ),
		.tx_frame_out_p                     (tx_frame_out_p          ),
		.tx_frame_out_n                     (tx_frame_out_n          ),
		.tx_data_out_p                      (tx_data_out_p           ),
		.tx_data_out_n                      (tx_data_out_n           ),
		.adc_valid                          (adc_valid               ),
		.adc_status                         (adc_status              ),
		.adc_data_i                         (adc_data_i              ),
		.adc_data_q                         (adc_data_q              ),
		.dac_valid                          (dac_valid               ),
		.dac_ready                          (dac_ready               ),
		.dac_data_i                         (dac_data_i              ),
		.dac_data_q                         (dac_data_q              ),
		.dbg_tx_data_p                      (dbg_tx_data_p           ),
		.dbg_tx_data_n                      (dbg_tx_data_n           ),
		.dbg_tx_frame                       (dbg_tx_frame            )
    );

/*************************** TX quad cal phase search (clk_20M) *****************************
    Runs automatically once the LUT init is done (and again on every dbg_txq_tgl toggle).
    SPI ownership: LUT init -> phase search -> VIO debug engine.
*********************************************************************************************/
    wire                                txq_start                   ;
    wire                                txq_spi_start               ;
    wire                                txq_spi_read                ;
    wire                                txq_spi_write               ;
    wire               [   9: 0]        txq_spi_address             ;
    wire               [   7: 0]        txq_spi_wdata               ;
    wire                                txq_busy                    ;
    wire                                txq_done                    ;
    wire                                txq_err                     ;
    wire               [   4: 0]        txq_phase                   ;
    wire               [  31: 0]        txq_field                   ;
    wire               [   5: 0]        txq_run_len                 ;
    wire               [   1: 0]        txq_status                  ;
    wire                                dbg_txq_tgl                 ;
    reg                                 dbg_txq_tgl_d               ;
    reg                                 cfg_done_d                  ;
    wire                                rf_ready                    ;
    reg                                 dbg_busy                    ;   // debug engine (below) has a transfer in flight

    always @ (posedge clk_20M or negedge rst_n) begin
        if (!rst_n) begin
            cfg_done_d    <= 1'b0;
            dbg_txq_tgl_d <= 1'b0;
        end
        else begin
            cfg_done_d    <= ad9361_config_init_done;
            if (txq_start) dbg_txq_tgl_d <= dbg_txq_tgl;                 // a pending re-run request is consumed when the search starts
        end
    end

    assign txq_start = (TXQ_EN != 0) & ((ad9361_config_init_done & ~cfg_done_d) |
                       (ad9361_config_init_done & txq_done & ~txq_busy & ~dbg_busy & (dbg_txq_tgl != dbg_txq_tgl_d)));
    assign rf_ready  = (TXQ_EN != 0) ? (txq_done & ~txq_busy) : ad9361_config_init_done;
    assign led       = rf_ready;                                          // LUT init + TX quad phase search finished

    ad9361_txq_search u_ad9361_txq_search (
        .I_clk                              (clk_20M                   ),
        .I_rst_n                            (rst_n                     ),
        .I_start                            (txq_start                 ),
        .O_start                            (txq_spi_start             ),
        .O_read                             (txq_spi_read              ),
        .O_write                            (txq_spi_write             ),
        .O_address                          (txq_spi_address           ),
        .O_writedata                        (txq_spi_wdata             ),
        .I_readdata                         (ad9361_config_readdata    ),
        .I_waitrequest                      (ad9361_config_waitrequest ),
        .O_busy                             (txq_busy                  ),
        .O_done                             (txq_done                  ),
        .O_err                              (txq_err                   ),
        .O_phase                            (txq_phase                 ),
        .O_field                            (txq_field                 ),
        .O_run_len                          (txq_run_len               ),
        .O_status                           (txq_status                )
    );

/*************************** SPI debug port (VIO, clk_20M) ***********************************
    After rf_ready the SPI master is handed to a VIO-driven debug engine:
      dbg_addr / dbg_wdata : register address / write data
      dbg_wr_tgl           : toggle -> one SPI write
      dbg_rd_tgl           : toggle -> one SPI read, result in dbg_rdata
      dbg_cnt              : increments after every finished debug transfer
*********************************************************************************************/
    wire               [   9: 0]        dbg_addr                    ;
    wire               [   7: 0]        dbg_wdata                   ;
    wire                                dbg_wr_tgl                  ;
    wire                                dbg_rd_tgl                  ;
    reg                [   7: 0]        dbg_rdata                   ;
    reg                [   7: 0]        dbg_cnt                     ;
    reg                                 dbg_wr_tgl_d                ;
    reg                                 dbg_rd_tgl_d                ;
    reg                                 dbg_start                   ;
    reg                                 dbg_read                    ;
    (* ASYNC_REG = "TRUE" *) reg [1:0] wr_tgl_s, rd_tgl_s;
    always @ (posedge clk_20M) begin
        wr_tgl_s <= {wr_tgl_s[0], ps_spi_wr_tgl};
        rd_tgl_s <= {rd_tgl_s[0], ps_spi_rd_tgl};
    end
    assign dbg_addr     = ps_spi_addr;
    assign dbg_wdata    = ps_spi_wdata;
    assign dbg_wr_tgl   = wr_tgl_s[1];
    assign dbg_rd_tgl   = rd_tgl_s[1];
    assign ps_spi_rdata = dbg_rdata;
    assign ps_spi_cnt   = dbg_cnt;
    assign ps_rf_ready  = rf_ready;
    wire               [   9: 0]        vio_addr_nc                 ;   // VIO outputs no longer used
    wire               [   7: 0]        vio_wdata_nc                ;
    wire                                vio_wr_nc, vio_rd_nc        ;
    wire                                dbg_ok = rf_ready | ps_mode_s;  // PS init: the SPI is the PS's from the start

    always @ (posedge clk_20M or negedge rst_n) begin
        if (!rst_n) begin
            dbg_wr_tgl_d <= 1'b0;
            dbg_rd_tgl_d <= 1'b0;
            dbg_start    <= 1'b0;
            dbg_read     <= 1'b0;
            dbg_busy     <= 1'b0;
            dbg_rdata    <= 8'd0;
            dbg_cnt      <= 8'd0;
        end
        else begin
            dbg_wr_tgl_d <= dbg_wr_tgl;
            dbg_rd_tgl_d <= dbg_rd_tgl;
            dbg_start    <= 1'b0;
            if (!dbg_busy) begin
                if (dbg_ok && !txq_start && (dbg_wr_tgl != dbg_wr_tgl_d)) begin
                    dbg_read  <= 1'b0;
                    dbg_start <= 1'b1;
                    dbg_busy  <= 1'b1;
                end
                else if (dbg_ok && !txq_start && (dbg_rd_tgl != dbg_rd_tgl_d)) begin
                    dbg_read  <= 1'b1;
                    dbg_start <= 1'b1;
                    dbg_busy  <= 1'b1;
                end
            end
            else if (!dbg_start && !ad9361_config_waitrequest) begin
                if (dbg_read) dbg_rdata <= ad9361_config_readdata;
                dbg_cnt  <= dbg_cnt + 1'b1;
                dbg_busy <= 1'b0;
            end
        end
    end

    // SPI owner: LUT init until init_done, then the phase search while it is busy, then the debug engine
    wire                                own_cfg     = ~ps_mode_s & ~cfg_done_lut;
    wire                                own_txq     = txq_busy;
    wire                                spi_start   = own_cfg ? ad9361_config_start     : own_txq ? txq_spi_start   : dbg_start;
    wire                                spi_read    = own_cfg ? ad9361_config_read      : own_txq ? txq_spi_read    : dbg_read;
    wire                                spi_write   = own_cfg ? ad9361_config_write     : own_txq ? txq_spi_write   : ~dbg_read;
    wire               [   9: 0]        spi_address = own_cfg ? ad9361_config_address   : own_txq ? txq_spi_address : dbg_addr;
    wire               [   7: 0]        spi_wdata   = own_cfg ? ad9361_config_writedata : own_txq ? txq_spi_wdata   : dbg_wdata;

    vio_spi u_vio_spi (
        .clk        (clk_20M                  ),
        .probe_in0  (dbg_rdata                ),   // [7:0]
        .probe_in1  (dbg_cnt                  ),   // [7:0]
        .probe_in2  (rf_ready                 ),   // [0:0]  LUT init + phase search done
        .probe_in3  (ENSM_State               ),   // [3:0]
        .probe_in4  (txq_field                ),   // [31:0] bit p = TX quad cal converged at RX NCO phase p
        .probe_in5  ({1'b0, txq_err, txq_done, txq_status, txq_run_len, txq_phase}), // [15:0]
        .probe_out0 (vio_addr_nc              ),   // [9:0]  (SPI debug now driven by the PS)
        .probe_out1 (vio_wdata_nc             ),   // [7:0]
        .probe_out2 (vio_wr_nc                ),   // [0:0]
        .probe_out3 (vio_rd_nc                ),   // [0:0]
        .probe_out4 (dbg_txq_tgl              )    // [0:0]  toggle -> re-run the phase search (e.g. after an LO change)
    );

    ad9361_config u_ad9361_config(
		.I_clk                              (clk_20M                   ),
		.I_rst_n                            (rst_n & ~ps_mode_s      ),
		.O_start                            (ad9361_config_start       ),
		.O_read                             (ad9361_config_read        ),
		.O_write                            (ad9361_config_write       ),
		.O_address                          (ad9361_config_address     ),
		.O_writedata                        (ad9361_config_writedata   ),
		.I_readdata                         (ad9361_config_readdata    ),
		.I_waitrequest                      (ad9361_config_waitrequest ),
		.O_resetb                           (cfg_resetb              ),
		.O_ENSM_state                       (ENSM_State                ),
		.O_ad9361_congfig_done              (cfg_done_lut              ) 
    );

    ad9361_spi u_ad9361_spi(
		.I_clk                              (clk_20M                   ),
		.I_rst_n                            (rst_n                   ),
		.I_start                            (spi_start                 ),
		.I_read                             (spi_read                  ),
		.I_write                            (spi_write                 ),
		.I_address                          (spi_address               ),
		.I_writedata                        (spi_wdata                 ),
		.O_readdata                         (ad9361_config_readdata    ),
		.O_waitrequest                      (ad9361_config_waitrequest ),
		.O_spi_clk                          (spi_clk                 ),
		.O_spi_csn                          (spi_csn                 ),
		.O_spi_mosi                         (spi_mosi                ),
		.I_spi_miso                         (spi_miso                ) 
    );

/*************************** (ila_txph ���Ƴ�) **************************************
    // ԭ��: dbg_hub ֻ�ܹ�һ��ʱ��, �ұ������������е��ڲ�ʱ�ӡ������������ ILA ʱ,
    //       Vivado ��� hub �ҵ�����"ĳ��"ILA ��ʱ����; ֮ǰ�� clk_200M(MMCM, ��Զ����),
    //       ɾ�� 200MHz �����Ĺ��� data_clk(rx_clk) ���� �� data_clk ���� AD9361,
    //       ��� hub ��ⲻ��, ���� ILA ȫ��ʧЧ(hardware manager �� "debug hub core was not detected")��
    //       ����ֻ�� ila_0(ʱ�� clk_100M), hub ��Ȼ�� clk_100M, ץ֡����Ҳ��˻ص�����
    //       ̽����������� ila_0 �е�(rx_ �� tx_ ���ź�����ͬ��, ���� CDC)��
***********************************************************************************/

endmodule
