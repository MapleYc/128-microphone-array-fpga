`default_nettype none
// Separate functional configuration. The reference benchmark is untouched.
module rv32_cached_feature_top(
    input wire clk,rst_n,uart_rx,input wire [3:0] gpio_in,
    output wire [3:0] gpio_out,output wire uart_tx
);
    wire cpu_clk,pll_lock;
    rv32_cpu_pll63 u_cpu_pll(.clkout0(cpu_clk),.clkin1(clk),.lock(pll_lock));
    wire async_reset_n=rst_n && pll_lock;
    reg [1:0] reset_sync;
    always @(posedge cpu_clk or negedge async_reset_n)
      if(!async_reset_n)reset_sync<=0;else reset_sync<={reset_sync[0],1'b1};
    wire qv,qw,qr,rv,re,rl,rr;wire [31:0] qa,qd,rd;wire [3:0] qs;wire [2:0] qlen;
    rv32_cached_soc #(.CLOCK_HZ(63000000),.UART_BAUD(115200),
      .UART_FIFO_ADDR_WIDTH(11),.UART_INTER_BYTE_GAP_CYCLES(630000)) u_soc(
      .clk(cpu_clk),.rst_n(reset_sync[1]),.irq_external_i(1'b0),
      .uart_rx_i(uart_rx),.gpio_i(gpio_in),.gpio_o(gpio_out),.uart_tx(uart_tx),
      .benchmark_done(),.exit_status(),.commit_valid_o(),.commit_write_o(),
      .commit_pc_o(),.commit_instr_o(),.commit_data_o(),.commit_rd_o(),
      .trap_valid_o(),.trap_pc_o(),.trap_cause_o(),.trap_tval_o(),
      .invalidate_i(1'b0),.invalidate_done_o(),.fatal_o(),
      .b_req_valid_o(qv),.b_req_write_o(qw),.b_req_ready_i(qr),
      .b_req_addr_o(qa),.b_req_data_o(qd),.b_req_strb_o(qs),.b_req_len_o(qlen),
      .b_rsp_valid_i(rv),.b_rsp_error_i(re),.b_rsp_last_i(rl),.b_rsp_data_i(rd),.b_rsp_ready_o(rr));
    rv32_cache_native_ram u_memory(.clk(cpu_clk),.rst_n(reset_sync[1]),
      .req_valid_i(qv),.req_write_i(qw),.req_ready_o(qr),.req_addr_i(qa),
      .req_data_i(qd),.req_strb_i(qs),.req_len_i(qlen),.rsp_valid_o(rv),
      .rsp_data_o(rd),.rsp_error_o(re),.rsp_last_o(rl),.rsp_ready_i(rr));
endmodule
`default_nettype wire
