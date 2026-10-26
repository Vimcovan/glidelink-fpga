// ***************************************************************************
// ***************************************************************************
// Copyright 2011(c) Analog Devices, Inc.
// 
// All rights reserved.
// 
// Redistribution and use in source and binary forms, with or without modification,
// are permitted provided that the following conditions are met:
//     - Redistributions of source code must retain the above copyright
//       notice, this list of conditions and the following disclaimer.
//     - Redistributions in binary form must reproduce the above copyright
//       notice, this list of conditions and the following disclaimer in
//       the documentation and/or other materials provided with the
//       distribution.
//     - Neither the name of Analog Devices, Inc. nor the names of its
//       contributors may be used to endorse or promote products derived
//       from this software without specific prior written permission.
//     - The use of this software may or may not infringe the patent rights
//       of one or more patent holders.  This license does not release you
//       from the requirement that you obtain separate licenses from these
//       patent holders to use this software.
//     - Use of the software either in source or binary form, must be run
//       on or directly connected to an Analog Devices Inc. component.
//    
// THIS SOFTWARE IS PROVIDED BY ANALOG DEVICES "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
// INCLUDING, BUT NOT LIMITED TO, NON-INFRINGEMENT, MERCHANTABILITY AND FITNESS FOR A
// PARTICULAR PURPOSE ARE DISCLAIMED.
//
// IN NO EVENT SHALL ANALOG DEVICES BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
// EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, INTELLECTUAL PROPERTY
// RIGHTS, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR 
// BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
// STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF 
// THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
// ***************************************************************************
// ***************************************************************************
//
// Provenance: derived from Analog Devices HDL (github.com/analogdevicesinc/hdl, e.g. tag hdl_2015_r1:
//   library/axi_ad9361/axi_ad9361_dev_if.v and library/common/ad_lvds_{in,out}.v), which were merged into one
//   module (primitives instantiated directly) in github.com/lzk2211/Zedboard_AD9361_radar (axi_ad9361_dev_if.v,
//   without the notice above), then adapted in the MiLianKe AD9361 example and in this project (LVDS 1R1T,
//   PYNQ-ZU pinout, interface to the OFDM PHY). The notice above is restored as required by the ADI license.
// ***************************************************************************

module ad9361_lvds_mode #(
    // TX data pairs inverted when tx_swap = 1 (bit k = pair Dk): for a daughter card with these pairs' P/N
    // swapped on its PCB (the third-party AD9361 card: D5; FMCOMMS3: none). Each pair carries sample bits k + 6
    // and k of I and Q, so the whole 6-bit half-words are XORed with the mask.
    parameter [5:0]                     TX_SWAP_PAIRS = 6'b100000
) (
    input                               clk_100M                   ,   // (本模块内部未使用, 仅为兼容例化)
    input                               rst_n                      ,
    input                               tx_swap                    ,   // async (board switch)
    // physical interface (receive)
    input                               rx_clk_in_p                ,
    input                               rx_clk_in_n                ,
    input                               rx_frame_in_p              ,
    input                               rx_frame_in_n              ,
    input              [   5: 0]        rx_data_in_p               ,
    input              [   5: 0]        rx_data_in_n               ,
    
	// physical interface (transmit)
    output                              tx_clk_out_p               ,
    output                              tx_clk_out_n               ,
    output                              tx_frame_out_p             ,
    output                              tx_frame_out_n             ,
    output             [   5: 0]        tx_data_out_p              ,
    output             [   5: 0]        tx_data_out_n              ,

	// receive data path interface
    output reg                          adc_valid                  ,
    output reg         [  11: 0]        adc_data_i                ,
    output reg         [  11: 0]        adc_data_q                ,
    output reg                          adc_status                 ,

	// transmit data path interface
    input                               dac_valid                  ,
    input              [  11: 0]        dac_data_i                ,
    input              [  11: 0]        dac_data_q                ,
    output reg                          dac_ready                ,

    //others
    output                              data_clk                   ,

    //---- observe-only debug outputs: ODDRE1 之前的并行半字与帧, 不参与任何逻辑 ----
    output              [   5: 0]        dbg_tx_data_p              ,
    output              [   5: 0]        dbg_tx_data_n              ,
    output                              dbg_tx_frame
);

	// internal registers
    reg                [   5: 0]        rx_data_n                 ='d0;
    reg                                 rx_frame_n                ='d0;
    reg                [  11: 0]        rx_data                   ='d0;
    reg                [   1: 0]        rx_frame                  ='d0;
    reg                [  11: 0]        rx_data_d                 ='d0;
    reg                [   1: 0]        rx_frame_d                ='d0;
    reg                                 rx_error               ='d0;
    reg                                 rx_valid               ='d0;
    reg                [  11: 0]        rx_data_i              ='d0;
    reg                [  11: 0]        rx_data_q              ='d0;
    reg                [   2: 0]        tx_data_cnt               ='d0;
    reg                [   5: 0]        tx_data_i_d              ='d0;
    reg                [   5: 0]        tx_data_q_d              ='d0;
    reg                                 tx_frame                  ='d0;
    reg                [   5: 0]        tx_data_p                 ='d0;
    reg                [   5: 0]        tx_data_n                 ='d0;
	// internal signals
    wire               [   3: 0]        rx_frame_s                  ;
    wire               [   1: 0]        tx_data_sel_s               ;
    wire               [   5: 0]        rx_data_ibuf_s              ;
    wire               [   5: 0]        rx_data_p_s                 ;
    wire               [   5: 0]        rx_data_n_s                 ;
    wire                                rx_frame_ibuf_s             ;
    wire                                rx_frame_p_s                ;
    wire                                rx_frame_n_s                ;
    wire               [   5: 0]        tx_data_oddr_s              ;
    wire                                tx_frame_oddr_s             ;
    wire                                tx_clk_oddr_s               ;
    wire                                data_clk_ibuff              ;
    genvar          					 l_inst						;


