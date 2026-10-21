`timescale 1ns / 1ps
// synchronizer_top with a file stimulus: one sample every 5 clocks (20 MSPS at 100 MHz) into a FIFO the DUT pulls from;
// logs the input index consumed when time_sync enters DATA, and every DDS configuration value
module tb_sync;
    string STIM = "stim.txt";
    string LOG  = "log.txt";
    localparam int NMAX = 200000;
    logic clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    logic [23:0] mem [0:NMAX-1];
    int nsamp = 0;
    integer fh;
    initial begin
        void'($value$plusargs("STIM=%s", STIM));
        void'($value$plusargs("LOG=%s", LOG));
        $readmemh(STIM, mem);
        while (nsamp < NMAX && !$isunknown(mem[nsamp])) nsamp++;
        fh = $fopen(LOG, "w");
        $display("TB: %0d samples", nsamp);
        #200 rst_n = 1;
    end
    logic [23:0] fifo [0:16383];
    int wr = 0, rd = 0, tick = 0;
    logic in_valid, in_ready;
    assign in_valid = (wr != rd);
    always @(posedge clk) if (rst_n) begin
        if (in_valid && in_ready) rd <= rd + 1;
        tick <= (tick == 4) ? 0 : tick + 1;
        if (tick == 4 && wr < nsamp) begin fifo[wr % 16384] <= mem[wr]; wr <= wr + 1; end
        if (wr == nsamp && rd == nsamp) begin $fclose(fh); $display("TB: done"); $finish; end
    end
    logic [23:0] out_data, in_data; logic out_valid, out_last;
    assign in_data = fifo[rd % 16384];
    synchronizer_top #(.NSYM(20)) dut (
        .clk, .rst_n, .s_in_tdata(in_data), .s_in_tvalid(in_valid), .s_in_tready(in_ready),
        .m_out_tdata(out_data), .m_out_tvalid(out_valid), .m_out_tlast(out_last), .m_out_tready(1'b1));
    // first output beat of time_sync per frame = it entered DATA; the DDS configuration (CFO estimate)
    logic ts_v, ts_l, ts_r, in_frame = 0, dds_v;
    logic signed [15:0] cfo_ph;
    assign ts_v = dut.ts_ovalid;
    assign ts_r = dut.ts_oready;
    assign ts_l = dut.ts_olast;
    assign dds_v = dut.frame_detection_inst.dds_valid_i;
    assign cfo_ph = dut.frame_detection_inst.cfo_cordic_data_o[39:24];
    always @(posedge clk) if (rst_n) begin
        if (ts_v && ts_r) begin
            if (!in_frame) $fwrite(fh, "TS %0d\n", rd);
            in_frame <= !ts_l;
        end
        if (dds_v) $fwrite(fh, "CFO %0d %0d\n", rd, cfo_ph);
    end
endmodule
