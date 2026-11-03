`default_nettype none
// 双发射 CoreMark SoC：双路取指、可选双 RAM 读口、单路 Store/MMIO。
// DOUBLE_LOAD 复制数据 RAM 并广播写入，保留现有地址映射和同步读写时钟。
module rv32_coremark_soc_dual #(
    parameter BRANCH_ALU_PAIR = 1'b1,
    parameter integer CLOCK_HZ = 27000000,
    parameter integer UART_BAUD = 115200,
    parameter integer UART_FIFO_ADDR_WIDTH = 11,
    parameter integer UART_INTER_BYTE_GAP_CYCLES = 0,
    parameter [31:0] RESET_PC = 32'h0000_0000,
    parameter IF_EARLY_JAL = 1'b1,
    parameter integer BRANCH_PREDICT = 2,
    parameter integer BHT_INDEX_BITS = 8,
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
)(
    input  wire clk,
    input  wire rst_n,
    output wire uart_tx
);
    localparam [31:0] DMEM_BASE      = 32'h1000_0000;
    localparam [31:0] UART_DATA_ADDR = 32'h2000_0000;
    localparam [31:0] UART_STAT_ADDR = 32'h2000_0004;
    localparam [31:0] TIMER_LO_ADDR  = 32'h2000_2000;
    localparam [31:0] TIMER_HI_ADDR  = 32'h2000_2004;
    localparam [31:0] MARKER_ADDR    = 32'h2000_2008;
    localparam [31:0] EXIT_ADDR      = 32'h2000_f000;

    reg [2:0] reset_release;
    wire core_rst_n = reset_release[2];
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            reset_release <= 3'b000;
        else
            reset_release <= {reset_release[1:0], 1'b1};
    end

    wire [31:0] imem_addr0, imem_addr1, imem_addr2;
    wire [31:0] imem_word0, imem_word1, imem_word2;
    wire [31:0] imem_rdata0 = (imem_addr0[31:16] == 16'h0000)
                            ? imem_word0 : 32'h0000_0013;
    wire [31:0] imem_rdata1 = (imem_addr1[31:16] == 16'h0000)
                            ? imem_word1 : 32'h0000_0013;
    wire [31:0] imem_rdata2 = (imem_addr2[31:16] == 16'h0000)
                            ? imem_word2 : 32'h0000_0013;
    wire memory_read_clk = !clk;

    rv32_imem_dual #(.THIRD_ALU_SLOT(THIRD_ALU_SLOT)) u_imem_dual (
        .addr0_i(imem_addr0[15:2]), .rdata0_o(imem_word0),
        .addr1_i(imem_addr1[15:2]), .rdata1_o(imem_word1),
        .addr2_i(imem_addr2[15:2]), .rdata2_o(imem_word2),
        .clk(memory_read_clk), .rst(!core_rst_n)
    );

    wire dmem_read, dmem_write;
    wire [31:0] dmem_addr, dmem_wdata;
    wire [3:0] dmem_wstrb;
    wire [31:0] dmem_rdata;
    wire [31:0] dmem_ram_rdata;
    wire dmem1_read;
    wire [31:0] dmem1_addr, dmem1_ram_rdata;
    wire [31:0] dmem1_rdata = dmem1_addr[31:16]==DMEM_BASE[31:16]
                            ? dmem1_ram_rdata : 32'b0;
    wire dmem_selected = dmem_addr[31:16] == DMEM_BASE[31:16];
    wire dmem_ram_write = dmem_write && dmem_selected;

    rv32_dmem u_dmem (
        .wr_data(dmem_wdata), .wr_addr(dmem_addr[15:2]),
        .wr_en(dmem_ram_write), .wr_clk(clk), .wr_rst(!core_rst_n),
        .wr_byte_en(dmem_wstrb), .rd_data(dmem_ram_rdata),
        .rd_addr(dmem_addr[15:2]), .rd_clk(memory_read_clk),
        .rd_rst(!core_rst_n)
    );
    // 两份相同初始化 RAM：所有 Store 广播写入，读地址各自独立。
    // 第二口只读取普通 RAM；UART/计时器等 MMIO 保持原单口顺序。
    generate if (DOUBLE_LOAD) begin : g_double_load
        rv32_dmem u_dmem_load1 (
            .wr_data(dmem_wdata), .wr_addr(dmem_addr[15:2]),
            .wr_en(dmem_ram_write), .wr_clk(clk), .wr_rst(!core_rst_n),
            .wr_byte_en(dmem_wstrb), .rd_data(dmem1_ram_rdata),
            .rd_addr(dmem1_addr[15:2]), .rd_clk(memory_read_clk),
            .rd_rst(!core_rst_n)
        );
    end else begin : g_single_load
        assign dmem1_ram_rdata = 32'b0;
    end endgenerate

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
    wire uart_write = dmem_write && dmem_addr == UART_DATA_ADDR && |dmem_wstrb;
    wire uart_ready, uart_busy, uart_overflow;
    rv32_uart_tx_fifo #(
        .CLOCK_HZ(CLOCK_HZ), .BAUD(UART_BAUD),
        .FIFO_ADDR_WIDTH(UART_FIFO_ADDR_WIDTH),
        .INTER_BYTE_GAP_CYCLES(UART_INTER_BYTE_GAP_CYCLES),
        .BLOCK_RAM(UART_FIFO_BLOCK_RAM)
    ) u_uart (
        .clk(clk), .rst_n(core_rst_n), .write_valid_i(uart_write),
        .write_data_i(uart_byte), .write_ready_o(uart_ready),
        .busy_o(uart_busy), .overflow_o(uart_overflow), .tx_o(uart_tx)
    );

    // Address classes are disjoint; prepare MMIO independently of RAM data.
    assign dmem_rdata = ({32{dmem_selected}} & dmem_ram_rdata) |
        ({32{dmem_addr==UART_STAT_ADDR}} & {29'b0,uart_overflow,uart_busy,uart_ready}) |
        ({32{dmem_addr==TIMER_LO_ADDR}} & cycle_counter[31:0]) |
        ({32{dmem_addr==TIMER_HI_ADDR}} & cycle_counter[63:32]) |
        ({32{dmem_addr==MARKER_ADDR}} & marker_value) |
        ({32{dmem_addr==EXIT_ADDR}} & exit_status);
    wire halted;
    wire [31:0] halt_pc, halt_instr;
    wire [3:0] halt_cause;
    wire commit0_valid, commit1_valid, commit2_valid;
    wire [31:0] commit0_pc, commit1_pc, commit2_pc;
    wire [31:0] commit0_instr, commit1_instr, commit2_instr;
    wire commit0_rd_write, commit1_rd_write, commit2_rd_write;
    wire [4:0] commit0_rd_addr, commit1_rd_addr, commit2_rd_addr;
    wire [31:0] commit0_rd_data, commit1_rd_data, commit2_rd_data;

    rv32_core_dual #(
        .RESET_PC(RESET_PC), .IF_EARLY_JAL(IF_EARLY_JAL),
        .BRANCH_PREDICT(BRANCH_PREDICT),
        .BHT_INDEX_BITS(BHT_INDEX_BITS), .DOUBLE_LOAD(DOUBLE_LOAD),
        .LOAD_USE_BYPASS(LOAD_USE_BYPASS), .MUL_ALU_PAIR(MUL_ALU_PAIR), .SLOT1_MUL_PAIR(SLOT1_MUL_PAIR),
        .BRANCH_ALU_PAIR(BRANCH_ALU_PAIR),
        .STORE_DATA_BYPASS(STORE_DATA_BYPASS), .LOAD_ADDR_BYPASS(LOAD_ADDR_BYPASS),
        .LOOP_PREDICT(LOOP_PREDICT), .LOOP_COUNTER_BITS(LOOP_COUNTER_BITS),
        .JALR_BTB(JALR_BTB), .RAS_PREDICT(RAS_PREDICT),
        .SLOT1_RET_PREDICT(SLOT1_RET_PREDICT), .LOAD_BRANCH_PAIR(LOAD_BRANCH_PAIR),
        .THIRD_ALU_SLOT(THIRD_ALU_SLOT)
    ) u_core_dual (
        .clk(clk), .rst_n(core_rst_n),
        .imem_addr0_o(imem_addr0), .imem_rdata0_i(imem_rdata0),
        .imem_addr1_o(imem_addr1), .imem_rdata1_i(imem_rdata1),
        .imem_addr2_o(imem_addr2), .imem_rdata2_i(imem_rdata2),
        .dmem_read_o(dmem_read), .dmem_write_o(dmem_write),
        .dmem_addr_o(dmem_addr), .dmem_wdata_o(dmem_wdata),
        .dmem_wstrb_o(dmem_wstrb), .dmem_rdata_i(dmem_rdata),
        .dmem1_read_o(dmem1_read), .dmem1_addr_o(dmem1_addr), .dmem1_rdata_i(dmem1_rdata),
        .halted_o(halted), .halt_pc_o(halt_pc),
        .halt_instr_o(halt_instr), .halt_cause_o(halt_cause),
        .commit0_valid_o(commit0_valid), .commit0_pc_o(commit0_pc),
        .commit0_instr_o(commit0_instr), .commit0_rd_write_o(commit0_rd_write),
        .commit0_rd_addr_o(commit0_rd_addr), .commit0_rd_data_o(commit0_rd_data),
        .commit1_valid_o(commit1_valid), .commit1_pc_o(commit1_pc),
        .commit1_instr_o(commit1_instr), .commit1_rd_write_o(commit1_rd_write),
        .commit1_rd_addr_o(commit1_rd_addr), .commit1_rd_data_o(commit1_rd_data),
        .commit2_valid_o(commit2_valid), .commit2_pc_o(commit2_pc),
        .commit2_instr_o(commit2_instr), .commit2_rd_write_o(commit2_rd_write),
        .commit2_rd_addr_o(commit2_rd_addr), .commit2_rd_data_o(commit2_rd_data)
    );
endmodule
`default_nettype wire