/************************************************  接收链路 ***************************************************/
/*************************************************************************************************************/

/**************************************** rx_clk ******************************************************/
    //rx_clk  ibuf -> bufg  差分转单端 -> 全局时钟缓存
    IBUFGDS IBUFGDS_data_clk (
    .O                                  (data_clk_ibuff            ),
    .I                                  (rx_clk_in_p               ),
    .IB                                 (rx_clk_in_n               ));

    BUFG BUFG_data_clk (
    .O                                  (data_clk                  ),
    .I                                  (data_clk_ibuff            ));


/**************************************** rx_data ******************************************************/
    // rx_data  interface, ibuf -> iddr  差分转单端 -> 双边沿转单边沿
    generate
		for (l_inst=0;l_inst<=5;l_inst=l_inst+1) begin:g_rx_data
			IBUFDS i_rx_data_ibuf(
            .I                                  (rx_data_in_p[l_inst]      ),
            .IB                                 (rx_data_in_n[l_inst]      ),
            .O                                  (rx_data_ibuf_s[l_inst]    ));
            
            IDDRE1 #(
            .DDR_CLK_EDGE                       ("SAME_EDGE_PIPELINED"     ),// IDDRE1 mode (OPPOSITE_EDGE, SAME_EDGE, SAME_EDGE_PIPELINED)
            .IS_CB_INVERTED                     (1'b1                      ),// Optional inversion for CB
            .IS_C_INVERTED                      (1'b0                      ) // Optional inversion for C
            )
            IDDRE1_i_rx_data (
            .Q1                                 (rx_data_p_s[l_inst]       ),// 1-bit output: Registered parallel output 1
            .Q2                                 (rx_data_n_s[l_inst]       ),// 1-bit output: Registered parallel output 2
            .C                                  (data_clk                  ),// 1-bit input: High-speed clock
            .CB                                 (data_clk                  ),// 1-bit input: Inversion of High-speed clock C
            .D                                  (rx_data_ibuf_s[l_inst]   ),// 1-bit input: Serial Data Input
            .R                                  (1'b0                      ) // 1-bit input: Active-High Async Reset
            );
        end
	endgenerate

/**************************************** rx_frame ******************************************************/
    // rx_frame  interface, ibuf -> iddr  差分转单端 -> 双边沿转单边沿

    IBUFDS i_rx_frame_ibuf (
    .I                                  (rx_frame_in_p             ),
    .IB                                 (rx_frame_in_n             ),
    .O                                  (rx_frame_ibuf_s            )          
    );

    IDDRE1 #(
    .DDR_CLK_EDGE                       ("SAME_EDGE_PIPELINED"     ),// IDDRE1 mode (OPPOSITE_EDGE, SAME_EDGE, SAME_EDGE_PIPELINED)
    .IS_CB_INVERTED                     (1'b1                      ),// Optional inversion for CB
    .IS_C_INVERTED                      (1'b0                      ) // Optional inversion for C
    )
    IDDRE1_i_rx_frame (
    .Q1                                 (rx_frame_p_s              ),// 1-bit output: Registered parallel output 1
    .Q2                                 (rx_frame_n_s              ),// 1-bit output: Registered parallel output 2
    .C                                  (data_clk                  ),// 1-bit input: High-speed clock
    .CB                                 (data_clk                  ),// 1-bit input: Inversion of High-speed clock C
    .D                                  (rx_frame_ibuf_s          ),// 1-bit input: Serial Data Input
    .R                                  (1'b0                      ) // 1-bit input: Active-High Async Reset
    ); 

/**************************************** 接收链路数据拼接 ******************************************************/
    //receive data path interface
    assign rx_frame_s = {rx_frame_d, rx_frame};
    always @(posedge data_clk)
        begin
            rx_data_n   <= rx_data_n_s;
            rx_frame_n  <= rx_frame_n_s;
            rx_data     <= {rx_data_n,rx_data_p_s};
            rx_frame    <= {rx_frame_n,rx_frame_p_s};
            rx_data_d   <= rx_data;
            rx_frame_d  <= rx_frame;
        end
    //receive data path for single rf, frame is expected to qualify i/q msb only
    always @(posedge data_clk)
        begin
            rx_error	<= ((rx_frame_s==4'b1100)||(rx_frame_s==4'b0011))?1'b0:1'b1;
        if (rx_frame_s==4'b1100) begin
                rx_data_i <= {rx_data_d[11:6],rx_data[11:6]};
                rx_data_q <= {rx_data_d[ 5:0],rx_data[ 5:0]};
                rx_valid <= 1'b1;
        end
        else rx_valid <= 1'b0;
        end
    //receive data path mux
    always @(posedge data_clk) begin
        adc_valid   <= rx_valid;
        adc_data_i <= rx_data_i;
        adc_data_q <= rx_data_q;
        adc_status  <= ~rx_error;
    end

/*************************************************************************************************************/
/*************************************************************************************************************/


/************************************************  发送链路 ***************************************************/
/*************************************************************************************************************/

/**************************************** tx_clk ******************************************************/
    // tx_clk, oddr -> obuf  单边沿转双边沿 -> 单端转差分
    ODDRE1 #(
    .IS_C_INVERTED                      (1'b0                      ),// Optional inversion for C
    .IS_D1_INVERTED                     (1'b0                      ),// Unsupported, do not use
    .IS_D2_INVERTED                     (1'b0                      ),// Unsupported, do not use
    .SIM_DEVICE                         ("ULTRASCALE_PLUS"         ),// Set the device version for simulation functionality (ULTRASCALE,
                                                                     // ULTRASCALE_PLUS, ULTRASCALE_PLUS_ES1, ULTRASCALE_PLUS_ES2)
    .SRVAL                              (1'b0                      ) // Initializes the ODDRE1 Flip-Flops to the specified value (1'b0, 1'b1)
    )
    ODDRE1_i_tx_clk (
    .Q                                  (tx_clk_oddr_s             ),// 1-bit output: Data output to IOB
    .C                                  (data_clk                  ),// 1-bit input: High-speed clock input
    .D1                                 (1'b0                      ),// 1-bit input: Parallel data input 1
    .D2                                 (1'b1                      ),// 1-bit input: Parallel data input 2
    .SR                                 (1'b0                      ) // 1-bit input: Active-High Async Reset
    );
	
    OBUFDS i_tx_clk_obuf (
    .I                                  (tx_clk_oddr_s             ),
    .O                                  (tx_clk_out_p              ),
    .OB                                 (tx_clk_out_n             ));

/**************************************** tx_data ******************************************************/
    // tx_data, oddr -> obuf  单边沿转双边沿 -> 单端转差分
    generate
		for (l_inst=0;l_inst<=5;l_inst=l_inst+1) begin: g_tx_data
            ODDRE1 #(
            .IS_C_INVERTED                      (1'b0                      ),// Optional inversion for C
            .IS_D1_INVERTED                     (1'b0                      ),// Unsupported, do not use
            .IS_D2_INVERTED                     (1'b0                      ),// Unsupported, do not use
            .SIM_DEVICE                         ("ULTRASCALE_PLUS"         ),// Set the device version for simulation functionality (ULTRASCALE,
                                                                             // ULTRASCALE_PLUS, ULTRASCALE_PLUS_ES1, ULTRASCALE_PLUS_ES2)
            .SRVAL                              (1'b0                      ) // Initializes the ODDRE1 Flip-Flops to the specified value (1'b0, 1'b1)
            )
            ODDRE1_i_tx_data (
            .Q                                  (tx_data_oddr_s[l_inst]    ),// 1-bit output: Data output to IOB
            .C                                  (data_clk                  ),// 1-bit input: High-speed clock input
            .D1                                 (tx_data_p[l_inst]         ),// 1-bit input: Parallel data input 1
            .D2                                 (tx_data_n[l_inst]         ),// 1-bit input: Parallel data input 2
            .SR                                 (1'b0                      ) // 1-bit input: Active-High Async Reset
            );

			OBUFDS i_tx_data_obuf (
            .I                                  (tx_data_oddr_s[l_inst]    ),
            .O                                  (tx_data_out_p[l_inst]     ),
            .OB                                 (tx_data_out_n[l_inst])    );
		end 
	endgenerate

/**************************************** tx_frame ******************************************************/
    // tx_frame, oddr -> obuf  单边沿转双边沿 -> 单端转差分
    ODDRE1 #(
    .IS_C_INVERTED                      (1'b0                      ),// Optional inversion for C
    .IS_D1_INVERTED                     (1'b0                      ),// Unsupported, do not use
    .IS_D2_INVERTED                     (1'b0                      ),// Unsupported, do not use
    .SIM_DEVICE                         ("ULTRASCALE_PLUS"         ),// Set the device version for simulation functionality (ULTRASCALE,
                                                                     // ULTRASCALE_PLUS, ULTRASCALE_PLUS_ES1, ULTRASCALE_PLUS_ES2)
    .SRVAL                              (1'b0                      ) // Initializes the ODDRE1 Flip-Flops to the specified value (1'b0, 1'b1)
    )
    ODDRE1_i_tx_frame (
    .Q                                  (tx_frame_oddr_s           ),// 1-bit output: Data output to IOB
    .C                                  (data_clk                  ),// 1-bit input: High-speed clock input
    .D1                                 (tx_frame                  ),// 1-bit input: Parallel data input 1
    .D2                                 (tx_frame                  ),// 1-bit input: Parallel data input 2
    .SR                                 (1'b0                      ) // 1-bit input: Active-High Async Reset
    );

    OBUFDS i_tx_frame_obuf (
    .I                                  (tx_frame_oddr_s           ),
    .O                                  (tx_frame_out_p            ),
    .OB                                 (tx_frame_out_n)           );

/**************************************** 发送链路数据拼接 ******************************************************/
    // transmit data path mux (reverse of what receive does above)
    // the count simply selets the data muxing on the ddr outputs
    //------------------data format 1t--------------------------
    //valid :~~~ ___ ~~~ ___ ~~~ ___ ~~~ ___                  //
    //datai :i1      i1      i1      i1                       //
    //dataq :q1      q1      q1      q1                       //
    //cnt   :    100 101 100 101 100 101 100                  //
    assign tx_data_sel_s = {dac_valid,dac_ready};
    (* ASYNC_REG = "TRUE" *) reg [1:0] tx_swap_s = 2'b00;     // static switch: two-flop synchroniser
    reg [5:0] tx_inv = 6'd0;
    always @(posedge data_clk) begin
        tx_swap_s <= {tx_swap_s[0], tx_swap};
        tx_inv    <= tx_swap_s[1] ? TX_SWAP_PAIRS : 6'd0;
    end
    always @(posedge data_clk)
        begin
            if(rst_n) dac_ready <= 1'b1;
            case (tx_data_sel_s)
                2'b11:
                begin
                    tx_frame  <= 1'b1;
                    tx_data_p <= dac_data_i[11:6] ^ tx_inv;
                    tx_data_n <= dac_data_q[11:6] ^ tx_inv;
                    tx_data_i_d <= dac_data_i[5:0] ^ tx_inv;
                    tx_data_q_d <= dac_data_q[5:0] ^ tx_inv;
                    dac_ready <= 1'b0;
                end
                2'b10,2'b00:
                begin
                    tx_frame  <= 1'b0;
                    tx_data_p <= tx_data_i_d;
                    tx_data_n <= tx_data_q_d;
                    dac_ready <= 1'b1;
                end
                2'b01:
                begin
                    tx_frame  <= 1'b0;
                    tx_data_p <= 6'd0;
                    tx_data_n <= 6'd0;
                end
            endcase
        end
/*************************************************************************************************************/
/*************************************************************************************************************/	

    //---- observe-only debug taps (不驱动任何功能逻辑) ----
    assign dbg_tx_data_p = tx_data_p;
    assign dbg_tx_data_n = tx_data_n;
    assign dbg_tx_frame  = tx_frame;

endmodule

