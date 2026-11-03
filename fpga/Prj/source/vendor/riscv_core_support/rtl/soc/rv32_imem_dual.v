`default_nettype none
// Default: two identical single-port ROM copies provide two independent reads.
// TRI-ALU-01 optionally adds a third full ROM copy without changing vendor IP.
module rv32_imem_dual #(
    parameter THIRD_ALU_SLOT = 1'b0
)(
    input  wire [13:0] addr0_i,
    output wire [31:0] rdata0_o,
    input  wire [13:0] addr1_i,
    output wire [31:0] rdata1_o,
    input  wire        clk,
    input  wire        rst,
    input  wire [13:0] addr2_i,
    output wire [31:0] rdata2_o
);
    rv32_imem u_bank0 (
        .addr(addr0_i), .rd_data(rdata0_o), .clk(clk), .rst(rst)
    );
    rv32_imem u_bank1 (
        .addr(addr1_i), .rd_data(rdata1_o), .clk(clk), .rst(rst)
    );
    // Third ROM is elaborated only for the TRI-ALU-01 experiment.
    generate if (THIRD_ALU_SLOT) begin : g_third_rom
        rv32_imem u_bank2 (
            .addr(addr2_i), .rd_data(rdata2_o), .clk(clk), .rst(rst)
        );
    end else begin : g_dual_rom
        assign rdata2_o = 32'b0;
    end endgenerate
endmodule
`default_nettype wire
