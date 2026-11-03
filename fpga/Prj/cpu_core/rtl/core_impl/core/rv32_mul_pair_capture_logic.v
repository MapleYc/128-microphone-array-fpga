`default_nettype none
module rv32_mul_pair_capture_logic #(
    parameter LOCAL=0
)(
    input wire [31:0] a_i,b_i,
    input wire [3:0] alu_op_i,
    input wire [2:0] funct3_i,
    output wire [31:0] result_o
) /* synthesis syn_hier = "fixed" */;
    generate if(LOCAL) begin : g_local
        // 仅已通过配对许可的 AND/OR/XOR 结果可进入同组 MUL 操作数选择。
        assign result_o=funct3_i==3'd7 ? (a_i & b_i) :
                        funct3_i==3'd6 ? (a_i | b_i) : (a_i ^ b_i);
    end else begin : g_original
        rv32_mul_pair_logic u_logic(.a_i(a_i),.b_i(b_i),
            .alu_op_i(alu_op_i),.result_o(result_o));
    end endgenerate
endmodule
`default_nettype wire
