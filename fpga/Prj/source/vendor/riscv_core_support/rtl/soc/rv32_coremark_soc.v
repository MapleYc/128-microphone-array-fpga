`default_nettype none
// CoreMark 最小 SoC：连接 CPU、程序 ROM、数据 RAM、UART 与计时/退出寄存器。
// 盘古存储器 IP 在 CPU 下降沿返回读数据，下一上升沿由零等待 CPU 采样；
// 这条半周期路径必须由 PDS 时序报告验证，不能只看行为仿真。
module rv32_coremark_soc #(
    parameter integer CLOCK_HZ = 27000000,
    parameter integer UART_BAUD = 115200,
    parameter integer UART_FIFO_ADDR_WIDTH = 11,
    parameter integer UART_INTER_BYTE_GAP_CYCLES = 0
)(
    input  wire clk,
    input  wire rst_n,
    output wire uart_tx
);
    // 与软件 platform.h / link.ld 保持一致的地址映射。
    localparam [31:0] DMEM_BASE       = 32'h1000_0000;
    localparam [31:0] UART_DATA_ADDR  = 32'h2000_0000;
    localparam [31:0] UART_STAT_ADDR  = 32'h2000_0004;
    localparam [31:0] TIMER_LO_ADDR   = 32'h2000_2000;
    localparam [31:0] TIMER_HI_ADDR   = 32'h2000_2004;
    localparam [31:0] MARKER_ADDR     = 32'h2000_2008;
    localparam [31:0] EXIT_ADDR       = 32'h2000_f000;

    // 外部低有效复位释放后，再延迟三个时钟让 ROM/RAM 输出稳定。
    reg [2:0] reset_release;
    wire core_rst_n = reset_release[2];
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            reset_release <= 3'b000;
        else
            reset_release <= {reset_release[1:0], 1'b1};
    end

    wire [31:0] imem_addr;     //PC
    wire [31:0] imem_word;      // 32位指令
    // 取指只映射低 64 KiB；越界地址返回 ADDI x0,x0,0，避免未知值传播。
    wire [31:0] imem_rdata = (imem_addr[31:16] == 16'h0000)
                           ? imem_word : 32'h0000_0013;
    wire dmem_read;
    wire dmem_write;
    wire [31:0] dmem_addr;    // 数据内存地址
    wire [31:0] dmem_wdata;
    wire [3:0] dmem_wstrb;
    reg  [31:0] dmem_rdata;
    wire [31:0] dmem_ram_rdata;
    wire halted;
    wire [31:0] halt_pc;
    wire [3:0] halt_cause;
    wire commit_valid;
    wire [31:0] commit_pc;
    wire [31:0] commit_instr;
    wire commit_rd_write;
    wire [4:0] commit_rd_addr;
    wire [31:0] commit_rd_data;

    wire dmem_selected = (dmem_addr[31:16] == DMEM_BASE[31:16]);
    wire dmem_ram_write = dmem_write && dmem_selected;
    // 数据 RAM 在读时钟上升沿读。反相 CPU 时钟后即为 CPU 下降沿；
    // IP 未启用输出寄存器，使数据能在下一个 CPU 上升沿前到达。
    wire memory_read_clk = !clk;

    rv32_imem u_imem (
        .addr(imem_addr[15:2]),
        .rd_data(imem_word),
        .clk(memory_read_clk),
        .rst(!core_rst_n)
    );

    rv32_dmem u_dmem (
        .wr_data(dmem_wdata),
        .wr_addr(dmem_addr[15:2]),
        .wr_en(dmem_ram_write),
        .wr_clk(clk),
        .wr_rst(!core_rst_n),
        .wr_byte_en(dmem_wstrb),
        .rd_data(dmem_ram_rdata),
        .rd_addr(dmem_addr[15:2]),
        .rd_clk(memory_read_clk),
        .rd_rst(!core_rst_n)
    );

    // 软件通过只读 MMIO 读取 64 位周期计数器，CoreMark 的计时来自这里，
    // 与 UART 打印耗时和仿真看门狗的周期上限是不同概念。
    reg [63:0] cycle_counter;
    reg [31:0] marker_value;
    reg benchmark_done;
    reg [31:0] exit_status;
    always @(posedge clk or negedge core_rst_n) begin
        if (!core_rst_n) begin
            cycle_counter <= 64'b0;
            marker_value <= 32'b0;
            benchmark_done <= 1'b0;
            exit_status <= 32'b0;
        end else begin
            cycle_counter <= cycle_counter + 64'd1;
            if (dmem_write && dmem_addr == MARKER_ADDR)
                marker_value <= dmem_wdata;
            if (dmem_write && dmem_addr == EXIT_ADDR) begin
                benchmark_done <= 1'b1;
                exit_status <= dmem_wdata;
            end
        end
    end

    // LSU 的写数据已按字节地址移到对应通道；根据写掩码选出 UART 字节。
    reg [7:0] uart_byte;
    always @(*) begin
        casez (dmem_wstrb)
            4'b???1: uart_byte = dmem_wdata[7:0];
            4'b??10: uart_byte = dmem_wdata[15:8];
            4'b?100: uart_byte = dmem_wdata[23:16];
            4'b1000: uart_byte = dmem_wdata[31:24];
            default: uart_byte = dmem_wdata[7:0];
        endcase
    end
    wire uart_write = dmem_write && (dmem_addr == UART_DATA_ADDR) && (|dmem_wstrb);
    wire uart_ready;
    wire uart_busy;
    wire uart_overflow;
    rv32_uart_tx_fifo #(
        .CLOCK_HZ(CLOCK_HZ),
        .BAUD(UART_BAUD),
        .FIFO_ADDR_WIDTH(UART_FIFO_ADDR_WIDTH),
        .INTER_BYTE_GAP_CYCLES(UART_INTER_BYTE_GAP_CYCLES)
    ) u_uart (
        .clk(clk),
        .rst_n(core_rst_n),
        .write_valid_i(uart_write),
        .write_data_i(uart_byte),
        .write_ready_o(uart_ready),
        .busy_o(uart_busy),
        .overflow_o(uart_overflow),
        .tx_o(uart_tx)
    );

    // 读多路选择：RAM 或 MMIO 状态/计时器。未映射地址读 0。
    // UART 状态的低三位依次表示可写、发送忙、写入溢出。
    always @(*) begin
        dmem_rdata = 32'b0;
        if (dmem_selected)
            dmem_rdata = dmem_ram_rdata;
        else begin
            case (dmem_addr)
                UART_STAT_ADDR: dmem_rdata = {29'b0, uart_overflow, uart_busy, uart_ready};
                TIMER_LO_ADDR:  dmem_rdata = cycle_counter[31:0];
                TIMER_HI_ADDR:  dmem_rdata = cycle_counter[63:32];
                MARKER_ADDR:    dmem_rdata = marker_value;
                EXIT_ADDR:      dmem_rdata = exit_status;
                default:        dmem_rdata = 32'b0;
            endcase
        end
    end

    // UART 输出为对外可见结果；停机和 commit 信号保留作内部调试。
    rv32_core u_core (
        .clk(clk),
        .rst_n(core_rst_n),
        .imem_addr_o(imem_addr),
        .imem_rdata_i(imem_rdata),
        .dmem_read_o(dmem_read),
        .dmem_write_o(dmem_write),
        .dmem_addr_o(dmem_addr),
        .dmem_wdata_o(dmem_wdata),
        .dmem_wstrb_o(dmem_wstrb),
        .dmem_rdata_i(dmem_rdata),
        .halted_o(halted),
        .halt_pc_o(halt_pc),
        .halt_cause_o(halt_cause),
        .commit_valid_o(commit_valid),
        .commit_pc_o(commit_pc),
        .commit_instr_o(commit_instr),
        .commit_rd_write_o(commit_rd_write),
        .commit_rd_addr_o(commit_rd_addr),
        .commit_rd_data_o(commit_rd_data)
    );
endmodule
`default_nettype wire
