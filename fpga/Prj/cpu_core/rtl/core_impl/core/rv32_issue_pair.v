`default_nettype none
// 相邻两条指令的配对判定；三槽核使用本模块判断最老两槽的接纳条件。
// slot0 是较老指令，slot1 是较新指令。该模块只决定 slot1 是否可以
// 与 slot0 同周期发射，不执行指令；第三槽接纳由 rv32_tri_issue 单独判断。
//
// slot1 的指令资格由核心传入；双访存只对核心确认的独立 RAM Load 放开。
// RAW 只在核心提供同组前递时放开，WAW 只在核心明确保证年轻结果优先时放开。
module rv32_issue_pair(
    input  wire       slot0_valid_i,
    input  wire       slot0_reg_write_i,
    input  wire [4:0] slot0_rd_i,
    input  wire       slot0_use_rs1_i,
    input  wire       slot0_use_rs2_i,
    input  wire [4:0] slot0_rs1_i,
    input  wire [4:0] slot0_rs2_i,
    input  wire       slot0_mem_i,

    input  wire       slot1_valid_i,
    input  wire       slot1_eligible_i,
    input  wire       slot1_reg_write_i,
    input  wire [4:0] slot1_rd_i,
    input  wire       slot1_use_rs1_i,
    input  wire       slot1_use_rs2_i,
    input  wire [4:0] slot1_rs1_i,
    input  wire [4:0] slot1_rs2_i,
    input  wire       slot1_mem_i,
    // slot0 为可组合前递的普通 ALU 指令时，允许同组 RAW；Load RAW 保守重放。
    input  wire       slot0_to_slot1_forward_i,
    // 核心只对双普通 ALU 开放 WAW；年轻槽位写回/跨组前递必须优先。
    input  wire       slot0_to_slot1_waw_i,
    // 只在核心确认两条均为普通 RAM Load 时放开双访存。
    input  wire       slot0_to_slot1_load_i,

    output wire       issue0_o,
    output wire       issue1_o,
    output wire       pair_dependency_o,
    output wire       pair_structural_conflict_o
);
    wire slot0_writes_real_rd = slot0_valid_i && slot0_reg_write_i &&
                                 (slot0_rd_i != 5'd0);
    wire slot1_writes_real_rd = slot1_valid_i && slot1_reg_write_i &&
                                 (slot1_rd_i != 5'd0);

    wire raw_dependency = slot0_writes_real_rd && slot1_valid_i &&
        ((slot1_use_rs1_i && slot1_rs1_i == slot0_rd_i) ||
         (slot1_use_rs2_i && slot1_rs2_i == slot0_rd_i));

    wire waw_dependency = slot0_writes_real_rd && slot1_writes_real_rd &&
                          (slot0_rd_i == slot1_rd_i);

    // 检测双访存；只有 slot0_to_slot1_load_i 可解除结构冲突，RAW/WAW 仍独立检查。
    wire dual_memory = slot0_valid_i && slot0_mem_i &&
                       slot1_valid_i && slot1_mem_i;

    assign issue0_o = slot0_valid_i;
    assign issue1_o = slot1_valid_i && slot1_eligible_i &&
                       (!raw_dependency || slot0_to_slot1_forward_i) &&
                       (!waw_dependency || slot0_to_slot1_waw_i) &&
                       (!dual_memory || slot0_to_slot1_load_i);
    assign pair_dependency_o = raw_dependency || waw_dependency;
    assign pair_structural_conflict_o = (dual_memory && !slot0_to_slot1_load_i) ||
                                        (slot1_valid_i && !slot1_eligible_i);
endmodule
`default_nettype wire
