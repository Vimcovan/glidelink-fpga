`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/09/17 18:39:56
// Design Name: 
// Module Name: time_sync
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


module time_sync#(
    parameter SYMBOL_PER_FRAME = 20,
    // LTF 64-sample energy gate: sum(|x|^2/1024) > ENERGY_THRESH: 750 -> rms > 110, 3x the noise energy at the gain the
    // AGC settles at near the sensitivity limit. A gate near the preamble level (2500, rms > 200) let the LTF1 peak fail and
    // LTF2 pass (+64 samples) when the energy fluctuated around it.
    // FFT window back-off: the LTF correlator coefficients (coe/ltf_cplx_bo8.coe) are the LTF matched filter
    // circularly shifted by BACKOFF+1 = 9 taps, so the sync fires 9 samples before LTF1 (LTF GI is cyclic: same
    // peak) and the output starting at the next sample puts every FFT window (LTF1, LTF2, data) 8 samples into
    // the CP/GI. The constant offset is absorbed by the channel estimate; it leaves margin for SFO drift.
    parameter ENERGY_THRESH = 750
)
(
    input logic           clk             ,
    input logic           rst_n           ,
    input logic[23:0]     s_in_tdata      ,
    input logic           s_in_tvalid     ,
    output logic          s_in_tready     ,
    output logic[23:0]    m_out_tdata     ,
    output logic          m_out_tvalid    ,
    output logic          m_out_tlast     ,
    input logic           m_out_tready
    );
    /* 计算滑窗能量值和互相关 */
    //计算瞬时功率
    logic[23:0] power_data_i;
    logic power_valid_i;
    logic power_ready_i;
    logic[49:0] power_data_o;
    logic power_valid_o;
    logic power_ready_o;
    complex_multiplier # (
        .A_W(12),
        .B_W(12)
    )
    u_cmpy_power (
        .clk(clk),
        .rst_n(rst_n),
        .s_a_tdata(power_data_i),
        .s_a_tvalid(power_valid_i),
        .s_a_tready(power_ready_i),
        .s_b_tdata({-power_data_i[23:12],power_data_i[11:0]}),
        .s_b_tvalid(power_valid_i),
        .s_b_tready(),
        .m_p_tdata(power_data_o),
        .m_p_tvalid(power_valid_o),
        .m_p_tready(power_ready_o)
    );
    //累加计算能量
    logic[17:0] energy_data_o;
    logic energy_valid_o;
    logic energy_ready_o;
    //功率乘法器 -> 滑窗之间补 4 级 en=valid&ready 寄存（仅在握手成交时前进）
    //作用：互相关通道到得最晚，合成整拍时能量支路要有空位存 beat，
    //      否则功率乘法器被堵、输入整拍被拒，DATA 相吞吐由 1/4 掉到约 1/7
    localparam SLACK_NUM = 4;
    logic [11:0] slack_d;
    logic        slack_v, slack_r;
    axis_forward_register # (.STAGE(SLACK_NUM), .WIDTH(12)) u_slack_energy (
        .clk(clk), .rst_n(rst_n),
        .s_in_tdata(power_data_o[21:10]), .s_in_tvalid(power_valid_o), .s_in_tready(power_ready_o),
        .m_out_tdata(slack_d), .m_out_tvalid(slack_v), .m_out_tready(slack_r)
    );
    slide_win_sum # (
        .WIDTH(12),
        .LENGTH(64)
    )
    u_sum (
        .clk(clk),
        .rst_n(rst_n),
        .s_in_tdata(slack_d),
        .s_in_tvalid(slack_v),
        .s_in_tready(slack_r),
        .m_out_tdata(energy_data_o),
        .m_out_tvalid(energy_valid_o),
        .m_out_tready(energy_ready_o)
    );
    // 计算互相关
    logic[23:0] xcorr_data_i;
    logic xcorr_valid_i;
    logic xcorr_ready_i;
    logic[15:0] xcorr_k1_data_o,xcorr_k2_data_o,xcorr_k3_data_o;
    logic xcorr_valid_o;
    logic xcorr_ready_o;
    logic xcorr_user_o;
    xcorr_cplx_block u_xcorr_k1 (
        .aresetn(rst_n),
        .aclk(clk),                                  // input logic aclk
        .s_axis_data_tvalid(xcorr_valid_i),      // input logic s_axis_data_tvalid
        .s_axis_data_tready(xcorr_ready_i),      // output logic s_axis_data_tready
        .s_axis_data_tdata(16'($signed(xcorr_data_i[11:0]))),        // input logic [15 : 0] s_axis_data_tdata
        .s_axis_config_tvalid(1'b1),  // input logic s_axis_config_tvalid
        .s_axis_config_tready(),  // output logic s_axis_config_tready
        .s_axis_config_tdata(8'd0),    // input logic [7 : 0] s_axis_config_tdata
        .m_axis_data_tvalid(xcorr_valid_o),      // output logic m_axis_data_tvalid
        .m_axis_data_tready(xcorr_ready_o),      // input wire m_axis_data_tready
        .m_axis_data_tuser(xcorr_user_o),        // output wire [0 : 0] m_axis_data_tuser
        .m_axis_data_tdata(xcorr_k1_data_o)        // output logic [15 : 0] m_axis_data_tdata
    );
    xcorr_cplx_block u_xcorr_k2 (
        .aresetn(rst_n),
        .aclk(clk),                                  // input logic aclk
        .s_axis_data_tvalid(xcorr_valid_i),      // input logic s_axis_data_tvalid
        .s_axis_data_tready(),      // output logic s_axis_data_tready
        .s_axis_data_tdata(16'($signed(xcorr_data_i[23:12]))),        // input logic [15 : 0] s_axis_data_tdata
        .s_axis_config_tvalid(1'b1),  // input logic s_axis_config_tvalid
        .s_axis_config_tready(),  // output logic s_axis_config_tready
        .s_axis_config_tdata(8'd1),    // input logic [7 : 0] s_axis_config_tdata
        .m_axis_data_tvalid(),      // output logic m_axis_data_tvalid
        .m_axis_data_tready(xcorr_ready_o),      // input wire m_axis_data_tready
        .m_axis_data_tuser(),        // output wire [0 : 0] m_axis_data_tuser
        .m_axis_data_tdata(xcorr_k2_data_o)        // output logic [15 : 0] m_axis_data_tdata
    );
    xcorr_cplx_block u_xcorr_k3 (
        .aresetn(rst_n),
        .aclk(clk),                                  // input logic aclk
        .s_axis_data_tvalid(xcorr_valid_i),      // input logic s_axis_data_tvalid
        .s_axis_data_tready(),      // output logic s_axis_data_tready
        .s_axis_data_tdata(16'($signed(xcorr_data_i[11:0]))+16'($signed(xcorr_data_i[23:12]))),        // input logic [15 : 0] s_axis_data_tdata
        .s_axis_config_tvalid(1'b1),  // input logic s_axis_config_tvalid
        .s_axis_config_tready(),  // output logic s_axis_config_tready
        .s_axis_config_tdata(8'd2),    // input logic [7 : 0] s_axis_config_tdata
        .m_axis_data_tvalid(),      // output logic m_axis_data_tvalid
        .m_axis_data_tready(xcorr_ready_o),      // input wire m_axis_data_tready
        .m_axis_data_tuser(),        // output wire [0 : 0] m_axis_data_tuser
        .m_axis_data_tdata(xcorr_k3_data_o)        // output logic [15 : 0] m_axis_data_tdata
    );
    

    logic[14:0] cmpy_real_data_i,cmpy_imag_data_i;
    logic[61:0] cmpy_data_o;
    logic cmpy_valid_o;
    logic cmpy_ready_o;
    logic[24:0] xcorr_abs_square;
    // two-point metric |X(n-1)|^2 + |X(n)|^2: the sampling phases of the two boards are unrelated, and at a half-sample
    // offset the LTF1 peak splits over two samples with half the energy each; at low SNR neither crossed the threshold
    // and the sync fell to LTF2 (+64) or missed the frame (6 dB: 3.4 % +64, 2.8 % missed -> 0). Fires on the same or
    // the next sample (within the 8-sample back-off). Saturated to 24 bits (the comparison below is signed).
    logic[24:0] xcorr_prev;
    logic[25:0] xcorr_pair;
    always_ff@(posedge clk or negedge rst_n) begin
        if(~rst_n) xcorr_prev <= '0;
        else if(cmpy_valid_o & cmpy_ready_o) xcorr_prev <= cmpy_data_o[30:6];
    end
    assign xcorr_pair = cmpy_data_o[30:6] + xcorr_prev;
    assign xcorr_abs_square = (xcorr_pair[25:24] != 2'b00) ? 25'h0FFFFFF : xcorr_pair[24:0];
    assign cmpy_real_data_i = 15'($signed(xcorr_k1_data_o[13:0])) - 15'($signed(xcorr_k2_data_o[13:0]));
    assign cmpy_imag_data_i = 15'($signed(xcorr_k3_data_o[13:0])) - 15'($signed(xcorr_k1_data_o[13:0])) - 15'($signed(xcorr_k2_data_o[13:0]));
    complex_multiplier # (
        .A_W(15),
        .B_W(15)
    )
    u_cmpy_xcorr (
        .clk(clk),
        .rst_n(rst_n),
        .s_a_tdata({cmpy_imag_data_i,cmpy_real_data_i}),
        .s_a_tvalid(xcorr_valid_o & xcorr_user_o),
        .s_a_tready(xcorr_ready_o),
        .s_b_tdata({-cmpy_imag_data_i,cmpy_real_data_i}),
        // 注意：B 口的 valid 必须保持 xcorr_valid_o，不要与 A 口(valid & user)合并成同一信号。
        // 实测把 B 也门控成 valid&user 后，FIR 的 s_axis_data_tready 会被 user 卡住，
        // 而 user 又来自 FIR 自己的输出，形成互锁 —— 无反压时就直接死锁（time_sync 一个样点都发不出）。
        // A 口这里起的是"闸门"作用：FIR 自由跑，乘法器只在 user=1 的拍采样 A，非 user 拍跳过。
        .s_b_tvalid(xcorr_valid_o),
        .s_b_tready(),
        .m_p_tdata(cmpy_data_o),
        .m_p_tvalid(cmpy_valid_o),
        .m_p_tready(cmpy_ready_o)
    );
    //FIFO缓存输入数据
    logic[23:0] fifo_data_i;
    logic fifo_valid_i;
    logic fifo_ready_i;
    logic[23:0] fifo_data_o;
    logic fifo_valid_o;
    logic fifo_ready_o;
    axis_fifo # (
        .DEPTH(128),
        .WIDTH(24)
    )
    u_fifo (
        .clk(clk),
        .rst_n(rst_n),
        .s_in_tdata(fifo_data_i),
        .s_in_tvalid(fifo_valid_i),
        .s_in_tready(fifo_ready_i),
        .m_out_tdata(fifo_data_o),
        .m_out_tvalid(fifo_valid_o),
        .m_out_tready(fifo_ready_o)
    );
    /* 互联 */
    //输入广播至能量计算、互相关计算、FIFO
    axis_broadcast # (
        .WIDTH(24),
        .CHANNEL(3)
    )
    u_broadcast (
        .clk(clk),
        .rst_n(rst_n),
        .s_in_tdata(s_in_tdata),
        .s_in_tvalid(s_in_tvalid),
        .s_in_tready(s_in_tready),
        .m_out_tdata({power_data_i,xcorr_data_i,fifo_data_i}),
        .m_out_tvalid({power_valid_i,xcorr_valid_i,fifo_valid_i}),
        .m_out_tready({power_ready_i,xcorr_ready_i,fifo_ready_i})
    );
    //输出对齐能量计算、互相关计算、FIFO
    logic align_valid;
    logic align_ready;
    logic[24:0] align_energy,align_xcorr,align_iq_sample;
    axis_combine # (
        .CHANNEL(3),
        .MAX_WIDTH(25)
    )
    u_align(
        .clk(clk),
        .rst_n(rst_n),
        .s_in_tdata({25'(energy_data_o),xcorr_abs_square,25'(fifo_data_o)}),
        .s_in_tvalid({energy_valid_o,cmpy_valid_o,fifo_valid_o}),
        .s_in_tready({energy_ready_o,cmpy_ready_o,fifo_ready_o}),
        .m_out_tdata({align_energy,align_xcorr,align_iq_sample}),
        .m_out_tvalid(align_valid),
        .m_out_tready(align_ready)
    );
    //状态机控制输出
    localparam DATA_LENGTH = 80*SYMBOL_PER_FRAME + 2*64;     // LTF1 + LTF2 + data symbols (with CP)
    logic[$clog2(DATA_LENGTH)-1:0] sample_cnt;
    logic align_fire;
    assign align_fire = align_valid & align_ready;
    logic synced;
    // LTF xcorr gate: |xcorr|^2 > 0.375*energy (was 0.5). RTL ratio ~= 0.81*rho2; 0.5 (rho2~0.62) sat right at the
    // two-board LTF1 peak (sampling-phase offset splits the peak) -> LTF1 missed, locked on LTF2 (+64). 0.375 = 1/4+1/8 (shift-add).
    assign synced = (($signed(align_xcorr) > (($signed(align_energy)>>>2) + ($signed(align_energy)>>>3))) &
                    ($signed(align_energy) > ENERGY_THRESH));
    typedef enum logic[1:0] { 
        IDLE = 2'b00,
        FIRST_LTF = 2'b01,
        DATA = 2'b10
    } state_t;
    state_t current_state,next_state;
    always_ff@(posedge clk or negedge rst_n) begin
        if(~rst_n) current_state <= IDLE;
        else current_state <= next_state;
    end
    always_comb begin
        next_state = current_state;
        case(next_state)
            IDLE:
                if(align_fire&synced) next_state = DATA;     // LTF1 starts at the next sample
            FIRST_LTF:
                next_state = IDLE;          // not used any more (LTF1 is output now)
            DATA:
                if(align_fire&&(sample_cnt==DATA_LENGTH-1)) next_state = IDLE;
        endcase
    end
    always_ff@(posedge clk or negedge rst_n) begin
        if(~rst_n) sample_cnt <= 'd0;
        else if(align_fire&(current_state inside {FIRST_LTF,DATA})) begin
            if(sample_cnt==DATA_LENGTH-1) sample_cnt <= 'd0;
            else sample_cnt <= sample_cnt + 1;
        end
    end
    always_comb begin
        m_out_tlast = (sample_cnt==DATA_LENGTH-1);
        case(current_state)
            IDLE,FIRST_LTF: begin
                m_out_tdata = 'd0;
                m_out_tvalid = 1'b0;
                align_ready = 1'b1;
            end
            DATA: begin
                m_out_tdata = align_iq_sample[23:0];
                m_out_tvalid = align_valid;
                align_ready = m_out_tready;
            end
        endcase
    end
endmodule
