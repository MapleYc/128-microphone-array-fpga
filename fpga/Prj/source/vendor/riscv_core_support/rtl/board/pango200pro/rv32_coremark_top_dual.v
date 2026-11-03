`default_nettype none
// Pango200Pro 双发射 CoreMark 板级顶层。
// 物理端口与单发射顶层一致，因此可复用 remote_lab_27mhz.fdc。
module rv32_coremark_top_dual #(
    parameter BRANCH_ALU_PAIR = 1'b1,
    parameter DOUBLE_LOAD = 1'b1,
    parameter LOAD_USE_BYPASS = 1'b1,
    parameter MUL_ALU_PAIR = 1'b1,
    parameter SLOT1_MUL_PAIR = 1'b1,
    parameter STORE_DATA_BYPASS = 1'b1,
    parameter LOAD_ADDR_BYPASS = 1'b1,
    parameter LOOP_PREDICT = 1'b1,
    parameter integer LOOP_COUNTER_BITS = 8,
    parameter JALR_BTB = 1'b1,
    parameter RAS_PREDICT = 1'b1,
    parameter SLOT1_RET_PREDICT = 1'b1,
    parameter UART_FIFO_BLOCK_RAM = 1'b1,
    parameter LOAD_BRANCH_PAIR = 1'b1,
    parameter THIRD_ALU_SLOT = 1'b0
) (
    input  wire clk,
    input  wire rst_n,
    output wire uart_tx
);
    rv32_coremark_soc_dual #(
        .CLOCK_HZ(27000000),
        .UART_BAUD(115200),
        .UART_FIFO_ADDR_WIDTH(11),
        .UART_INTER_BYTE_GAP_CYCLES(270000),
        .RESET_PC(32'h0000_0000),
        .IF_EARLY_JAL(1'b1),
        .BRANCH_PREDICT(2),
        .BHT_INDEX_BITS(8), .DOUBLE_LOAD(DOUBLE_LOAD),
        .LOAD_USE_BYPASS(LOAD_USE_BYPASS), .MUL_ALU_PAIR(MUL_ALU_PAIR), .SLOT1_MUL_PAIR(SLOT1_MUL_PAIR),
        .BRANCH_ALU_PAIR(BRANCH_ALU_PAIR),
        .STORE_DATA_BYPASS(STORE_DATA_BYPASS), .LOAD_ADDR_BYPASS(LOAD_ADDR_BYPASS),
        .LOOP_PREDICT(LOOP_PREDICT), .LOOP_COUNTER_BITS(LOOP_COUNTER_BITS),
        .JALR_BTB(JALR_BTB), .RAS_PREDICT(RAS_PREDICT),
        .SLOT1_RET_PREDICT(SLOT1_RET_PREDICT), .UART_FIFO_BLOCK_RAM(UART_FIFO_BLOCK_RAM),
        .LOAD_BRANCH_PAIR(LOAD_BRANCH_PAIR), .THIRD_ALU_SLOT(THIRD_ALU_SLOT)
    ) u_soc (
        .clk(clk),
        .rst_n(rst_n),
        .uart_tx(uart_tx)
    );
endmodule
`default_nettype wire
