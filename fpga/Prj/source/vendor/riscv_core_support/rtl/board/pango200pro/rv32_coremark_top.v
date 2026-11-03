`default_nettype none
// 远程实验平台的板级顶层：系统输入为 27 MHz 时钟和低有效复位，
// CoreMark 运行信息通过 uart_tx 送至平台串口监视器。
module rv32_coremark_top (
    input  wire clk,
    input  wire rst_n,
    output wire uart_tx
);
    rv32_coremark_soc #(
        .CLOCK_HZ(27000000),
        .UART_BAUD(115200),
        .UART_FIFO_ADDR_WIDTH(11),
        // FPGA LAB 接收端轮询较慢：字节间插入 270000 个空闲周期，
        // 约限制为每秒 100 字符；单个字节的帧格式仍是 115200/8N1。
        .UART_INTER_BYTE_GAP_CYCLES(270000)
    ) u_soc (
        .clk(clk),
        .rst_n(rst_n),
        .uart_tx(uart_tx)
    );
endmodule
`default_nettype wire
