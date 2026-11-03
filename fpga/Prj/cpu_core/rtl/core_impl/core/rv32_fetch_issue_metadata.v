`default_nettype none
`default_nettype wire

// 静态预译码复用同一个指令译码器，避免形成第二套 ISA 判定规则。
module rv32_fetch_issue_metadata(
    input wire [31:0] instr_i,
    output wire [10:0] metadata_o
);
    rv32_decoder u_decode(
        .instr_i(instr_i),.imm_type_o(),.alu_op_o(),.alu_a_sel_o(),.alu_b_sel_o(),
        .wb_sel_o(),.flow_type_o(),.branch_op_o(),.lsu_type_o(),.sys_op_o(),
        .illegal_o(metadata_o[10]),.reg_write_o(metadata_o[9]),
        .use_rs2_o(metadata_o[8]),.use_rs1_o(metadata_o[7]),
        .issue_class_o(metadata_o[6:0]),.mem_read_o(),.mem_write_o());
endmodule
`default_nettype wire
