`default_nettype none
// 顺序三槽接纳、相关检查与资源配对。
// 保持原寄存边界与年龄优先级；组合输出由父模块统一接纳和提交。
module rv32_tri_issue #(
    parameter ALLOW_ALU_WAW = 1'b1,
    parameter BRANCH_ALU_PAIR = 1'b0,
    parameter integer BRANCH_PREDICT = 2,
    parameter DOUBLE_LOAD = 1'b1,
    parameter EX2_FORWARD_FROM_WB_ONLY = 1'b1,
    parameter LOAD_ADDR_BYPASS = 1'b0,
    parameter LOAD_BRANCH_PAIR = 1'b0,
    parameter LOAD_STORE_PAIR = 1'b1,
    parameter LOAD_USE_BYPASS = 1'b1,
    parameter LOCAL_MEMORY_LOOKAHEAD = 1'b0,
    parameter LOCAL_PAIR_DIRECT_DECODE = 1'b1,
    parameter LOCAL_PAIR_HAZARD_VIEW = 1'b1,
    parameter MACHINE_MODE = 1'b0,
    parameter MEM_HANDSHAKE = 1'b0,
    parameter MUL_ALU_PAIR = 1'b0,
    parameter SERIALIZE_LOCAL_ADDRESS_RAW = 1'b1,
    parameter SLOT1_MUL_PAIR = 1'b0,
    parameter SPECULATIVE_RAM_PAIR = 1'b1,
    parameter THIRD_ALU_BYPASS = 1'b1,
    parameter THIRD_ALU_SLOT = 1'b0,
    parameter THIRD_SOURCE_COFACTOR = 1'b1,
    parameter  FETCH_PREDECODE = 0,
    parameter  ROM_SCHEDULE_ACTIVE = 0
) (
    input wire  csr_illegal,
    input wire [31:0] ex0_forward_data,
    input wire  ex0_forwardable,
    input wire  ex0_is_mul,
    input wire  ex0_load_ready,
    input wire  ex0_mem_read,
    input wire [4:0] ex0_rd,
    input wire  ex0_writes,
    input wire [31:0] ex1_forward_data,
    input wire  ex1_forwardable,
    input wire  ex1_is_mul,
    input wire  ex1_load_ready,
    input wire  ex1_mem_read,
    input wire [4:0] ex1_rd,
    input wire  ex1_shift_wb,
    input wire  ex1_writes,
    input wire [31:0] ex2_id_forward_data,
    input wire [4:0] ex2_rd,
    input wire  ex2_writes,
    input wire  fast_mul_low,
    input wire  fetch_fault0,
    input wire  fetch_head0_valid,
    input wire  fetch_head1_valid,
    input wire  fetch_head2_valid,
    input wire [31:0] fetch_pc,
    input wire  fetch_ready,
    input wire  fetch_started,
    input wire  halted_o,
    input wire  id0_alu_b_sel,
    input wire [3:0] id0_alu_op,
    input wire  id0_csr,
    input wire  id0_eligible,
    input wire [1:0] id0_flow_type,
    input wire [31:0] id0_imm,
    input wire  id0_is_control,
    input wire  id0_is_mul,
    input wire [6:0] id0_issue_class,
    input wire  id0_mem_read,
    input wire  id0_mem_write,
    input wire [4:0] id0_rd,
    input wire  id0_reg_write,
    input wire [4:0] id0_rs1,
    input wire [31:0] id0_rs1_capture_data,
    input wire [31:0] id0_rs1_data,
    input wire [4:0] id0_rs2,
    input wire [31:0] id0_rs2_capture_data,
    input wire  id0_use_rs1,
    input wire  id0_use_rs2,
    input wire  id0_wait_ex,
    input wire [1:0] id0_wb_sel,
    input wire  id1_eligible,
    input wire [1:0] id1_flow_type,
    input wire  id1_mem_read,
    input wire  id1_mem_write,
    input wire [4:0] id1_rd,
    input wire  id1_reg_write,
    input wire [4:0] id1_rs1,
    input wire [31:0] id1_rs1_capture_data,
    input wire [31:0] id1_rs1_data,
    input wire [4:0] id1_rs2,
    input wire [31:0] id1_rs2_capture_data,
    input wire  id1_simple_alu,
    input wire  id1_simple_branch,
    input wire  id1_simple_load,
    input wire  id1_simple_mul,
    input wire  id1_simple_store,
    input wire  id1_use_rs1,
    input wire  id1_use_rs2,
    input wire  id1_wait_ex,
    input wire [31:0] id2_instr,
    input wire [4:0] id2_rd,
    input wire  id2_reg_write,
    input wire [4:0] id2_rs1,
    input wire [4:0] id2_rs2,
    input wire  id2_simple_alu,
    input wire  id2_use_rs1,
    input wire  id2_use_rs2,
    input wire  id2_wait_ex,
    input wire [31:0] id_word0,
    input wire [31:0] id_word1,
    input wire  if_branch_pred_taken,
    input wire [2:0] ifetch_error_i,
    input wire  machine_special,
    input wire  pipeline_empty,
    output  wire accept0,
    output  wire accept1,
    output  wire accept2,
    output  wire bad_slot0,
    output  wire id0_to_id1_pair_forwardable,
    output wire [31:0] id1_mul_capture_rs1,
    output wire [31:0] id1_mul_capture_rs2,
    output  wire pair_dependency,
    output  wire pair_raw_rs1,
    output  wire pair_raw_rs2,
    output  wire pair_stall,
    output  wire pair_structural_conflict
);
    function automatic shift_source_wait;
        input reg use_source, match2, match1, shift1;
        begin shift_source_wait=use_source && !match2 && match1 && shift1; end
    endfunction


    wire  pair_issue0;
    wire  pair_issue1;


    assign pair_raw_rs1 = id0_reg_write && id0_rd!=5'd0 &&
                        id1_use_rs1 && id1_rs1==id0_rd;
    assign pair_raw_rs2 = id0_reg_write && id0_rd!=5'd0 &&
                        id1_use_rs2 && id1_rs2==id0_rd;
    // 本地直连 ROM 发射路径使用译码器直接输出的普通 ALU 类别，减少控制编码往返。
    wire  id0_to_id1_alu_forwardable = (LOCAL_PAIR_DIRECT_DECODE && LOCAL_MEMORY_LOOKAHEAD && !FETCH_PREDECODE) ?
        (id0_issue_class[1] && id0_rd!=5'd0) :
        (id0_reg_write && id0_rd!=5'd0 && id0_wb_sel==2'd0 && !id0_mem_read &&
         !id0_mem_write && !id0_is_control && !id0_is_mul);
    wire  pair_mul_alu_allowed = MUL_ALU_PAIR && id0_is_mul && id1_simple_alu;
    wire  pair_load_branch_allowed;
    wire  id0_logic_mul_forwardable = (LOCAL_PAIR_DIRECT_DECODE && LOCAL_MEMORY_LOOKAHEAD && !FETCH_PREDECODE) ?
        (((id_word0[6:0] === 7'h13) ||
          ((id_word0[6:0] === 7'h33) && (id_word0[31:25] === 7'd0))) &&
         ((id_word0[14:12] === 3'd4) || (id_word0[14:12] === 3'd6) ||
          (id_word0[14:12] === 3'd7))) :
        (id0_alu_op==4'd5 || id0_alu_op==4'd8 || id0_alu_op==4'd9);
    // 本地模式下，依赖较老结果的 MUL 留到下一组执行。
    // 普通和提前配对入口共用该限制；独立 MUL 与 WAW 规则不变。
    assign id0_to_id1_pair_forwardable = (LOCAL_MEMORY_LOOKAHEAD && id1_simple_mul) ? 1'b0 :
        ((id0_to_id1_alu_forwardable && (!id1_simple_mul || id0_logic_mul_forwardable)) ||
         ((pair_mul_alu_allowed || pair_load_branch_allowed) && id0_reg_write && id0_rd!=5'd0));
    // 逻辑到 MUL 的候选数据仅保留 AND/OR/XOR，避免加法、比较和移位网络进入 APM 捕获端。
    wire [31:0] local_mul_pair_logic;
    rv32_mul_pair_capture_logic #(.LOCAL(LOCAL_MEMORY_LOOKAHEAD)) u_local_mul_pair_logic (
        .a_i(id0_rs1_capture_data),
        .b_i(LOCAL_MEMORY_LOOKAHEAD ? (id_word0[5] ? id0_rs2_capture_data : id0_imm) :
             (id0_alu_b_sel ? id0_imm : id0_rs2_capture_data)),
        .alu_op_i(id0_alu_op), .funct3_i(id_word0[14:12]),
        .result_o(local_mul_pair_logic)
    );
    // 当前本地配置的 APM 输入不接入同组运算结果。
    assign id1_mul_capture_rs1 = id1_rs1_capture_data;
    assign id1_mul_capture_rs2 = id1_rs2_capture_data;
    // 普通 ALU 可与年轻 ALU/MUL 同写 rd，年轻结果在写回和跨组前递中优先。
    wire  pair_waw_allowed = ALLOW_ALU_WAW && id0_to_id1_alu_forwardable &&
                            (id1_simple_alu || (SLOT1_MUL_PAIR && id1_simple_mul));
    // 以实际有效地址检查 RAM 范围，避免 base+立即数跨界后误双读 MMIO。
    // 同组 RAW/WAW 和上一拍 Load-use 仍由已有发射/等待逻辑处理。
    // Load 必用 rs1 和 I 型立即数。地址预计算不等待通用 use_rs1/imm_type 译码；
    // 是否使用预计算结果仍由下方合法 Load/RAW/Load-use 条件决定。
    // 不接 RAM 返回值，EX1 非 Load 的年轻结果仍优先于 EX0。
    wire [31:0] id0_load_base = (ex2_writes && id0_rs1==ex2_rd) ? ex2_id_forward_data :
        (ex1_forwardable && id0_rs1==ex1_rd) ? ex1_forward_data :
        (ex0_forwardable && id0_rs1==ex0_rd) ? ex0_forward_data : id0_rs1_data;
    wire [31:0] id1_load_base = (ex2_writes && id1_rs1==ex2_rd) ? ex2_id_forward_data :
        (ex1_forwardable && id1_rs1==ex1_rd) ? ex1_forward_data :
        (ex0_forwardable && id1_rs1==ex0_rd) ? ex0_forward_data : id1_rs1_data;
    wire [31:0] id0_load_imm = {{20{id_word0[31]}}, id_word0[31:20]};
    wire [31:0] id1_load_imm = {{20{id_word1[31]}}, id_word1[31:20]};
    wire [31:0] id0_load_addr = id0_load_base + id0_load_imm;
    wire [31:0] id1_load_addr = id1_load_base + id1_load_imm;
    // I型offset为[-2048,2047]，先算低11位进位，再判断高5位是否跨16位边界。
    // 只在base低16位接近0/ffff时才跨窗口，避免16位进位链串入配对/PC选择。
    function automatic load_address_in_ram;
        input reg [31:0] base;
        input reg [11:0] offset;
        reg [11:0] low_sum;
        reg same_page, inc_ok, dec_ok;
        reg carry0_ok, carry1_ok;
        begin
            low_sum = {1'b0,base[10:0]} + {1'b0,offset[10:0]};
            // 低位进位到达前，提前准备跨页与不跨页两种边界判断。
            same_page = base[31:16]==16'h1000;
            inc_ok = (same_page && !(&base[15:11])) ||
                     (base[31:16]==16'h0fff && (&base[15:11]));
            dec_ok = (same_page && (|base[15:11])) ||
                     (base[31:16]==16'h1001 && !(|base[15:11]));
            carry0_ok = offset[11] ? dec_ok : same_page;
            carry1_ok = offset[11] ? same_page : inc_ok;
            load_address_in_ram = low_sum[11] ? carry1_ok : carry0_ok;
        end
    endfunction
    // 先分别计算各生产者地址的 RAM 范围，再按最新生产者选择；优先级与 load_base 一致。
    wire  ram0_ex2 /* synthesis syn_keep=1 */ = load_address_in_ram(ex2_id_forward_data,id_word0[31:20]);
    wire  ram0_ex1 /* synthesis syn_keep=1 */ = load_address_in_ram(ex1_forward_data,id_word0[31:20]);
    wire  ram0_ex0 /* synthesis syn_keep=1 */ = load_address_in_ram(ex0_forward_data,id_word0[31:20]);
    wire  ram0_rf /* synthesis syn_keep=1 */ = load_address_in_ram(id0_rs1_data,id_word0[31:20]);
    wire  ram1_ex2 /* synthesis syn_keep=1 */ = load_address_in_ram(ex2_id_forward_data,id_word1[31:20]);
    wire  ram1_ex1 /* synthesis syn_keep=1 */ = load_address_in_ram(ex1_forward_data,id_word1[31:20]);
    wire  ram1_ex0 /* synthesis syn_keep=1 */ = load_address_in_ram(ex0_forward_data,id_word1[31:20]);
    wire  ram1_rf /* synthesis syn_keep=1 */ = load_address_in_ram(id1_rs1_data,id_word1[31:20]);
    wire  id0_load_in_ram = (ex2_writes && id0_rs1==ex2_rd) ? ram0_ex2 :
        (ex1_forwardable && id0_rs1==ex1_rd) ? ram0_ex1 :
        (ex0_forwardable && id0_rs1==ex0_rd) ? ram0_ex0 : ram0_rf;
    wire  id1_load_in_ram = (ex2_writes && id1_rs1==ex2_rd) ? ram1_ex2 :
        (ex1_forwardable && id1_rs1==ex1_rd) ? ram1_ex1 :
        (ex0_forwardable && id1_rs1==ex0_rd) ? ram1_ex0 : ram1_rf;
    // ID 地址预计算没有 RAM 返回值：若 rs1 等待 EX Load，就算允许提前捕获，
    // 也只能使用主 LSU 顺序访存，不能用旧基址将 MMIO/越界访问误判为双 RAM Load。
    wire  id0_load_addr_pending = LOAD_USE_BYPASS && LOAD_ADDR_BYPASS && id0_use_rs1 &&
        !(ex2_writes && id0_rs1==ex2_rd) &&
        ((ex0_writes && ex0_mem_read && id0_rs1==ex0_rd) ||
         (ex1_writes && ex1_mem_read && id0_rs1==ex1_rd));
    wire  id1_load_addr_pending = LOAD_USE_BYPASS && LOAD_ADDR_BYPASS && id1_use_rs1 &&
        !(ex2_writes && id1_rs1==ex2_rd) &&
        ((ex0_writes && ex0_mem_read && id1_rs1==ex0_rd) ||
         (ex1_writes && ex1_mem_read && id1_rs1==ex1_rd));
    wire  pair_double_load_allowed = DOUBLE_LOAD && id0_mem_read && id1_simple_load &&
                                   !id0_load_addr_pending && !id1_load_addr_pending &&
                                   (SPECULATIVE_RAM_PAIR || (id0_load_in_ram && id1_load_in_ram));
    // 通过相同基址寄存器与相同偏移判断同址，不额外串接第二次完整有效地址求和。
    // RAW、最新生产者和 Load 地址等待检查仍必须全部满足。
    wire  pair_load_store_allowed = LOAD_STORE_PAIR && DOUBLE_LOAD && id0_mem_read &&
        id_word0[14:12]==3'b010 && id1_simple_store && id_word1[14:12]==3'b010 &&
        id0_rs1==id1_rs1 && id_word0[31:20]=={id_word1[31:25],id_word1[11:7]} &&
        !id0_load_addr_pending && !id1_load_addr_pending &&
        (SPECULATIVE_RAM_PAIR || (id0_load_in_ram &&
         ((id0_load_base[1:0] + id_word0[21:20])==2'b00)));
    // 仅开放Load到条件分支，真实地址是否为普通RAM在EX确认；非RAM重放分支。
    // 不把Load返回值接入IF地址预计算，也不放开同组ALU或访存地址Load RAW。
    assign pair_load_branch_allowed = LOAD_BRANCH_PAIR && id0_mem_read && id1_simple_branch;
    // 仅在完整合法发射条件之下，使用操作码直接判断相关字段。
    function automatic [3:0] pair_opcode_hazards;
        input reg [31:0] ins;
        reg lu,au,ja,jr,ld,st,br,ai,ar;
        begin
            lu=(ins[6:0] === 7'h37);au=(ins[6:0] === 7'h17);
            ja=(ins[6:0] === 7'h6f);jr=(ins[6:0] === 7'h67);
            ld=(ins[6:0] === 7'h03);st=(ins[6:0] === 7'h23);
            br=(ins[6:0] === 7'h63);ai=(ins[6:0] === 7'h13);
            ar=(ins[6:0] === 7'h33);
            pair_opcode_hazards={lu|au|ja|jr|ld|ai|ar,
                jr|ld|st|br|ai|ar,st|br|ar,ld|st};
        end
    endfunction
    wire [3:0] pair_view0 = pair_opcode_hazards(id_word0);
    wire [3:0] pair_view1 = pair_opcode_hazards(id_word1);
    wire  pair_issue1_original;
    wire  pair_issue1_fast;
    rv32_issue_pair u_pair_issue (
        .slot0_valid_i(fetch_started && !halted_o && fetch_head0_valid),
        .slot0_reg_write_i(id0_reg_write), .slot0_rd_i(id0_rd),
        .slot0_use_rs1_i(id0_use_rs1), .slot0_use_rs2_i(id0_use_rs2),
        .slot0_rs1_i(id0_rs1), .slot0_rs2_i(id0_rs2),
        .slot0_mem_i(id0_mem_read || id0_mem_write),
        .slot1_valid_i(fetch_started && !halted_o && fetch_head1_valid), .slot1_eligible_i(id1_eligible),
        .slot1_reg_write_i(id1_reg_write), .slot1_rd_i(id1_rd),
        .slot1_use_rs1_i(id1_use_rs1), .slot1_use_rs2_i(id1_use_rs2),
        .slot1_rs1_i(id1_rs1), .slot1_rs2_i(id1_rs2),
        .slot1_mem_i(id1_mem_read || id1_mem_write),
        .slot0_to_slot1_forward_i(id0_to_id1_pair_forwardable),
        .slot0_to_slot1_waw_i(pair_waw_allowed),
        .slot0_to_slot1_load_i(pair_double_load_allowed || pair_load_store_allowed),
        .issue0_o(pair_issue0), .issue1_o(pair_issue1_original),
        .pair_dependency_o(pair_dependency),
        .pair_structural_conflict_o(pair_structural_conflict)
    );
    rv32_issue_pair u_pair_issue_early (
        .slot0_valid_i(fetch_started && !halted_o && fetch_head0_valid),
        .slot0_reg_write_i(pair_view0[3]), .slot0_rd_i(id0_rd),
        .slot0_use_rs1_i(pair_view0[2]), .slot0_use_rs2_i(pair_view0[1]),
        .slot0_rs1_i(id0_rs1), .slot0_rs2_i(id0_rs2),
        .slot0_mem_i(pair_view0[0]),
        .slot1_valid_i(fetch_started && !halted_o && fetch_head1_valid), .slot1_eligible_i(id1_eligible),
        .slot1_reg_write_i(pair_view1[3]), .slot1_rd_i(id1_rd),
        .slot1_use_rs1_i(pair_view1[2]), .slot1_use_rs2_i(pair_view1[1]),
        .slot1_rs1_i(id1_rs1), .slot1_rs2_i(id1_rs2),
        .slot1_mem_i(pair_view1[0]),
        .slot0_to_slot1_forward_i(id0_to_id1_pair_forwardable),
        .slot0_to_slot1_waw_i(pair_waw_allowed),
        .slot0_to_slot1_load_i(pair_double_load_allowed || pair_load_store_allowed),
        .issue0_o(), .issue1_o(pair_issue1_fast),
        .pair_dependency_o(),
        .pair_structural_conflict_o()
    );
    assign pair_issue1=(LOCAL_PAIR_HAZARD_VIEW && LOCAL_MEMORY_LOOKAHEAD && !FETCH_PREDECODE && !ROM_SCHEDULE_ACTIVE)
        ? pair_issue1_fast : pair_issue1_original;


    // slot1 的跨周期数据相关不应阻塞独立的 slot0；slot1 不接纳时
    // PC 只前进4字节，下一拍将其重放为 slot0。
    assign pair_stall = id0_wait_ex;
    assign accept0 = pair_issue0 && id0_eligible && !pair_stall && fetch_ready && !fetch_fault0;
    // 只投机发射预测不跳分支后的普通 ALU；每组最多一个控制流。
    wire  pair_branch_alu_allowed = BRANCH_ALU_PAIR && BRANCH_PREDICT!=0 &&
                                  id0_flow_type==2'd1 && !if_branch_pred_taken &&
                                  id1_simple_alu;
    wire  local_address_raw_split = LOCAL_MEMORY_LOOKAHEAD && SERIALIZE_LOCAL_ADDRESS_RAW &&
                                   (id1_mem_read || id1_mem_write) && pair_raw_rs1;
    assign accept1 = pair_issue1 && id0_eligible && (!id0_is_control || pair_branch_alu_allowed) &&
                   (!id0_is_mul || pair_mul_alu_allowed) && !id1_wait_ex && !pair_stall &&
                   !local_address_raw_split && fetch_ready && (!MEM_HANDSHAKE || ifetch_error_i[1:0]==0);
    // 第三槽只扩展连续指令前缀，不跳过较老指令；同组前递由下列条件限制。
    wire  third_raw0 = id0_reg_write && id0_rd!=5'd0 &&
                      ((id2_use_rs1 && id2_rs1==id0_rd) || (id2_use_rs2 && id2_rs2==id0_rd));
    wire  third_raw1 = id1_reg_write && id1_rd!=5'd0 &&
                      ((id2_use_rs1 && id2_rs1==id1_rd) || (id2_use_rs2 && id2_rs2==id1_rd));
    wire  third_waw = id2_reg_write && id2_rd!=5'd0 &&
                     ((id0_reg_write && id0_rd==id2_rd) || (id1_reg_write && id1_rd==id2_rd));
    // 分别检查每个实际源。消费 ALU0 的槽 2 可与依赖 ALU0 的槽 1 并行；
    // 消费 ALU1 时不得形成第三级串行运算。同址生产者必须同时满足两项安全条件，年轻值优先。
    wire  third_raw0_safe = !third_raw0 ||
                           (THIRD_ALU_BYPASS && id0_to_id1_alu_forwardable);
    wire  third_raw1_safe = !third_raw1 ||
                           (THIRD_ALU_BYPASS && id1_simple_alu && !pair_raw_rs1 && !pair_raw_rs2);
    // 第三槽指令资格仍由完整译码器决定；相关判断提前使用操作码源字段。
    // 非 ALU 指令不得发射，捕获数据选择保持不变。
    wire  id2_early_use_rs1 = (id2_instr[6:0] === 7'h13) || (id2_instr[6:0] === 7'h33);
    wire  id2_early_use_rs2 = (id2_instr[6:0] === 7'h33);
    wire  id2_issue_rs1_match2 = id2_early_use_rs1 && ex2_writes && id2_rs1==ex2_rd;
    wire  id2_issue_rs2_match2 = id2_early_use_rs2 && ex2_writes && id2_rs2==ex2_rd;
    wire  id2_issue_rs1_match1 = id2_early_use_rs1 && ex1_writes && id2_rs1==ex1_rd && !id2_issue_rs1_match2;
    wire  id2_issue_rs2_match1 = id2_early_use_rs2 && ex1_writes && id2_rs2==ex1_rd && !id2_issue_rs2_match2;
    wire  id2_issue_rs1_match0 = id2_early_use_rs1 && ex0_writes && id2_rs1==ex0_rd &&
                          !id2_issue_rs1_match2 && !id2_issue_rs1_match1;
    wire  id2_issue_rs2_match0 = id2_early_use_rs2 && ex0_writes && id2_rs2==ex0_rd &&
                          !id2_issue_rs2_match2 && !id2_issue_rs2_match1;
    wire  id2_issue_ex2_wait = EX2_FORWARD_FROM_WB_ONLY && (id2_issue_rs1_match2 || id2_issue_rs2_match2);
    wire  id2_issue_fast_mul_wait = fast_mul_low &&
        (((id2_issue_rs1_match1 || id2_issue_rs2_match1) && ex1_is_mul) ||
         ((id2_issue_rs1_match0 || id2_issue_rs2_match0) && ex0_is_mul));
    wire  id2_issue_shift_wait = shift_source_wait(id2_early_use_rs1,id2_issue_rs1_match2,id2_issue_rs1_match1,ex1_shift_wb) ||
        shift_source_wait(id2_early_use_rs2,id2_issue_rs2_match2,id2_issue_rs2_match1,ex1_shift_wb);
    wire  id2_issue_wait_ex = id2_issue_shift_wait || id2_issue_ex2_wait || id2_issue_fast_mul_wait ||
                       ((id2_issue_rs1_match1 || id2_issue_rs2_match1) && ex1_mem_read && !ex1_load_ready) ||
                       ((id2_issue_rs1_match0 || id2_issue_rs2_match0) && ex0_mem_read && !ex0_load_ready);
    wire  third_issue_raw0 = id0_reg_write && id0_rd!=5'd0 &&
                      ((id2_early_use_rs1 && id2_rs1==id0_rd) || (id2_early_use_rs2 && id2_rs2==id0_rd));
    wire  third_issue_raw1 = id1_reg_write && id1_rd!=5'd0 &&
                      ((id2_early_use_rs1 && id2_rs1==id1_rd) || (id2_early_use_rs2 && id2_rs2==id1_rd));
    wire  third_issue_waw = id2_rd!=5'd0 &&
                     ((id0_reg_write && id0_rd==id2_rd) || (id1_reg_write && id1_rd==id2_rd));
    // 分别检查每个实际源。消费 ALU0 的槽 2 可与依赖 ALU0 的槽 1 并行；
    // 消费 ALU1 时不得形成第三级串行运算。同址生产者必须同时满足两项安全条件，年轻值优先。
    wire  third_issue_raw0_safe = !third_issue_raw0 ||
                           (THIRD_ALU_BYPASS && id0_to_id1_alu_forwardable);
    wire  third_issue_raw1_safe = !third_issue_raw1 ||
                           (THIRD_ALU_BYPASS && id1_simple_alu && !pair_raw_rs1 && !pair_raw_rs2);
    wire  accept2_early = THIRD_ALU_SLOT && fetch_head2_valid && accept0 && accept1 &&
                   id0_flow_type==2'd0 && id1_flow_type==2'd0 && id2_simple_alu &&
                   third_issue_raw0_safe && third_issue_raw1_safe &&
                   !third_issue_waw && !id2_issue_wait_ex && (!MEM_HANDSHAKE || !ifetch_error_i[2]);
    wire  accept2_original = THIRD_ALU_SLOT && fetch_head2_valid && accept0 && accept1 &&
                   id0_flow_type==2'd0 && id1_flow_type==2'd0 && id2_simple_alu &&
                   third_raw0_safe && third_raw1_safe &&
                   !third_waw && !id2_wait_ex && (!MEM_HANDSHAKE || !ifetch_error_i[2]);
    assign accept2 = THIRD_SOURCE_COFACTOR ? accept2_early : accept2_original;
    assign bad_slot0 = fetch_started && fetch_ready &&
        (fetch_fault0 || (MACHINE_MODE && fetch_pc[1:0]!=0) ||
         (!id0_eligible && !machine_special) ||
         (machine_special && id0_csr && pipeline_empty && csr_illegal));

endmodule
`default_nettype wire
