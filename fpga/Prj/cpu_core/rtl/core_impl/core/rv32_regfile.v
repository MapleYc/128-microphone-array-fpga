// RV32I 整数寄存器堆：32 个 32 位寄存器，单写口、双异步读口。
// x0 永远读作 0，写入 x0 被忽略；其他寄存器在时钟上升沿写入。
module rv32_regfile(
    input           clk,
    input           rst_n,
    input           we,
    input   [4:0]   waddr,
    input   [31:0]  wdata,
    input   [4:0]   raddr1,
    output  [31:0]  rdata1,
    input   [4:0]   raddr2,
    output  [31:0]  rdata2
);


integer i;

reg [31:0] x [0:31];

// 异步低有效复位清零全部物理寄存器；复位后的普通写入仅更新一个非零地址。
always@(posedge clk or negedge rst_n)begin
    if(!rst_n)begin
        for(i=0;i<32;i=i+1)begin
            x[i] <= 32'b0;
        end
    end
    else if(we && waddr!= 5'b0) begin
        x[waddr] <= wdata;
    end
end

// 两个读口为组合逻辑；读写同地址同周期的观测时序由上升沿前后决定。
// 显式旁路 x0，保证即使内部 x[0] 出现未知值也不影响架构可见结果。
assign rdata1 = raddr1==5'b0 ? 32'b0 : x[raddr1];
assign rdata2 = raddr2==5'b0 ? 32'b0 : x[raddr2];



endmodule





