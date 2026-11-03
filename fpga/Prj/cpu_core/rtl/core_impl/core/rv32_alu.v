// 组合逻辑 ALU：执行 RV32I 的算术/逻辑/移位运算。
// Zmmul 操作码 10~13 由独立的 rv32_mul_unit 执行，避免乘法器落在
// EX 到数据存储器的半周期组合路径中。
module rv32_alu(
    input   [31:0]  a_i,
    input   [31:0]  b_i,
    input   [3:0]   alu_op_i,
    output  [31:0]  result_o,
    output reg [31:0] result_no_shift_o
);

// 使用一个 32 位、五层选择的右移网络实现 SRL/SRA；SLL 通过输入输出位反转复用。
wire shift_left = alu_op_i==4'd2;
wire shift_fill = alu_op_i==4'd7 && a_i[31];
wire [31:0] reverse_a;
wire [31:0] reverse_shift;
wire [31:0] shift_stage [0:5];
wire [31:0] shift_result = shift_left ? reverse_shift : shift_stage[5];
genvar shift_bit, shift_level;
generate
    for(shift_bit=0;shift_bit<32;shift_bit=shift_bit+1) begin : g_reverse
        assign reverse_a[shift_bit] = a_i[31-shift_bit];
        assign reverse_shift[shift_bit] = shift_stage[5][31-shift_bit];
    end
    for(shift_level=0;shift_level<5;shift_level=shift_level+1) begin : g_shift
        localparam integer DISTANCE = 1 << shift_level;
        assign shift_stage[shift_level+1] = b_i[shift_level]
            ? {{DISTANCE{shift_fill}},shift_stage[shift_level][31:DISTANCE]}
            : shift_stage[shift_level];
    end
endgenerate
assign shift_stage[0] = shift_left ? reverse_a : a_i;
// 同号有符号比较与无符号比较共享低 31 位比较结果。
wire less_low = a_i[30:0] < b_i[30:0];
wire signs_differ = a_i[31] ^ b_i[31];
wire signed_less = signs_differ ? a_i[31] : less_low;
wire unsigned_less = signs_differ ? b_i[31] : less_low;
reg [31:0] result;
wire [31:0] arithmetic_result;
rv32_alu_arithmetic u_arithmetic(.a_i(a_i),.b_i(b_i),
    .subtract_i(alu_op_i[0]),.result_o(arithmetic_result));

assign result_o = result;

// 跨组短路径结果共享算术和比较逻辑，但不接入桶形移位器输出。
// 选择该路径时，依赖移位结果的消费者等待既有 WB 结果。
always @(*) begin
    case (alu_op_i)
        4'd0,4'd1: result_no_shift_o = arithmetic_result;
        4'd3: result_no_shift_o = {31'b0,signed_less};
        4'd4: result_no_shift_o = {31'b0,unsigned_less};
        4'd5: result_no_shift_o = a_i ^ b_i;
        4'd8: result_no_shift_o = a_i | b_i;
        4'd9: result_no_shift_o = a_i & b_i;
        default: result_no_shift_o = 32'd0;
    endcase
end

always@(*)begin
    case(alu_op_i)
        // 0~9：RV32I。移位量只使用 rs2/立即数的低 5 位（范围 0~31）。
        4'd0: result = arithmetic_result;
        4'd1: result = arithmetic_result;
        4'd2: result = shift_result;
        // SLT：先比较符号位；同号时比较剩余 31 位。
        4'd3: result = {31'b0,signed_less};
        // SLTU 结合最高位大小关系与共享的低 31 位比较结果。
        4'd4: result = {31'b0,unsigned_less};
        4'd5: result = a_i ^ b_i;
        4'd6: result = shift_result;
        // SRA 显式填充符号位；SRL 和位反转后的 SLL 使用零填充。
        4'd7: result = shift_result;
        4'd8: result = a_i | b_i;
        4'd9: result = a_i & b_i;
        default:result = 32'd0;    
    endcase
end




endmodule

// 独立算术层级避免结果选择和移位逻辑进入进位链。
// 纯组合实现，保持完整 32 位模加法与减法语义。
module rv32_alu_arithmetic(
    input [31:0] a_i,b_i,
    input subtract_i,
    output [31:0] result_o
) /* synthesis syn_hier = "fixed" */;
    wire [31:0] operand_b=b_i ^ {32{subtract_i}};
    assign result_o=a_i+operand_b+subtract_i;
endmodule

// 仅用于已获发射许可的 AND/OR/XOR 到年轻 MUL 的前递。
// 独立逻辑单元避免通用算术及桶形移位网络进入 APM 输入。
module rv32_mul_pair_logic(
    input [31:0] a_i,b_i,
    input [3:0] alu_op_i,
    output [31:0] result_o
) /* synthesis syn_hier = "fixed" */;
    assign result_o = alu_op_i==4'd9 ? (a_i & b_i) :
                      alu_op_i==4'd8 ? (a_i | b_i) : (a_i ^ b_i);
endmodule

