`default_nettype none
// 并行精确识别指令，再分别编码控制信号。
// 使用确定常量比较拒绝未知选择位，不使用通配匹配放宽合法指令集合。
module rv32_decoder(
    input wire [31:0] instr_i,
    output wire [2:0] imm_type_o,
    output wire [3:0] alu_op_o,
    output wire [1:0] alu_a_sel_o,
    output wire alu_b_sel_o,
    output wire [1:0] wb_sel_o,flow_type_o,
    output wire [2:0] branch_op_o,lsu_type_o,
    output wire reg_write_o,mem_read_o,mem_write_o,use_rs1_o,use_rs2_o,
    output wire [1:0] sys_op_o,
    output wire illegal_o,
    output wire [6:0] issue_class_o
);
    wire [7:0] f;
    genvar k;
    generate for(k=0;k<8;k=k+1) begin:g_funct3
        assign f[k]=(instr_i[14:12] === k[2:0]);
    end endgenerate
    wire f7_zero=(instr_i[31:25] === 7'd0);
    wire f7_sub=(instr_i[31:25] === 7'd32);
    wire f7_mul=(instr_i[31:25] === 7'd1);
    wire op_imm=(instr_i[6:0] === 7'h13);
    wire op_reg=(instr_i[6:0] === 7'h33);
    wire lui=(instr_i[6:0] === 7'h37);
    wire auipc=(instr_i[6:0] === 7'h17);
    wire jal=(instr_i[6:0] === 7'h6f);
    wire jalr=(instr_i[6:0] === 7'h67) && f[0];
    wire branch=(instr_i[6:0] === 7'h63) && (f[0]|f[1]|f[4]|f[5]|f[6]|f[7]);
    wire load=(instr_i[6:0] === 7'h03) && (f[0]|f[1]|f[2]|f[4]|f[5]);
    wire store=(instr_i[6:0] === 7'h23) && (f[0]|f[1]|f[2]);
    wire imm=op_imm && (f[0]|f[2]|f[3]|f[4]|f[6]|f[7]|
                          (f[1]&f7_zero)|(f[5]&(f7_zero|f7_sub)));
    wire regop=op_reg && ((f7_zero && (|f)) | (f7_sub&&(f[0]|f[5])));
    wire mul=op_reg && f7_mul && (f[0]|f[1]|f[2]|f[3]);
    wire ordinary=imm|regop;
    wire fence=(instr_i[6:0] === 7'h0f) && f[0];
    wire ecall=(instr_i === 32'h00000073);
    wire ebreak=(instr_i === 32'h00100073);

    // 直接输出指令语义类别，避免再次译码通用控制编码。
    // 位定义：[0] 可发射，[1] 普通 ALU，[2] Load，[3] Store，[4] Branch，[5] MUL，[6] 控制流。
    assign issue_class_o={jal|jalr|branch,mul,branch,store,load,lui|auipc|ordinary,
                          lui|auipc|jal|jalr|branch|load|store|ordinary|mul};
    assign illegal_o=!(lui|auipc|jal|jalr|branch|load|store|imm|regop|mul|fence|ecall|ebreak);
    assign imm_type_o=({3{lui|auipc}}&3'd3)|({3{jal}}&3'd4)|
                      ({3{branch}}&3'd2)|({3{store}}&3'd1);
    assign alu_op_o=({4{regop&&f[0]&&f7_sub}}&4'd1)|
                    ({4{ordinary&&f[1]}}&4'd2)|
                    ({4{ordinary&&f[2]}}&4'd3)|
                    ({4{ordinary&&f[3]}}&4'd4)|
                    ({4{ordinary&&f[4]}}&4'd5)|
                    ({4{ordinary&&f[5]&&f7_zero}}&4'd6)|
                    ({4{ordinary&&f[5]&&f7_sub}}&4'd7)|
                    ({4{ordinary&&f[6]}}&4'd8)|
                    ({4{ordinary&&f[7]}}&4'd9)|
                    ({4{mul&&f[0]}}&4'd10)|({4{mul&&f[1]}}&4'd11)|
                    ({4{mul&&f[2]}}&4'd12)|({4{mul&&f[3]}}&4'd13);
    assign alu_a_sel_o={lui,auipc|jal|branch};
    assign alu_b_sel_o=lui|auipc|jal|jalr|branch|load|store|imm;
    assign wb_sel_o={jal|jalr,load};
    assign flow_type_o={jal|jalr,branch|jalr};
    assign branch_op_o=branch ? instr_i[14:12] : 3'b0;
    assign lsu_type_o=(load|store) ? instr_i[14:12] : 3'b0;
    assign reg_write_o=lui|auipc|jal|jalr|load|imm|regop|mul;
    assign mem_read_o=load;
    assign mem_write_o=store;
    assign use_rs1_o=jalr|branch|load|store|imm|regop|mul;
    assign use_rs2_o=branch|store|regop|mul;
    assign sys_op_o={ecall|ebreak,fence|ebreak};
endmodule
`default_nettype wire
