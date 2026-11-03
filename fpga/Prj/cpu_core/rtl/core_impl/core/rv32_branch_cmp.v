// RV32 条件分支比较器：有符号与无符号判断共享低 31 位比较逻辑。
module rv32_branch_cmp(input [31:0] a_i,b_i,input [2:0] branch_op_i,output taken_o);
    wire less_low=a_i[30:0]<b_i[30:0];
    wire sign_diff=a_i[31]^b_i[31];
    wire signed_less=sign_diff ? a_i[31] : less_low;
    wire unsigned_less=sign_diff ? b_i[31] : less_low;
    wire equal_value=a_i==b_i;
    wire base_condition=branch_op_i[2] ? (branch_op_i[1] ? unsigned_less : signed_less) : equal_value;
    wire legal=branch_op_i[2] || !branch_op_i[1];
    assign taken_o=legal && (base_condition ^ branch_op_i[0]);
endmodule
