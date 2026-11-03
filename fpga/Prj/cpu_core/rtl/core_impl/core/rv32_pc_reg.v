// PC 状态寄存器：异步低有效复位；每个上升沿在 en_i 有效时装入 next_pc_i，否则保持原 PC。
// 本模块只保存地址，跳转/暂停的选择由外部组合控制逻辑完成。
module rv32_pc_reg#(
    parameter [31:0] RESET_PC = 32'h0000_0000
)(
    input              clk,
    input              rst_n,
    input              en_i,
    input   [31:0]     next_pc_i,
    output reg [31:0]  pc_o

);

always@(posedge clk or negedge rst_n)begin
    // 优先级：复位 > 更新 > 保持。
    if(!rst_n)begin
        pc_o <= RESET_PC;
    end
    else if(en_i)begin
        pc_o <= next_pc_i;
    end
end






endmodule


