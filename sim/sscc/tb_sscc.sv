`timescale 1ns / 1ps
// SSCC TX -> AWGN -> RX loopback. Plusargs: +FRAMES=n +SNR=dB (Es/N0, 0 = no noise) +DUMP=1 (TX bytes / symbols
// of the first frame to tx_bytes.txt / tx_syms.txt, LDPC decoder input LLRs / per-codeword outputs to dec_llr.txt /
// dec_out.txt, for sscc_ref.py)
module tb_sscc;
    localparam int PAY_BYTES = 16360, FRAME_SYMS = 32768;
    logic clk = 0, rst_n = 0;
    always #5 clk = ~clk;

    int   frames = 2;
    real  snr = 0.0;
    int   dump = 0;
    byte  txb [][];
    initial begin
        void'($value$plusargs("FRAMES=%d", frames));
        void'($value$plusargs("SNR=%f", snr));
        void'($value$plusargs("DUMP=%d", dump));
        txb = new[frames];
        foreach (txb[f]) begin
            txb[f] = new[PAY_BYTES];
            foreach (txb[f][i]) txb[f][i] = $urandom;
        end
    end

    // ---------------- TX
    logic [7:0]  s_tdata; logic s_tvalid, s_tready;
    logic [23:0] t_tdata; logic t_tlast, t_tvalid, t_tready;
    sscc_tx #(.PAY_BYTES(PAY_BYTES), .FRAME_SYMS(FRAME_SYMS)) dut_tx (
        .clk, .rst_n, .s_tdata, .s_tvalid, .s_tready,
        .m_tdata(t_tdata), .m_tlast(t_tlast), .m_tvalid(t_tvalid), .m_tready(t_tready));
    int bf = 0, bi = 0;
    assign s_tvalid = rst_n && (bf < frames);
    assign s_tdata  = (bf < frames) ? txb[bf][bi] : 8'h00;
    always @(posedge clk) if (s_tvalid && s_tready) begin
        if (bi == PAY_BYTES - 1) begin bi <= 0; bf <= bf + 1; end else bi <= bi + 1;
    end
    // collect symbols
    logic [24:0] symq [$];
    int nsym = 0, ntl = 0, fd_s;
    assign t_tready = 1'b1;
    always @(posedge clk) if (t_tvalid && t_tready) begin
        symq.push_back({t_tlast, t_tdata});
        if (dump && nsym < FRAME_SYMS) $fdisplay(fd_s, "%0d %0d", $signed(t_tdata[11:0]), $signed(t_tdata[23:12]));
        nsym++;
        if (t_tlast) begin
            ntl++;
            if (nsym != ntl * FRAME_SYMS) $display("ERROR tlast at symbol %0d", nsym);
        end
    end

    // ---------------- channel + RX (PHY pacing: 48 symbols back to back every 400 clocks)
    logic [23:0] r_tdata; logic r_tlast, r_fire;
    logic [31:0] seq, ovf, cw_fail, cw_iters; logic last_buf;
    logic [12:0] rd_addr; logic [31:0] rd_q;
    sscc_rx #(.PAY_BYTES(PAY_BYTES), .FRAME_SYMS(FRAME_SYMS)) dut_rx (
        .clk, .rst_n, .s_tdata(r_tdata), .s_tlast(r_tlast), .s_fire(r_fire),
        .seq, .last_buf, .fifo_ovf(ovf), .cw_fail, .cw_iters, .rd_addr, .rd_q);
    // decoder I/O dump
    int fd_l, fd_o, obits = 0;
    string ob;
    logic signed [6:0] d_llr; logic d_v, d_r, d_bit, d_bv, d_done, d_ok; logic [4:0] d_it;
    assign d_llr = dut_rx.u_dec.s_llr;  assign d_v = dut_rx.u_dec.s_valid; assign d_r = dut_rx.u_dec.s_ready;
    assign d_bit = dut_rx.u_dec.m_bit;  assign d_bv = dut_rx.u_dec.m_valid;
    assign d_done = dut_rx.u_dec.cw_done; assign d_ok = dut_rx.u_dec.cw_ok; assign d_it = dut_rx.u_dec.cw_iter;
    logic st_ok; logic [4:0] st_it;
    always @(posedge clk) if (dump && rst_n) begin
        if (d_v && d_r) $fdisplay(fd_l, "%0d", d_llr);
        if (d_done) begin st_ok = d_ok; st_it = d_it; ob = ""; end
        if (d_bv) begin
            ob = {ob, d_bit ? "1" : "0"};
            if (ob.len() == 1296) $fdisplay(fd_o, "%0d %0d %s", st_ok, st_it, ob);
        end
    end
    real sigma;
    int  seed = 7;
    function automatic logic [11:0] noisy(input logic [11:0] v);
        real x;
        int  r;
        x = $signed(v) + ((snr > 0) ? sigma * $dist_normal(seed, 0, 100000) / 100000.0 : 0.0);
        r = (x >= 0) ? int'(x + 0.5) : -int'(-x + 0.5);
        if (r > 2047) r = 2047; if (r < -2047) r = -2047;
        return 12'(r);
    endfunction
    initial begin
        logic [24:0] s;
        int k;
        sigma = (snr > 0) ? $sqrt(42.0 * 158.0 * 158.0 / 2.0 / (10.0 ** (snr / 10.0))) : 0.0;
        r_fire = 0; r_tdata = 0; r_tlast = 0;
        if (dump) begin
            fd_s = $fopen("tx_syms.txt", "w");
            fd_l = $fopen("dec_llr.txt", "w");
            fd_o = $fopen("dec_out.txt", "w");
            begin
                int fd_b = $fopen("tx_bytes.txt", "w");
                foreach (txb[0][i]) $fdisplay(fd_b, "%02x", txb[0][i]);
                $fclose(fd_b);
            end
        end
        repeat (20) @(posedge clk); rst_n = 1;
        $display("SSCC sim: %0d frames, SNR %0.1f dB (sigma %0.1f per dimension)", frames, snr, sigma);
        k = 0;
        forever begin
            @(posedge clk);
            r_fire <= 0;
            if (k < 48) begin
                if (symq.size() > 0) begin
                    s = symq.pop_front();
                    r_tdata <= {noisy(s[23:12]), noisy(s[11:0])}; r_tlast <= s[24]; r_fire <= 1; k++;
                end
            end else if (++k == 400) k = 0;
        end
    end

    // ---------------- check every completed packet
    int checked = 0, total_err = 0;
    initial begin
        int last_seq = 0, errs, f;
        logic [31:0] w;
        rd_addr = 0;
        wait (rst_n);
        while (checked < frames) begin
            @(posedge clk);
            if (seq != last_seq) begin
                last_seq = seq; f = seq - 1; errs = 0;
                for (int i = 0; i < PAY_BYTES / 4; i++) begin
                    rd_addr <= {last_buf, 12'(i)};
                    @(posedge clk); @(posedge clk); #1;
                    w = rd_q;
                    for (int b = 0; b < 4; b++) if (w[8*b +: 8] !== txb[f][4*i + b]) errs++;
                end
                $display("frame %0d: %0d byte errors of %0d (fifo overflow %0d) at %0t", f, errs, PAY_BYTES, ovf, $time);
                total_err += errs; checked++;
            end
        end
        if (dump) begin $fclose(fd_s); $fclose(fd_l); $fclose(fd_o); end
        $display("SSCC_RESULT frames %0d byte_errors %0d tx_symbols %0d codewords_failed %0d iterations %0d",
                 frames, total_err, nsym, cw_fail, cw_iters);
        $finish;
    end
    initial begin #50ms; $display("SSCC_RESULT TIMEOUT seq %0d", seq); $finish; end
endmodule
