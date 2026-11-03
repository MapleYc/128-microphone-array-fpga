`timescale 1ns/1ps
`default_nettype none
// Joint hardware entry. Runtime behavior comes from the explicitly generated
// ROM/RAM images. A controller self-test image is not a live audio application.
module meeting_soc_clocks(
    input wire sys_clk_27,reset_n,
    output wire cpu_clk,audio_clk,locked,
    output wire [2:0] locks
);
    wire sys27,audio_locked,cpu_raw,cpu_locked;
    wire [1:0] audio_locks;
    meeting_audio_clocks u_audio(.ref_clk_27(sys_clk_27),.reset_n(reset_n),
        .sys_clk(sys27),.audio_clk(audio_clk),.locked(audio_locked),.locks(audio_locks));
    // 27 MHz * 14 * 2 = 756 MHz VCO; /12 = exactly 63 MHz.
    pango_audio_gpll #(.INPUT_MHZ(27.0),.INPUT_DIV(1),.FEEDBACK_MULTIPLY(14),.OUTPUT_DIV(12))
        u_cpu_pll(.clk_in(sys27),.rst(!reset_n),.clk_out(cpu_raw),.locked(cpu_locked));
    GTP_CLKBUFG u_cpu_buf(.CLKIN(cpu_raw),.CLKOUT(cpu_clk));
    assign locks={cpu_locked,audio_locks};
    assign locked=audio_locked && cpu_locked;
endmodule

module meeting_soc_mini200(
    input wire sys_clk_27,reset_n,uart_rx,
    input wire [63:0] pdm_data,
    output wire [7:0] mic_clk,
    output wire uart_tx,
    output wire [4:0] led
);
    wire cpu_clk,audio_clk,locked,cpu_reset_n;
    wire [2:0] locks;
    meeting_soc_clocks clocks(.sys_clk_27(sys_clk_27),.reset_n(reset_n),
        .cpu_clk(cpu_clk),.audio_clk(audio_clk),.locked(locked),.locks(locks));
    reset_release u_cpu_reset(.clk(cpu_clk),.arst_n(reset_n && locked),.rst_n(cpu_reset_n));
    wire qv,qw,qr,rv,re,rl,rr;
    wire [31:0] qa,qd,rd;
    wire [3:0] qs,gpio;
    wire [2:0] qlen;
    wire done,fatal,trap;
    wire [31:0] exit_status,applied_control;
    wire cam_scl_unused,cam_sda_unused;
    // P128 hardware probe 2026-10-23: DATA1 raw phases 2/3 stuck low.
    // Reorder maps these to canonical 3/2. Preserve raw acquisition diagnostics.
    array128_full_control_soc #(.CLOCK_HZ(63000000),.UART_BAUD(921600),.ENABLE_CAMERA(0),.MONO_16K(1),.ENABLE_SPATIAL(1),
        .SPATIAL_CHANNEL_MASK(128'hfffffffffffffffffffffffffffffff3)) soc(
        .clk(cpu_clk),.rst_n(cpu_reset_n),.uart_rx_i(uart_rx),.gpio_i(4'b0),
        .uart_tx_o(uart_tx),.gpio_o(gpio),.audio_clk(audio_clk),.pdm_data_i(pdm_data),.mic_clk_o(mic_clk),
        .pcm_ready_i(1'b1),.pcm_valid_o(),.pcm_data_o(),.pcm_channel_o(),.pcm_frame_o(),
        .applied_control_o(applied_control),
        .result_valid_i(1'b0),.result_ready_o(),.result_azimuth_i(32'sd0),.result_elevation_i(32'sd0),
        .result_confidence_i(16'd0),.result_frame_i(32'd0),.audio_irq_o(),
        .benchmark_done_o(done),.exit_status_o(exit_status),.fatal_o(fatal),.trap_valid_o(trap),
        .b_req_valid_o(qv),.b_req_write_o(qw),.b_req_ready_i(qr),.b_req_addr_o(qa),.b_req_data_o(qd),
        .b_req_strb_o(qs),.b_req_len_o(qlen),.b_rsp_valid_i(rv),.b_rsp_error_i(re),.b_rsp_last_i(rl),
        .b_rsp_data_i(rd),.b_rsp_ready_o(rr),
        .cam_pclk_i(1'b0),.cam_vsync_i(1'b0),.cam_href_i(1'b0),.cam_data_i(8'b0),
        .cam_scl_io(cam_scl_unused),.cam_sda_io(cam_sda_unused),.cam_reset_no(),.cam_pwdn_o(),.camera_irq_o());
    rv32_cache_native_ram memory(.clk(cpu_clk),.rst_n(cpu_reset_n),
        .req_valid_i(qv),.req_write_i(qw),.req_ready_o(qr),.req_addr_i(qa),.req_data_i(qd),
        .req_strb_i(qs),.req_len_i(qlen),.rsp_valid_o(rv),.rsp_data_o(rd),.rsp_error_o(re),
        .rsp_last_o(rl),.rsp_ready_i(rr));
    reg sticky_trap;
    always @(posedge cpu_clk or negedge cpu_reset_n)
        if(!cpu_reset_n) sticky_trap<=0;else if(trap)sticky_trap<=1;
    // Raw pad states: complete, error, RUN request, CPU reset released, PLL lock.
    assign led={done,fatal|sticky_trap,applied_control[0],cpu_reset_n,locked};
endmodule
`default_nettype wire
