`default_nettype none
// Isolated board packaging only; the protected core and original OFF top are unchanged.
module rv32_coremark_top_tri (
    input wire clk,
    input wire rst_n,
    output wire uart_tx
);
    rv32_coremark_top_dual #(.THIRD_ALU_SLOT(1'b1)) u_explicit_tri (
        .clk(clk), .rst_n(rst_n), .uart_tx(uart_tx)
    );
endmodule
`default_nettype wire
