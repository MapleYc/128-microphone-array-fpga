// IF/ID 级间寄存器：把取指结果与对应 PC/PC+4 一起送入译码阶段。
// valid_o 标识指令是否可用；无效槽的其余字段不应被当作有效指令使用。
module rv32_if_id_reg(
    input         clk,
    input         rst_n,
    input         en_i,
    input         flush_i,

    input         valid_i,
    input  [31:0] pc_i,
    input  [31:0] pc_plus4_i,
    input  [31:0] instr_i,

    output reg        valid_o,
    output reg [31:0] pc_o,
    output reg [31:0] pc_plus4_o,
    output reg [31:0] instr_o
);


always@(posedge clk or negedge rst_n)begin
    // 优先级：异步复位 > flush 插入空槽 > en 捕获输入 > 保持全部输出。
    if(!rst_n)begin
        valid_o <= 1'b0;
        pc_o    <= 32'h0000_0000;
        pc_plus4_o<= 32'h0000_0000;
        instr_o <= 32'b0;
    end
    else if(flush_i)begin
        valid_o <= 1'b0;
        pc_o    <= 32'b0;
        instr_o <= 32'b0;
        pc_plus4_o <= 32'b0;
    end
    else if(en_i)begin
        valid_o <= valid_i;
        pc_o    <= pc_i;
        pc_plus4_o <= pc_plus4_i;
        instr_o <= instr_i;
    end
    
end


endmodule




