// RV32I 立即数生成器：由译码器给出的 imm_type_i 选择编码格式，输出 32 位扩展值。
// I/S/B/J 为有符号立即数；U 型直接保留指令高 20 位并在低 12 位补零。
module rv32_imm_gen(
    input [31:0] instr_i,
    input [2:0]  imm_type_i,
    output reg [31:0] imm_o

);


always@(*)begin
    case(imm_type_i)
        // I 型：instr[31:20]，符号扩展。
        3'd0:imm_o = {{20{instr_i[31]}},instr_i[31:20]}; 
        // S 型：instr[31:25] 与 instr[11:7] 拼成 12 位偏移。
        3'd1:imm_o = {{20{instr_i[31]}},instr_i[31:25],instr_i[11:7]};
        // B 型：低位隐含 0，形成以 2 字节为单位编码的分支偏移。
        3'd2:imm_o = {{20{instr_i[31]}},instr_i[7],
                         instr_i[30:25],instr_i[11:8],1'b0};
        // U 型：高 20 位立即数，低 12 位清零。
        3'd3:imm_o = {instr_i[31:12],12'b0};
        // J 型：位段按规范重排，低位隐含 0，再做符号扩展。
        3'd4:imm_o = {{12{instr_i[31]}},instr_i[19:12],instr_i[20],instr_i[30:21],1'b0};
        default:imm_o = 32'd0;
    endcase
end



endmodule





