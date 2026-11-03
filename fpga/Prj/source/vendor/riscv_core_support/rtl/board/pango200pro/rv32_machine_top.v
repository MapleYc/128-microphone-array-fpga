`default_nettype none
// New explicit entry, no silent replacement of the accepted CoreMark top.
// Physical27MHz input; actual vendor PLL generates50MHz CPU clock.
// External IRQ is available at rv32_machine_soc; no unapproved pad is invented.
module rv32_machine_top(input wire clk,rst_n,output wire uart_tx);
    wire cpu_clk,pll_lock;
    rv32_cpu_pll63 u_cpu_pll(.clkout0(cpu_clk),.clkin1(clk),.lock(pll_lock));
    wire async_reset_n=rst_n && pll_lock;
    reg [1:0] reset_sync;
    always @(posedge cpu_clk or negedge async_reset_n)
        if(!async_reset_n) reset_sync<=0;else reset_sync<={reset_sync[0],1'b1};
    rv32_machine_soc #(.CLOCK_HZ(63000000),.UART_BAUD(115200),
        .UART_FIFO_ADDR_WIDTH(11),.UART_INTER_BYTE_GAP_CYCLES(630000)) u_soc(
        .clk(cpu_clk),.rst_n(reset_sync[1]),.irq_external_i(1'b0),.uart_tx(uart_tx),
        .benchmark_done(),.exit_status(),.trap_valid_o(),.trap_pc_o(),.trap_cause_o(),.trap_tval_o());
endmodule
`default_nettype wire
