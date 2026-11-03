`default_nettype none
// 双发射整数寄存器堆：四个组合读口、两个同步写口。
// slot0 比 slot1 老；若同拍写同一地址，slot1（年轻指令）的值最终生效。
module rv32_regfile_4r2w(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        we0_i,
    input  wire [4:0]  waddr0_i,
    input  wire [31:0] wdata0_i,
    input  wire        we1_i,
    input  wire [4:0]  waddr1_i,
    input  wire [31:0] wdata1_i,
    input  wire [4:0]  raddr0_1_i,
    output wire [31:0] rdata0_1_o,
    input  wire [4:0]  raddr0_2_i,
    output wire [31:0] rdata0_2_o,
    input  wire [4:0]  raddr1_1_i,
    output wire [31:0] rdata1_1_o,
    input  wire [4:0]  raddr1_2_i,
    output wire [31:0] rdata1_2_o
);
    // x0 是常量，不分配存储；每个真实寄存器的年轻写口优先选择局部化。
    // 保留 x 数组作为读口与诊断视图，复位后四个读口的原契约不变。
    wire [31:0] x [0:31];
    assign x[0] = 32'b0;
    genvar reg_idx;
    generate for (reg_idx=1; reg_idx<32; reg_idx=reg_idx+1) begin : g_register
        reg [31:0] data;
        assign x[reg_idx] = data;
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n)
                data <= 32'b0;
            else if (we1_i && waddr1_i==reg_idx)
                data <= wdata1_i;
            else if (we0_i && waddr0_i==reg_idx)
                data <= wdata0_i;
        end
    end endgenerate

    // 年轻的写口1优先，用于同拍写回到下一组 ID 读口的旁路。
    function [31:0] read_with_bypass;
        input [4:0]  addr;
        input [31:0] stored_data;
        input        write0_en;
        input [4:0]  write0_addr;
        input [31:0] write0_data;
        input        write1_en;
        input [4:0]  write1_addr;
        input [31:0] write1_data;
        begin
            if (addr==5'd0)
                read_with_bypass = 32'b0;
            else if (write1_en && write1_addr==addr && write1_addr!=5'd0)
                read_with_bypass = write1_data;
            else if (write0_en && write0_addr==addr && write0_addr!=5'd0)
                read_with_bypass = write0_data;
            else
                read_with_bypass = stored_data;
        end
    endfunction

    assign rdata0_1_o = read_with_bypass(raddr0_1_i, x[raddr0_1_i],
        we0_i, waddr0_i, wdata0_i, we1_i, waddr1_i, wdata1_i);
    assign rdata0_2_o = read_with_bypass(raddr0_2_i, x[raddr0_2_i],
        we0_i, waddr0_i, wdata0_i, we1_i, waddr1_i, wdata1_i);
    assign rdata1_1_o = read_with_bypass(raddr1_1_i, x[raddr1_1_i],
        we0_i, waddr0_i, wdata0_i, we1_i, waddr1_i, wdata1_i);
    assign rdata1_2_o = read_with_bypass(raddr1_2_i, x[raddr1_2_i],
        we0_i, waddr0_i, wdata0_i, we1_i, waddr1_i, wdata1_i);
endmodule
`default_nettype wire
