`default_nettype none
// 顺序三槽 RV32I/Zmmul 核：槽 0 最老，槽 2 最年轻，只发射连续指令前缀。
// 槽 0/1 具有整数运算及受规则限制的访存、乘法和控制流能力；共享一个乘法器。
// 槽 2 仅执行普通整数 ALU，不执行访存、乘法或控制流；不是三个全功能执行槽。
// 预测、执行访存、操作数前递/等待和顺序发射分别由四个子模块实现。
// 本模块保留译码、寄存器堆、流水状态及异常/重定向仲裁，提交保持程序顺序。
// clk 上升沿更新；rst_n 异步低有效。载荷是否有效由 valid 决定，无效载荷不解释。
// MACHINE_MODE=1 时使用机器态陷阱与 MRET；关闭时保留排空后的诊断停机接口。
// 参数默认值兼容既有独立测试；正式 rv32_core_bus 显式开启第三槽与机器态。
// 子模块化只调整职责与层级，不新增流水拍。物理时序和资源须独立重新验收。
module rv32_core_tri #(
    parameter [31:0] RESET_PC = 32'h0000_0000,
    parameter IF_EARLY_JAL = 1'b1,
    parameter integer BRANCH_PREDICT = 2,
    parameter integer BHT_INDEX_BITS = 8,
    parameter SLOT1_DYNAMIC_BHT = 1'b1,
    parameter ALLOW_ALU_WAW = 1'b1,
    parameter DOUBLE_LOAD = 1'b1,
    parameter LOAD_USE_BYPASS = 1'b1,
    // 仅允许上一组有效 RAM Load 前递到已有操作数寄存器，不允许同组 RAM Load 直接驱动 MUL。
    parameter LOAD_MUL_BYPASS = 1'b1,
    parameter MUL_ALU_PAIR = 1'b0,
    parameter SLOT1_MUL_PAIR = 1'b0,
    parameter BRANCH_ALU_PAIR = 1'b0,
    // 底层独立实例默认保持旧等待；当前 SoC、板级顶层及完整 CoreMark TB 显式开启。
    parameter STORE_DATA_BYPASS = 1'b0,
    parameter LOAD_ADDR_BYPASS = 1'b0,
    parameter PARALLEL_BHT_READ = 1'b1,
    parameter LOOP_PREDICT = 1'b0,
    parameter integer LOOP_COUNTER_BITS = 16,
    parameter JALR_BTB = 1'b0,
    parameter RAS_PREDICT = 1'b0,
    parameter SLOT1_RET_PREDICT = 1'b0,
    parameter LOAD_BRANCH_PAIR = 1'b0,
    // 第三槽配置：仅普通整数 ALU，不增加 LSU、MUL 或控制流执行资源。
    parameter THIRD_ALU_SLOT = 1'b0,
    // 第三槽可接收前两槽独立 ALU 的结果，但禁止形成三个 ALU 依次串联的组合链。
    parameter THIRD_ALU_BYPASS = 1'b1,
    // 共享乘法器使用结果寄存时，为相关指令组保留一次准备等待。
    parameter REGISTER_MUL_RESULT = 1'b1,
    // 独立的本地低 32 位乘法可在既有 WB 边沿完成。
    parameter FAST_MUL_LOW = 1'b1,
    // 第三槽跨组消费者等待 WB，以切断 EX2 到下一组译码/地址的组合连接。
    parameter EX2_FORWARD_FROM_WB_ONLY = 1'b1,
    // 寄存同组有效 RAM Load 到条件分支的结果，切断访存到取指的反馈链。
    parameter REGISTER_LOAD_BRANCH_RESULT = 1'b1,
    // 双 Load 或 LW/SW 配对在 EX 核验地址合法性，再允许年轻访存请求。
    parameter SPECULATIVE_RAM_PAIR = 1'b1,
    // 本地模式下将同组生产者到年轻访存地址的 RAW 相关拆成顺序发射。
    parameter SERIALIZE_LOCAL_ADDRESS_RAW = 1'b1,
    // 允许普通 RAM 内同址、字对齐的较老 LW 与年轻 SW 配对。
    parameter LOAD_STORE_PAIR = 1'b1,
    parameter MACHINE_MODE = 1'b0,
    parameter MEM_HANDSHAKE = 1'b0,
    // 本地同步存储使用提前地址与结果捕获；通用握手模式保持请求和响应协议。
    parameter LOCAL_MEMORY_LOOKAHEAD = 1'b0,
    parameter REGISTER_LOCAL_FETCH = 1'b0,
    // 静态预译码复用已有取指窗口边界，不额外增加流水拍。
    parameter PREDECODE_LOCAL_FETCH = 1'b1,
    parameter LOCAL_SHIFT_FROM_WB_ONLY = 1'b1,
    parameter LOCAL_SMALL_SHIFT_FORWARD = 1'b1,
    parameter LOCAL_LOAD0_PAYLOAD_VIEW = 1'b0,
    parameter LOCAL_LOAD1_FROM_WB_ONLY = 1'b1,
    parameter LOCAL_BRANCH_SOURCE_COFACTOR = 1'b1,
    parameter LOCAL_LOAD0_READY_COFACTOR = 1'b1,
    parameter LOCAL_PAIR_DIRECT_DECODE = 1'b1,
    parameter LOCAL_PAIR_HAZARD_VIEW = 1'b1,
    parameter ROM_SCHEDULE = 1'b0,
    // 在已有 EX 捕获边沿保存 ID 提前计算的有效地址。
    parameter CAPTURE_LOCAL_ADDRESS = 1'b1,
    parameter LOCAL_MEMORY_RESPONSE_CLASS = 1'b0,
    parameter THIRD_SOURCE_COFACTOR = 1'b1,
    parameter ISSUE_HEAD_COFACTOR = 1'b1
)(
    input  wire        clk,
    input  wire        rst_n,
    output wire [31:0] imem_addr0_o,
    input  wire [31:0] imem_rdata0_i,
    output wire [31:0] imem_addr1_o,
    input  wire [31:0] imem_rdata1_i,
    output wire [31:0] imem_addr2_o,
    input  wire [31:0] imem_rdata2_i,
    input wire [32:0] imem_schedule_i,

    output wire        dmem_read_o,
    output wire        dmem_write_o,
    output wire [31:0] dmem_addr_o,
    output wire [31:0] dmem_wdata_o,
    output wire [3:0]  dmem_wstrb_o,
    input  wire [31:0] dmem_rdata_i,
    input  wire [31:0] dmem_local_payload_i,
    // 第二接口只承载同组两条独立 RAM Load 中的 slot1，无写口/MMIO。
    output wire        dmem1_read_o,
    output wire [31:0] dmem1_addr_o,
    input  wire [31:0] dmem1_rdata_i,

    output reg         halted_o,
    output reg  [31:0] halt_pc_o,
    output reg  [31:0] halt_instr_o,
    output reg  [3:0]  halt_cause_o,

    output wire        commit0_valid_o,
    output wire [31:0] commit0_pc_o,
    output wire [31:0] commit0_instr_o,
    output wire        commit0_rd_write_o,
    output wire [4:0]  commit0_rd_addr_o,
    output wire [31:0] commit0_rd_data_o,

    output wire        commit1_valid_o,
    output wire [31:0] commit1_pc_o,
    output wire [31:0] commit1_instr_o,
    output wire        commit1_rd_write_o,
    output wire [4:0]  commit1_rd_addr_o,
    output wire [31:0] commit1_rd_data_o,

    output wire        commit2_valid_o,
    output wire [31:0] commit2_pc_o,
    output wire [31:0] commit2_instr_o,
    output wire        commit2_rd_write_o,
    output wire [4:0]  commit2_rd_addr_o,
    output wire [31:0] commit2_rd_data_o,

    input wire ifetch_valid_i,
    input wire [2:0] ifetch_error_i,
    input wire dmem_req_ready_i, dmem_rsp_valid_i, dmem_rsp_error_i,
    output wire dmem_rsp_ready_o,
    input wire dmem1_req_ready_i, dmem1_rsp_valid_i, dmem1_rsp_error_i,
    input wire [1:0] dmem_local_ram_i,
    output wire dmem1_rsp_ready_o,
    output wire dmem1_write_o,
    output wire [31:0] dmem1_wdata_o,
    output wire [3:0] dmem1_wstrb_o,
    input wire irq_external_i, irq_software_i, irq_timer_i,
    output reg trap_valid_o,
    output reg [31:0] trap_pc_o, trap_cause_o, trap_tval_o,
    output wire [31:0] imem_next_pc_o,
    output wire [13:0] imem_next_word1_o, imem_next_word2_o,
    output wire [31:0] dmem_preview_addr0_o,dmem_preview_addr1_o,
    output wire [31:0] dmem_preview_load1_o,
    output wire dmem_ram_read_ahead1_o,
    output wire [1:0] dmem_preview_valid_o
);
    reg [31:0] fetch_pc;
    reg  fetch_started;
    localparam  FETCH_WINDOW = LOCAL_MEMORY_LOOKAHEAD && THIRD_ALU_SLOT && REGISTER_LOCAL_FETCH;
    localparam  FETCH_PREDECODE = FETCH_WINDOW && PREDECODE_LOCAL_FETCH;
    localparam  ROM_SCHEDULE_ACTIVE = LOCAL_MEMORY_LOOKAHEAD && ROM_SCHEDULE && !FETCH_WINDOW;
    localparam  ID_PREDECODE = FETCH_PREDECODE || ROM_SCHEDULE_ACTIVE;
    wire [10:0] fetch_metadata0;
    wire [10:0] fetch_metadata1;
    wire [10:0] fetch_metadata2;
    localparam  CAPTURE_ADDRESS = LOCAL_MEMORY_LOOKAHEAD && SERIALIZE_LOCAL_ADDRESS_RAW && CAPTURE_LOCAL_ADDRESS;
    wire [31:0] local_id_address0;
    wire [31:0] local_id_address1;
    wire [31:0] local_ex_address0;
    wire [31:0] local_ex_address1;
    wire [31:0] id_word0;
    wire [31:0] id_word1;
    wire [31:0] id_word2;
    wire [2:0] fetch_word_count;
    wire  fetch_head0_valid = !FETCH_WINDOW || fetch_word_count>=1;
    wire  fetch_head1_valid = !FETCH_WINDOW || fetch_word_count>=2;
    wire  fetch_head2_valid = !FETCH_WINDOW || fetch_word_count>=3;
    wire  fetch_ready = (!MEM_HANDSHAKE || ifetch_valid_i) && fetch_head0_valid;
    wire [31:0] legacy_next_pc;
    wire [31:0] window_request_pc;
    wire [31:0] window_sequential_pc;
    wire [13:0] legacy_word1;
    wire [13:0] legacy_word2;
    wire [1:0] fetch_pop;
    wire  fetch_flush;
    generate if(FETCH_WINDOW)begin:g_fetch_window
        rv32_local_fetch_window #(.RESET_PC(RESET_PC),.PREDECODE(FETCH_PREDECODE)) u_window(
            .clk(clk),.rst_n(rst_n),.pop_i(fetch_pop),.flush_i(fetch_flush),
            .target_i(legacy_next_pc),.response_i({imem_rdata2_i,imem_rdata1_i,imem_rdata0_i}),
            .heads_o({id_word2,id_word1,id_word0}),.count_o(fetch_word_count),
            .metadata_o({fetch_metadata2,fetch_metadata1,fetch_metadata0}),
            .request_pc_o(window_request_pc),.sequential_request_pc_o(window_sequential_pc));
        assign imem_next_pc_o=window_request_pc;
        wire [13:0] sequential_word1=window_sequential_pc[15:2]+14'd1;
        wire [13:0] sequential_word2=window_sequential_pc[15:2]+14'd2;
        assign imem_next_word1_o=!rst_n ? RESET_PC[15:2]+14'd1 : fetch_flush ? legacy_word1 : sequential_word1;
        assign imem_next_word2_o=!rst_n ? RESET_PC[15:2]+14'd2 : fetch_flush ? legacy_word2 : sequential_word2;
    end else begin:g_no_fetch_window
        assign {fetch_metadata2,fetch_metadata1,fetch_metadata0}=ROM_SCHEDULE_ACTIVE ? imem_schedule_i : 33'b0;
        assign {id_word2,id_word1,id_word0}={imem_rdata2_i,imem_rdata1_i,imem_rdata0_i};
        assign fetch_word_count=3;
        assign window_request_pc=legacy_next_pc;
        assign window_sequential_pc=legacy_next_pc;
        assign imem_next_pc_o=legacy_next_pc;
        assign imem_next_word1_o=legacy_word1;
        assign imem_next_word2_o=legacy_word2;
    end endgenerate
    wire  fetch_fault0 = MEM_HANDSHAKE && ifetch_error_i[0];
    wire  ex_advance;
    // 保留候选地址网络，禁止资源共享把晚到的配对/预测选择串入 PC 加法器。
    wire [31:0] fetch_pc_plus4 /* synthesis syn_keep=1 */ = fetch_pc + 32'd4;
    wire [31:0] fetch_pc_plus8 /* synthesis syn_keep=1 */ = fetch_pc + 32'd8;
    wire [31:0] fetch_pc_plus12 /* synthesis syn_keep=1 */ = THIRD_ALU_SLOT ? fetch_pc + 32'd12 : 32'b0;
    assign imem_addr0_o = fetch_pc;
    assign imem_addr1_o = fetch_pc_plus4;
    assign imem_addr2_o = THIRD_ALU_SLOT ? fetch_pc_plus8 : 32'b0;

    // 分支方向、循环、间接跳转与返回地址预测：下列信号连接对应子模块。
    wire [31:0] btb_train_pc;
    wire [31:0] btb_train_target;
    wire  btb_train_valid;
    wire  id0_eligible;
    wire  id0_is_control;
    wire  id0_is_mul;
    wire  id0_ras_pop_hint;
    wire  id0_ras_push_hint;
    wire  id1_eligible;
    wire  id1_ras_push_hint;
    wire  id1_ret_candidate;
    wire  id1_return;
    wire  id1_simple_alu;
    wire  id1_simple_branch;
    wire  id1_simple_load;
    wire  id1_simple_mul;
    wire  id1_simple_store;
    wire  id2_simple_alu;
    wire  if_branch_pred_taken;
    wire [31:0] if_branch_target;
    wire [31:0] if_btb_target;
    wire  if_jal_candidate;
    wire [31:0] if_jal_target;
    wire [31:0] if_jalr_pred_target;
    wire  if_jalr_pred_valid;
    wire  if_jalr_return;
    wire [31:0] if_ras_target;
    wire  if_slot1_branch_pred_taken;
    wire [31:0] if_slot1_branch_target;
    wire  if_slot1_jal_candidate;
    wire [31:0] if_slot1_jal_target;
    wire [31:0] loop_train_pc;
    wire  loop_train_taken;
    wire  loop_train_valid;
    wire  ras_pop;
    wire  ras_push;
    wire [31:0] ras_push_pc;
    wire  ras_update_valid;

    // ---------------- ID：三槽译码与指令元数据 ----------------
    wire [4:0] id0_rs1 = id_word0[19:15];
    wire [4:0] id0_rs2 = id_word0[24:20];
    wire [4:0] id0_rd = id_word0[11:7];
    wire [4:0] id1_rs1 = id_word1[19:15];
    wire [4:0] id1_rs2 = id_word1[24:20];
    wire [4:0] id1_rd = id_word1[11:7];
    // 开关关闭时不解释未连接的第三取指输入。
    wire [31:0] id2_instr = THIRD_ALU_SLOT ? id_word2 : 32'h0000_0013;
    wire [4:0] id2_rs1 = id2_instr[19:15];
    wire [4:0] id2_rs2 = id2_instr[24:20];
    wire [4:0] id2_rd = id2_instr[11:7];
    wire [2:0] id2_imm_type;
    wire [2:0] id2_branch_op;
    wire [2:0] id2_lsu_type;
    wire [3:0] id2_alu_op;
    wire [1:0] id2_alu_a_sel;
    wire [1:0] id2_wb_sel;
    wire [1:0] id2_flow_type;
    wire [1:0] id2_sys_op;
    wire  id2_alu_b_sel;
    wire  id2_reg_write;
    wire  id2_mem_read;
    wire  id2_mem_write;
    wire  id2_use_rs1;
    wire  id2_use_rs2;
    wire  id2_illegal;
    wire [31:0] id2_imm;

    wire [2:0] id0_imm_type;
    wire [2:0] id1_imm_type;
    wire [3:0] id0_alu_op;
    wire [3:0] id1_alu_op;
    wire [1:0] id0_alu_a_sel;
    wire [1:0] id1_alu_a_sel;
    wire  id0_alu_b_sel;
    wire  id1_alu_b_sel;
    wire [1:0] id0_wb_sel;
    wire [1:0] id1_wb_sel;
    wire [1:0] id0_flow_type;
    wire [1:0] id1_flow_type;
    wire [2:0] id0_branch_op;
    wire [2:0] id1_branch_op;
    wire [2:0] id0_lsu_type;
    wire [2:0] id1_lsu_type;
    wire  id0_reg_write;
    wire  id1_reg_write;
    wire  id0_mem_read;
    wire  id1_mem_read;
    wire  id0_mem_write;
    wire  id1_mem_write;
    wire  id0_use_rs1;
    wire  id0_use_rs2;
    wire  id1_use_rs1;
    wire  id1_use_rs2;
    wire [1:0] id0_sys_op;
    wire [1:0] id1_sys_op;
    wire  id0_illegal;
    wire  id1_illegal;
    wire [31:0] id0_imm;
    wire [31:0] id1_imm;

    wire [6:0] id0_issue_class;
    wire [6:0] id1_issue_class;
    wire [6:0] id2_issue_class;
    wire [10:0] decoded_metadata0;
    wire [10:0] decoded_metadata1;
    wire [10:0] decoded_metadata2;
    wire  decoded_mem_read0;
    wire  decoded_mem_write0;
    wire  decoded_mem_read1;
    wire  decoded_mem_write1;
    wire  decoded_mem_read2;
    wire  decoded_mem_write2;
    assign {id0_illegal,id0_reg_write,id0_use_rs2,id0_use_rs1,id0_issue_class}=
        ID_PREDECODE ? fetch_metadata0 : decoded_metadata0;
    assign {id1_illegal,id1_reg_write,id1_use_rs2,id1_use_rs1,id1_issue_class}=
        ID_PREDECODE ? fetch_metadata1 : decoded_metadata1;
    assign {id2_illegal,id2_reg_write,id2_use_rs2,id2_use_rs1,id2_issue_class}=
        (FETCH_PREDECODE || (ROM_SCHEDULE_ACTIVE && THIRD_ALU_SLOT)) ? fetch_metadata2 : decoded_metadata2;
    assign {id0_mem_write,id0_mem_read}=ID_PREDECODE ?
        fetch_metadata0[3:2] : {decoded_mem_write0,decoded_mem_read0};
    assign {id1_mem_write,id1_mem_read}=ID_PREDECODE ?
        fetch_metadata1[3:2] : {decoded_mem_write1,decoded_mem_read1};
    assign {id2_mem_write,id2_mem_read}=ID_PREDECODE ?
        fetch_metadata2[3:2] : {decoded_mem_write2,decoded_mem_read2};
    rv32_decoder u_decoder0 (
        .instr_i(id_word0), .imm_type_o(id0_imm_type), .alu_op_o(id0_alu_op),
        .alu_a_sel_o(id0_alu_a_sel), .alu_b_sel_o(id0_alu_b_sel), .wb_sel_o(id0_wb_sel),
        .flow_type_o(id0_flow_type), .branch_op_o(id0_branch_op), .lsu_type_o(id0_lsu_type),
        .reg_write_o(decoded_metadata0[9]), .mem_read_o(decoded_mem_read0), .mem_write_o(decoded_mem_write0),
        .use_rs1_o(decoded_metadata0[7]), .use_rs2_o(decoded_metadata0[8]), .sys_op_o(id0_sys_op),
        .illegal_o(decoded_metadata0[10]), .issue_class_o(decoded_metadata0[6:0])
    );
    rv32_decoder u_decoder1 (
        .instr_i(id_word1), .imm_type_o(id1_imm_type), .alu_op_o(id1_alu_op),
        .alu_a_sel_o(id1_alu_a_sel), .alu_b_sel_o(id1_alu_b_sel), .wb_sel_o(id1_wb_sel),
        .flow_type_o(id1_flow_type), .branch_op_o(id1_branch_op), .lsu_type_o(id1_lsu_type),
        .reg_write_o(decoded_metadata1[9]), .mem_read_o(decoded_mem_read1), .mem_write_o(decoded_mem_write1),
        .use_rs1_o(decoded_metadata1[7]), .use_rs2_o(decoded_metadata1[8]), .sys_op_o(id1_sys_op),
        .illegal_o(decoded_metadata1[10]), .issue_class_o(decoded_metadata1[6:0])
    );
    rv32_imm_gen u_imm0 (.instr_i(id_word0), .imm_type_i(id0_imm_type), .imm_o(id0_imm));
    rv32_imm_gen u_imm1 (.instr_i(id_word1), .imm_type_i(id1_imm_type), .imm_o(id1_imm));
    rv32_decoder u_decoder2 (
        .instr_i(id2_instr), .imm_type_o(id2_imm_type), .alu_op_o(id2_alu_op),
        .alu_a_sel_o(id2_alu_a_sel), .alu_b_sel_o(id2_alu_b_sel), .wb_sel_o(id2_wb_sel),
        .flow_type_o(id2_flow_type), .branch_op_o(id2_branch_op), .lsu_type_o(id2_lsu_type),
        .reg_write_o(decoded_metadata2[9]), .mem_read_o(decoded_mem_read2), .mem_write_o(decoded_mem_write2),
        .use_rs1_o(decoded_metadata2[7]), .use_rs2_o(decoded_metadata2[8]), .sys_op_o(id2_sys_op),
        .illegal_o(decoded_metadata2[10]), .issue_class_o(decoded_metadata2[6:0])
    );
    rv32_imm_gen u_imm2 (.instr_i(id2_instr), .imm_type_i(id2_imm_type), .imm_o(id2_imm));

    // ---------------- WB：两槽兼容与三槽寄存器堆 ----------------
    reg  wb0_valid;
    reg  wb1_valid;
    reg  wb2_valid;
    reg [31:0] wb0_pc;
    reg [31:0] wb1_pc;
    reg [31:0] wb2_pc;
    reg [31:0] wb0_instr;
    reg [31:0] wb1_instr;
    reg [31:0] wb2_instr;
    reg [4:0] wb0_rd;
    reg [4:0] wb1_rd;
    reg [4:0] wb2_rd;
    reg [31:0] wb0_data;
    reg [31:0] wb1_data;
    reg [31:0] wb2_data;
    reg  wb0_reg_write;
    reg  wb1_reg_write;
    reg  wb2_reg_write;
    wire  wb0_we = wb0_valid && wb0_reg_write && wb0_rd!=5'd0 && !halted_o;
    wire  wb1_we = wb1_valid && wb1_reg_write && wb1_rd!=5'd0 && !halted_o;
    wire  wb2_we = THIRD_ALU_SLOT && wb2_valid && wb2_reg_write && wb2_rd!=5'd0 && !halted_o;

    wire [31:0] id0_rs1_data;
    wire [31:0] id0_rs2_data;
    wire [31:0] id1_rs1_data;
    wire [31:0] id1_rs2_data;
    wire [31:0] id2_rs1_data;
    wire [31:0] id2_rs2_data;
    wire [31:0] dual_rs1_data0;
    wire [31:0] dual_rs2_data0;
    wire [31:0] dual_rs1_data1;
    wire [31:0] dual_rs2_data1;
    // 保留原u_regfile层次用于关闭参数的原测试；开启时常量禁写且读输出不用，
    // 综合可裁剪整实例。有效三写RF只有g_third_rf.u_regfile一个实例。
    rv32_regfile_4r2w u_regfile (
        .clk(clk), .rst_n(rst_n),
        .we0_i(!THIRD_ALU_SLOT && wb0_we), .waddr0_i(wb0_rd), .wdata0_i(wb0_data),
        .we1_i(!THIRD_ALU_SLOT && wb1_we), .waddr1_i(wb1_rd), .wdata1_i(wb1_data),
        .raddr0_1_i(id0_rs1), .rdata0_1_o(dual_rs1_data0),
        .raddr0_2_i(id0_rs2), .rdata0_2_o(dual_rs2_data0),
        .raddr1_1_i(id1_rs1), .rdata1_1_o(dual_rs1_data1),
        .raddr1_2_i(id1_rs2), .rdata1_2_o(dual_rs2_data1)
    );
    generate if (THIRD_ALU_SLOT) begin : g_third_rf
        rv32_regfile_6r3w u_regfile (
            .clk(clk), .rst_n(rst_n),
            .we0_i(wb0_we), .waddr0_i(wb0_rd), .wdata0_i(wb0_data),
            .we1_i(wb1_we), .waddr1_i(wb1_rd), .wdata1_i(wb1_data),
            .we2_i(wb2_we), .waddr2_i(wb2_rd), .wdata2_i(wb2_data),
            .raddr0_1_i(id0_rs1), .rdata0_1_o(id0_rs1_data),
            .raddr0_2_i(id0_rs2), .rdata0_2_o(id0_rs2_data),
            .raddr1_1_i(id1_rs1), .rdata1_1_o(id1_rs1_data),
            .raddr1_2_i(id1_rs2), .rdata1_2_o(id1_rs2_data),
            .raddr2_1_i(id2_rs1), .rdata2_1_o(id2_rs1_data),
            .raddr2_2_i(id2_rs2), .rdata2_2_o(id2_rs2_data)
        );
    end else begin : g_dual_rf
        assign id0_rs1_data = dual_rs1_data0;
        assign id0_rs2_data = dual_rs2_data0;
        assign id1_rs1_data = dual_rs1_data1;
        assign id1_rs2_data = dual_rs2_data1;
        assign id2_rs1_data = 32'b0;
        assign id2_rs2_data = 32'b0;
    end endgenerate

    // ---------------- EX：三槽流水状态与机器态控制 ----------------
    reg  ex0_valid;
    reg  ex1_valid;
    reg  ex2_valid;
    reg [31:0] ex0_pc;
    reg [31:0] ex1_pc;
    reg [31:0] ex2_pc;
    reg [31:0] ex0_instr;
    reg [31:0] ex1_instr;
    reg [31:0] ex2_instr;
    reg [31:0] ex0_rs1_data;
    reg [31:0] ex0_rs2_data;
    reg [31:0] ex0_imm;
    reg [31:0] ex1_rs1_data;
    reg [31:0] ex1_rs2_data;
    reg [31:0] ex1_imm;
    reg [31:0] ex2_rs1_data;
    reg [31:0] ex2_rs2_data;
    reg [31:0] ex2_imm;
    reg [4:0] ex0_rd;
    reg [4:0] ex1_rd;
    reg [4:0] ex2_rd;
    reg [3:0] ex0_alu_op;
    reg [3:0] ex1_alu_op;
    reg [3:0] ex2_alu_op;
    reg [1:0] ex0_alu_a_sel;
    reg [1:0] ex1_alu_a_sel;
    reg [1:0] ex2_alu_a_sel;
    reg  ex0_alu_b_sel;
    reg  ex1_alu_b_sel;
    reg  ex2_alu_b_sel;
    reg  ex0_reg_write;
    reg  ex1_reg_write;
    reg  ex2_reg_write;
    reg  ex0_mem_read;
    reg  ex0_mem_write;
    reg  ex1_mem_read;
    reg  ex1_mem_write;
    reg [2:0] ex0_lsu_type;
    reg [2:0] ex1_lsu_type;
    reg [1:0] ex0_wb_sel;
    reg [1:0] ex0_flow_type;
    reg [1:0] ex1_wb_sel;
    reg [1:0] ex1_flow_type;
    reg [2:0] ex0_branch_op;
    reg [2:0] ex1_branch_op;
    reg  ex0_branch_pred_taken;
    reg  ex1_branch_pred_taken;
    reg  ex0_target_pred_valid;
    // valid预测必为4字节对齐，低两位不必保存；真实目标低位仍独立检查。
    reg [29:0] ex0_target_pred_word;
    wire [31:0] ex0_target_pred = {ex0_target_pred_word,2'b00};
    reg  ex0_ras_push_hint;
    reg  ex0_ras_pop_hint;
    reg  ex0_return_hint;
    reg  ex1_ras_push_hint;
    reg  ex1_ras_pop_hint;
    reg  ex1_fwd_slot0_rs1;
    reg  ex1_fwd_slot0_rs2;
    // 停机采用排空方式：先完成在途老指令，再报告错误指令。
    reg  stop_pending;
    reg [31:0] pending_pc;
    reg [31:0] pending_instr;
    reg [3:0] pending_cause;
    reg [31:0] pending_mcause;
    reg [31:0] pending_tval;
    reg  wb0_special;
    wire  machine_special;
    wire  special_fire;
    wire  machine_trap;
    wire  irq_pending;
    wire  local_irq_queued;
    wire [31:0] admitted_irq_cause;
    wire  irq_wait = irq_pending || local_irq_queued;
    wire [31:0] csr_data;
    wire [31:0] csr_vector;
    wire [31:0] csr_epc;
    wire [31:0] irq_cause;
    wire  csr_illegal;
    wire  pipeline_empty = !ex0_valid && !ex1_valid && !ex2_valid &&
                          !wb0_valid && !wb1_valid && !wb2_valid;
    wire  id0_csr = id_word0[6:0]==7'h73 &&
                   (id_word0[14:12]==1 || id_word0[14:12]==2 ||
                    id_word0[14:12]==3 || id_word0[14:12]>=5);
    wire  id0_mret = id_word0==32'h30200073;
    wire  id0_fence = id_word0[6:0]==7'h0f && id_word0[14:12]==0;
    wire  id0_wfi = id_word0==32'h10500073;
    assign machine_special = MACHINE_MODE && (id0_csr || id0_mret || id0_fence || id0_wfi);
    assign special_fire = machine_special && pipeline_empty && fetch_started && fetch_ready &&
                           !fetch_fault0 && fetch_pc[1:0]==0 && !stop_pending &&
                           !irq_wait && !halted_o && (!id0_csr || !csr_illegal);
    wire [31:0] machine_cause = stop_pending ? pending_mcause : admitted_irq_cause;
    wire [31:0] machine_pc = stop_pending ? pending_pc : fetch_pc;
    wire [31:0] machine_tval = stop_pending ? pending_tval : 32'b0;
    generate if(MACHINE_MODE) begin : g_machine
        wire [2:0] retired = {2'b0,(wb0_valid && !wb0_special)} +
                              {2'b0,wb1_valid} + {2'b0,wb2_valid} + {2'b0,special_fire};
        rv32_machine_csr u_csr (
            .clk(clk),.rst_n(rst_n),.addr_i(id_word0[31:20]),
            .cmd_i(id_word0[13:12]),
            .operand_i(id_word0[14] ? {27'b0,id0_rs1} : id0_rs1_data),
            .source_nonzero_i(id0_rs1!=0),.access_i(special_fire && id0_csr),
            .rdata_o(csr_data),.illegal_o(csr_illegal),.retire_count_i(retired),
            .trap_i(machine_trap),.mret_i(special_fire && id0_mret),
            .trap_pc_i(machine_pc),.trap_cause_i(machine_cause),.trap_tval_i(machine_tval),
            .irq_external_i(irq_external_i),.irq_software_i(irq_software_i),.irq_timer_i(irq_timer_i),
            .irq_pending_o(irq_pending),.irq_cause_o(irq_cause),
            .trap_vector_o(csr_vector),.epc_o(csr_epc)
        );
    end else begin : g_no_machine
        assign csr_data=0;assign csr_illegal=0;assign csr_vector=0;assign csr_epc=0;
        assign irq_pending=0;assign irq_cause=0;
    end endgenerate

    // 三槽执行、共享乘法器及主次访存接口：下列信号连接对应子模块。
    wire  ex0_branch_taken;
    wire [31:0] ex0_control_next_pc;
    wire [31:0] ex0_control_target;
    wire  ex0_fault;
    wire  ex0_is_branch;
    wire  ex0_is_jalr;
    wire  ex0_is_mul;
    wire  ex0_kill_slot1;
    wire  ex0_load_ready_fault;
    wire [31:0] ex0_mem_addr;
    wire  ex0_mul_busy;
    reg  ex0_mul_decoded;
    reg [1:0] ex0_mul_op_decoded;
    wire  ex0_redirect;
    wire [31:0] ex0_result;
    wire  ex0_target_misaligned;
    wire  ex1_branch_taken;
    wire [31:0] ex1_branch_target;
    wire [31:0] ex1_control_next_pc;
    wire  ex1_is_branch;
    wire  ex1_is_jalr;
    wire  ex1_is_mul;
    wire [31:0] ex1_jalr_target;
    wire  ex1_mem_active;
    wire [31:0] ex1_mem_addr;
    reg  ex1_mul_decoded;
    reg [1:0] ex1_mul_op_decoded;
    wire [31:0] ex1_no_shift_result;
    wire  ex1_ram_pair_replay;
    wire  ex1_redirect;
    wire  ex1_replay;
    wire [31:0] ex1_result;
    wire  ex1_target_misaligned;
    wire [31:0] ex2_result;
    wire  ex_double_load;
    wire  ex_load_store;
    wire  fast_mul_low;
    wire [31:0] id0_rs1_capture_data;
    wire [31:0] id0_rs2_capture_data;
    wire [31:0] id1_mul_capture_rs1;
    wire [31:0] id1_mul_capture_rs2;
    wire [31:0] id1_rs1_capture_data;
    wire [31:0] id1_rs2_capture_data;
    wire [31:0] load0_capture_data;
    reg [31:0] local_ex0_alu_result;
    wire [31:0] local_id0_alu_result;
    wire  lsu1_misaligned;
    wire [31:0] lsu_load_data;
    wire  lsu_misaligned;
    wire  memory_error0;
    wire  memory_error1;
    wire [31:0] mul_forward_result;
    wire [31:0] mul_result;
    wire  primary_active;
    wire  primary_slot1;
    wire [31:0] slot1_load_data;

    // 跨组最新生产者选择、操作数前递及等待判断：下列信号连接对应子模块。
    wire [31:0] ex0_forward_data;
    wire  ex0_forwardable;
    wire  ex0_load_ready;
    wire  ex0_writes;
    wire [31:0] ex1_forward_data;
    wire  ex1_forwardable;
    wire  ex1_load_ready;
    wire  ex1_shift_wb;
    wire  ex1_writes;
    wire [31:0] ex2_id_forward_data;
    wire  ex2_writes;
    wire  id0_wait_ex;
    wire  id1_wait_ex;
    wire [31:0] id2_rs1_capture_data;
    wire [31:0] id2_rs2_capture_data;
    wire  id2_wait_ex;

    // 顺序三槽接纳、相关检查与资源配对：下列信号连接对应子模块。
    wire  accept0;
    wire  accept1;
    wire  accept2;
    wire  bad_slot0;
    wire  id0_to_id1_pair_forwardable;
    wire  pair_dependency;
    wire  pair_raw_rs1;
    wire  pair_raw_rs2;
    wire  pair_stall;
    wire  pair_structural_conflict;

    // 时序块使用的控制条件集中命名，避免在同一条件表达式中混入多个流水线原因。
    wire  ex0_lsu_misaligned = ex0_valid && (ex0_mem_read || ex0_mem_write) && lsu_misaligned;
    wire  ex1_lsu_misaligned = ex1_mem_active && !ex_load_store && !ex1_ram_pair_replay &&
                            (ex_double_load ? lsu1_misaligned : lsu_misaligned);
    wire  ex0_bus_fault = MEM_HANDSHAKE && primary_active && !primary_slot1 && memory_error0;
    wire  ex1_bus_fault = MEM_HANDSHAKE && !ex1_ram_pair_replay && ((primary_slot1 && memory_error0) ||
                                         ((ex_double_load || ex_load_store) && memory_error1));
    assign ex0_fault = ex0_lsu_misaligned || (ex0_valid && ex0_target_misaligned) || ex0_bus_fault;
    assign ex0_load_ready_fault = ex0_lsu_misaligned || ex0_bus_fault;
    wire  ex1_fault = ex1_lsu_misaligned || ex1_target_misaligned || ex1_bus_fault;
    wire  ex0_jalr_return = ex0_return_hint;
    assign btb_train_valid = JALR_BTB && ex0_is_jalr && !ex0_fault && !ex0_jalr_return && ex_advance;
    assign btb_train_pc = ex0_pc;
    assign btb_train_target = ex0_control_target;
    // 提示在IF清除早JAL的flow_type之前产生，仍覆盖早调用；年龄/异常门保持EX判断。
    wire  ex0_ras_push = ex0_valid && !ex0_fault && ex0_ras_push_hint;
    wire  ex0_ras_pop = ex0_valid && !ex0_fault && ex0_ras_pop_hint;
    wire  ex1_ras_push = ex1_valid && !ex0_fault && !ex1_fault && !ex0_kill_slot1 && ex1_ras_push_hint;
    wire  ex1_ras_pop = ex1_valid && !ex0_fault && !ex1_fault && !ex0_kill_slot1 && ex1_ras_pop_hint;
    wire  ras_select0 = ex0_ras_push || ex0_ras_pop;
    assign ras_update_valid = RAS_PREDICT && (ras_select0 || ex1_ras_push || ex1_ras_pop) && ex_advance;
    assign ras_pop = ras_select0 ? ex0_ras_pop : ex1_ras_pop;
    assign ras_push = ras_select0 ? ex0_ras_push : ex1_ras_push;
    assign ras_push_pc = ras_select0 ? ex0_pc+32'd4 : ex1_pc+32'd4;
    wire  can_issue_original = !halted_o && !stop_pending && !bad_slot0 && !pair_stall &&
                     ex_advance && fetch_ready && !machine_special && !irq_wait &&
                     !ex0_fault && !ex1_fault && !ex0_redirect &&
                     !ex1_redirect && !ex0_mul_busy;
    // 普通合法指令不能同时是机器态特殊指令；启动状态单独处理。
    // 陷阱与非法 CSR 访问仍由 bad_slot0 拒绝。
    wire  issue_head_permitted = fetch_started ?
        (id0_eligible && !fetch_fault0 && !(MACHINE_MODE && fetch_pc[1:0]!=0)) :
        !machine_special;
    wire  can_issue_ordinary = !halted_o && !stop_pending && issue_head_permitted &&
                     !pair_stall && ex_advance && fetch_ready && !irq_wait &&
                     !ex0_fault && !ex1_fault && !ex0_redirect &&
                     !ex1_redirect && !ex0_mul_busy;
    wire  can_issue = ISSUE_HEAD_COFACTOR ? can_issue_ordinary : can_issue_original;
    wire  jal_redirect_if = accept0 && if_jal_candidate;
    wire  slot1_jal_redirect_if = accept0 && accept1 && if_slot1_jal_candidate;
    wire  branch_redirect_if = accept0 && id0_flow_type==2'd1 && BRANCH_PREDICT!=0;
    wire  slot1_branch_redirect_if = accept0 && accept1 && id1_simple_branch &&
                                    BRANCH_PREDICT!=0;
    // 每槽控制流类型互斥，先独立准备“只接纳slot0”和“接纳两条”的下一PC。
    // 晚到的双Load范围/配对决定只控制最后一级32位mux，不串过控制流优先链。
    wire  if_pc0_branch = if_branch_pred_taken;
    wire  if_pc0_jal = if_jal_candidate;
    wire  if_pc0_jalr = if_jalr_pred_valid;
    wire  if_pc0_sequential = !(if_pc0_branch || if_pc0_jal || if_pc0_jalr);
    wire [31:0] if_pc0_next = ({32{if_pc0_sequential}} & fetch_pc_plus4) |
                            ({32{if_pc0_branch}} & if_branch_target) |
                            ({32{if_pc0_jal}} & if_jal_target) |
                            ({32{if_pc0_jalr}} & if_jalr_pred_target);
    wire  if_pc1_branch = id1_simple_branch && if_slot1_branch_pred_taken;
    wire  if_pc1_jal = if_slot1_jal_candidate;
    wire  if_pc1_ret = id1_ret_candidate;
    wire  if_pc1_sequential = !(if_pc1_branch || if_pc1_jal || if_pc1_ret);
    wire [31:0] if_pc1_next = ({32{if_pc1_sequential}} & fetch_pc_plus8) |
                            ({32{if_pc1_branch}} & if_slot1_branch_target) |
                            ({32{if_pc1_jal}} & if_slot1_jal_target) |
                            ({32{if_pc1_ret}} & if_ras_target);
    wire [31:0] if_issue_next_pc = accept2 ? fetch_pc_plus12 :
                                 accept1 ? if_pc1_next : if_pc0_next;
    wire  ex_redirect_if = ex_advance && !halted_o && !stop_pending && ex0_redirect &&
                          !ex0_target_misaligned;
    wire  ex1_redirect_if = ex_advance && !halted_o && !stop_pending && !ex0_fault &&
                           ex1_redirect && !ex1_target_misaligned;
    // 两个槽位不会同时承载条件分支，合并训练请求以保持 BHT 单写口。
    wire  bht_train_ex0 = ex0_is_branch && !ex0_target_misaligned;
    wire  bht_train_ex1 = ex1_is_branch && !ex0_fault && !ex1_target_misaligned;
    wire  bht_train_valid = BRANCH_PREDICT==2 && (bht_train_ex0 || bht_train_ex1) && ex_advance;
    wire [BHT_INDEX_BITS-1:0] bht_train_index = bht_train_ex0
                                              ? ex0_pc[BHT_INDEX_BITS+1:2]
                                              : ex1_pc[BHT_INDEX_BITS+1:2];
    wire  bht_train_taken = bht_train_ex0 ? ex0_branch_taken : ex1_branch_taken;
    // 只训练已经解析且无目标/年长访存故障的后向分支，误预测拍也要训练。
    assign loop_train_pc = bht_train_ex0 ? ex0_pc : ex1_pc;
    assign loop_train_taken = bht_train_taken;
    assign loop_train_valid = LOOP_PREDICT && bht_train_valid &&
                              (bht_train_ex0 ? ex0_instr[31] : ex1_instr[31]);
    wire  halt_drain_done = stop_pending && !ex0_valid && !ex1_valid && !ex2_valid &&
                           !wb0_valid && !wb1_valid && !wb2_valid;
    // 中断准入只判定一次，随后保持队首并分派已保存的中断。
    // 中断分派到 ROM 的路径不再经过指令及 CSR 合法性判断。
    wire  irq_take;
    generate if(LOCAL_MEMORY_LOOKAHEAD && MACHINE_MODE) begin:g_local_irq_admission
        reg queued;
        reg [1:0] cause_code;
        wire admit = irq_pending && pipeline_empty && fetch_started && fetch_ready &&
                     !stop_pending && !bad_slot0 && !halted_o && !queued;
        always @(posedge clk or negedge rst_n) begin
            if(!rst_n) begin queued<=1'b0;cause_code<=2'b0;end
            else if(machine_trap || stop_pending || halted_o) queued<=1'b0;
            else if(admit) begin queued<=1'b1;cause_code<=irq_cause[3:2];end
        end
        assign local_irq_queued=queued;
        assign admitted_irq_cause={28'h8000000,cause_code,2'b11};
        assign irq_take=queued && pipeline_empty && !stop_pending && !halted_o;
    end else begin:g_direct_irq_admission
        assign local_irq_queued=1'b0;
        assign admitted_irq_cause=irq_cause;
        assign irq_take=MACHINE_MODE && irq_pending && pipeline_empty &&
                        !stop_pending && !bad_slot0 && !halted_o;
    end endgenerate
    assign machine_trap = MACHINE_MODE && (halt_drain_done || irq_take);

    // 完整计算 PC 寄存器的下一值，包含所有较老指令重定向。
    // 本地 ROM 可与 PC 同沿采样该值，不额外插入空拍。
    reg [31:0] fetch_pc_next;
    always @* begin
        fetch_pc_next=fetch_pc;
        if(ex_advance && can_issue && accept0) fetch_pc_next=if_issue_next_pc;
        if(ex_redirect_if) fetch_pc_next=ex0_control_next_pc;
        if(ex1_redirect_if) fetch_pc_next=ex1_control_next_pc;
        if(special_fire) fetch_pc_next=id0_mret ? csr_epc : fetch_pc_plus4;
        if(machine_trap) fetch_pc_next=csr_vector;
    end
    assign legacy_next_pc=rst_n ? fetch_pc_next : RESET_PC;
    assign fetch_pop=(ex_advance && can_issue && accept0) ?
                     (accept2 ? 2'd3 : accept1 ? 2'd2 : 2'd1) : 2'd0;
    assign fetch_flush=ex_redirect_if || ex1_redirect_if || special_fire || machine_trap ||
        (fetch_pop!=0 && !accept2 && (accept1 ? !if_pc1_sequential : !if_pc0_sequential));

    // 先计算各候选 ROM 字地址，再按发射数量选择，避免在最终 PC 后串接加法。
    // 体系结构 PC 与取指异常仍使用完整 32 位地址。
    generate if(LOCAL_MEMORY_LOOKAHEAD) begin : g_local_rom_words
        // 候选地址算术置于晚到 PC 选择之前；保留既有 ADS 属性，不增加寄存器或时序例外。
        wire [13:0] pc0_word1,pc0_word2,pc1_word1,pc1_word2;
        rv32_if_rom_offset_select_jalr u_pc0_offsets(
            .select_onehot({if_pc0_jalr,if_pc0_jal,if_pc0_branch,if_pc0_sequential}),
            .pc0(fetch_pc_plus4),.pc1(if_branch_target),.pc2(if_jal_target),
            .ras(if_ras_target),.btb(if_btb_target),.jalr_return(if_jalr_return),
            .word1(pc0_word1),.word2(pc0_word2));
        rv32_if_rom_offset_select u_pc1_offsets(
            .select_onehot({if_pc1_ret,if_pc1_jal,if_pc1_branch,if_pc1_sequential}),
            .pc0(fetch_pc_plus8),.pc1(if_slot1_branch_target),.pc2(if_slot1_jal_target),.pc3(if_ras_target),
            .word1(pc1_word1),.word2(pc1_word2));
        wire [13:0] pc2_word1 /* synthesis syn_keep=1 */ =fetch_pc[15:2]+14'd4;
        wire [13:0] pc2_word2 /* synthesis syn_keep=1 */ =fetch_pc[15:2]+14'd5;
        wire [13:0] held_word1 /* synthesis syn_keep=1 */ =fetch_pc[15:2]+14'd1;
        wire [13:0] held_word2 /* synthesis syn_keep=1 */ =fetch_pc[15:2]+14'd2;
        wire [13:0] held_word3 /* synthesis syn_keep=1 */ =fetch_pc[15:2]+14'd3;
        wire [13:0] redirect0_word1 /* synthesis syn_keep=1 */ =ex0_control_next_pc[15:2]+14'd1;
        wire [13:0] redirect0_word2 /* synthesis syn_keep=1 */ =ex0_control_next_pc[15:2]+14'd2;
        wire [13:0] redirect1_word1 /* synthesis syn_keep=1 */ =ex1_control_next_pc[15:2]+14'd1;
        wire [13:0] redirect1_word2 /* synthesis syn_keep=1 */ =ex1_control_next_pc[15:2]+14'd2;
        wire [13:0] epc_word1 /* synthesis syn_keep=1 */ =csr_epc[15:2]+14'd1;
        wire [13:0] epc_word2 /* synthesis syn_keep=1 */ =csr_epc[15:2]+14'd2;
        wire [13:0] trap_word1 /* synthesis syn_keep=1 */ =csr_vector[15:2]+14'd1;
        wire [13:0] trap_word2 /* synthesis syn_keep=1 */ =csr_vector[15:2]+14'd2;
        wire [13:0] next_word1,next_word2;
        rv32_rom_word_priority u_word_priority(
            .control({machine_trap,id0_mret,special_fire,ex1_redirect_if,ex_redirect_if,
                      accept2,accept1,(ex_advance && can_issue && accept0)}),
            .held({held_word2,held_word1}),.issue0({pc0_word2,pc0_word1}),
            .issue1({pc1_word2,pc1_word1}),.issue2({pc2_word2,pc2_word1}),
            .redirect0({redirect0_word2,redirect0_word1}),
            .redirect1({redirect1_word2,redirect1_word1}),
            .epc({epc_word2,epc_word1}),.step({held_word3,held_word2}),
            .trap({trap_word2,trap_word1}),.words({next_word2,next_word1}));
        assign legacy_word1=rst_n ? next_word1 : RESET_PC[15:2]+14'd1;
        assign legacy_word2=rst_n ? next_word2 : RESET_PC[15:2]+14'd2;
    end else begin : g_elastic_rom_words
        assign legacy_word1=legacy_next_pc[15:2]+14'd1;
        assign legacy_word2=legacy_next_pc[15:2]+14'd2;
    end endgenerate

    generate if(LOCAL_MEMORY_LOOKAHEAD) begin : g_local_preview
        // 使用 EX 将要捕获的真实操作数，包含最新生产者选择和 Load 前递。
        wire [31:0] alu_a=id0_alu_a_sel==0 ? id0_rs1_capture_data :
                               id0_alu_a_sel==1 ? fetch_pc : 32'b0;
        wire [31:0] alu_b=id0_alu_b_sel ? id0_imm : id0_rs2_capture_data;
        wire [31:0] ordinary0;
        rv32_alu u_address_producer(.a_i(alu_a),.b_i(alu_b),
            .alu_op_i(id0_alu_op),.result_o(ordinary0));
        assign local_id0_alu_result=ordinary0;
        // 与 EX0 指令和操作数同沿保存，不增加指令流水级。
        // 有效性与复位仍由 EX valid 控制，无效数据不参与体系结构行为。
        always @(posedge clk)
            if(ex_advance && can_issue && accept0) local_ex0_alu_result<=ordinary0;
        // 本地地址相关串行化后，获准发射的槽 1 访存地址不依赖普通 ALU0 结果。
        wire [31:0] base1=(!SERIALIZE_LOCAL_ADDRESS_RAW && id0_to_id1_pair_forwardable && pair_raw_rs1)
                             ? ordinary0 : id1_rs1_capture_data;
        wire [31:0] address0=id0_rs1_capture_data+id0_imm;
        wire [31:0] address1=base1+id1_imm;
        assign local_id_address0=address0;
        assign local_id_address1=address1;
        wire incoming1=accept1 && (id1_mem_read || id1_mem_write);
        wire incoming0=id0_mem_read || id0_mem_write;
        wire primary1=incoming1 && !id0_mem_read;
        assign dmem_preview_addr0_o=primary1 ? address1 : address0;
        assign dmem_preview_addr1_o=address1;
        // 第二读通道仅服务两条独立 Load；同组 Load RAW 不直接前递。
        // 跨组最新值前递及通用预读地址端口保持原有语义，无效载荷不被消费。
        assign dmem_preview_load1_o=id1_rs1_capture_data+id1_imm;
        // 此信号只控制物理预读，体系结构请求接纳及响应归属仍由 preview_valid 控制。
        // EX 保持期间必须维持已经接纳的请求地址。
        assign dmem_ram_read_ahead1_o=rst_n && fetch_started && fetch_ready &&
                                     fetch_head1_valid && id1_mem_read && ex_advance;
        assign dmem_preview_valid_o={
            ex_advance && can_issue && accept0 && accept1 && id0_mem_read && id1_mem_read,
            ex_advance && can_issue && accept0 && (incoming0 || incoming1)};
    end else begin : g_no_local_preview
        assign local_id_address0=0;
        assign local_id_address1=0;
        assign local_id0_alu_result=0;
        assign dmem_preview_load1_o=0;
        assign dmem_ram_read_ahead1_o=0;
        assign dmem_preview_addr0_o=0;assign dmem_preview_addr1_o=0;
        assign dmem_preview_valid_o=0;
    end endgenerate

    generate if(CAPTURE_ADDRESS)begin:g_effective_address_capture
        reg [31:0] address0_q,address1_q;
        assign local_ex_address0=address0_q;
        assign local_ex_address1=address1_q;
        // 时钟使能与 EX0/EX1 指令组所有权一致；复位仅清除原有效位，无效载荷不解释。
        always @(posedge clk)begin
            if(ex_advance && can_issue && accept0) address0_q<=local_id_address0;
            if(ex_advance) address1_q<=local_id_address1;
        end
    end else begin:g_original_effective_address
        assign local_ex_address0=0;
        assign local_ex_address1=0;
    end endgenerate

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            fetch_pc <= RESET_PC;
            fetch_started <= 1'b0;
            ex0_valid <= 1'b0; ex1_valid <= 1'b0; ex2_valid <= 1'b0;
            ex0_target_pred_valid <= 1'b0;
            ex1_fwd_slot0_rs1 <= 1'b0; ex1_fwd_slot0_rs2 <= 1'b0;
            wb0_valid <= 1'b0; wb1_valid <= 1'b0; wb2_valid <= 1'b0;
            halted_o <= 1'b0; halt_pc_o <= 32'b0; halt_instr_o <= 32'b0;
            halt_cause_o <= 4'b0;
            stop_pending <= 1'b0; pending_pc <= 32'b0; pending_instr <= 32'b0;
            pending_cause <= 4'b0;
            pending_mcause<=0;pending_tval<=0;wb0_special<=0;
            trap_valid_o<=0;trap_pc_o<=0;trap_cause_o<=0;trap_tval_o<=0;
        end else begin
            fetch_started <= 1'b1;
            trap_valid_o<=0;
            wb0_special<=0;

            // ---------------- WB：提交上一拍 EX 结果 ----------------
            wb0_valid <= ex_advance && ex0_valid && !ex0_fault;
            wb0_pc <= ex0_pc; wb0_instr <= ex0_instr; wb0_rd <= ex0_rd;
            wb0_data <= ex0_mem_read ? lsu_load_data :
                        ex0_wb_sel==2'd2 ? ex0_pc + 32'd4 :
                        ex0_is_mul ? mul_result : ex0_result;
            wb0_reg_write <= ex0_reg_write;
            // slot0 故障或实际跳转时，取消同组更年轻的 slot1。
            wb1_valid <= ex_advance && ex1_valid && !ex1_fault && !ex0_fault && !ex0_kill_slot1 &&
                         !ex1_replay;
            wb1_pc <= ex1_pc; wb1_instr <= ex1_instr; wb1_rd <= ex1_rd;
            wb1_data <= ex1_mem_read ? slot1_load_data :
                        ex1_wb_sel==2'd2 ? ex1_pc + 32'd4 : ex1_is_mul ? mul_result : ex1_result;
            wb1_reg_write <= ex1_reg_write;
            // 第三槽只执行无异常普通ALU，较老故障/取消/重放仍精确取消它。
            // stop_pending不取消此前已接纳的健康老指令，按原停机规则排空。
            wb2_valid <= ex_advance && THIRD_ALU_SLOT && ex2_valid && !ex0_fault && !ex1_fault &&
                         !ex0_kill_slot1 && !ex0_redirect && !ex1_redirect &&
                         !ex1_replay;
            wb2_pc <= ex2_pc; wb2_instr <= ex2_instr; wb2_rd <= ex2_rd;
            wb2_data <= ex2_result; wb2_reg_write <= ex2_reg_write;

            // EX1无效槽的数据没有架构作用；每拍提前捕获payload，只对valid/同组
            // 前递标志保留接纳门。降低晚到accept1的CE扇出，有效拍数据完全相同。
            if(ex_advance) begin
            ex1_pc <= fetch_pc_plus4; ex1_instr <= id_word1;
            ex1_rs1_data <= id1_mul_capture_rs1; ex1_rs2_data <= id1_mul_capture_rs2;
            ex1_imm <= id1_ret_candidate ? if_ras_target : id1_imm;
            ex1_rd <= id1_rd;
            ex1_alu_op <= id1_alu_op; ex1_alu_a_sel <= id1_alu_a_sel;
            ex1_mul_decoded <= id1_alu_op>=4'd10 && id1_alu_op<=4'd13;
            ex1_mul_op_decoded <= id1_alu_op - 4'd10;
            ex1_alu_b_sel <= id1_alu_b_sel; ex1_reg_write <= id1_reg_write;
            ex1_wb_sel <= id1_wb_sel;
            // slot1早JAL仅保留link写回，Branch/ret仍在EX1核对。
            ex1_flow_type <= if_slot1_jal_candidate ? 2'd0 : id1_flow_type;
            ex1_branch_op <= id1_branch_op;
            ex1_branch_pred_taken <= if_slot1_branch_pred_taken;
            ex1_ras_push_hint <= id1_ras_push_hint;
            ex1_ras_pop_hint <= SLOT1_RET_PREDICT && id1_return;
            ex1_mem_read <= id1_mem_read;
            ex1_mem_write <= id1_mem_write;
            ex1_lsu_type <= id1_lsu_type;

            // 第三槽payload同样提前捕获，accept2只驱动valid，取消由上方WB保护。
            ex2_pc <= fetch_pc_plus8; ex2_instr <= id2_instr;
            ex2_rs1_data <= id2_rs1_capture_data; ex2_rs2_data <= id2_rs2_capture_data;
            ex2_imm <= id2_imm; ex2_rd <= id2_rd;
            ex2_alu_op <= id2_alu_op; ex2_alu_a_sel <= id2_alu_a_sel;
            ex2_alu_b_sel <= id2_alu_b_sel; ex2_reg_write <= id2_reg_write;

            // ---------------- EX：默认注入气泡，成功接纳时装载新指令 ----------------
            ex0_valid <= 1'b0;
            ex1_valid <= 1'b0;
            ex2_valid <= 1'b0;
            ex1_fwd_slot0_rs1 <= 1'b0;
            ex1_fwd_slot0_rs2 <= 1'b0;
            // 多周期乘法尚未完成时保持slot0 EX；slot1在发射时已被禁止。
            if (ex0_mul_busy)
                ex0_valid <= ex0_valid;
            if (can_issue) begin
                if (accept0) begin
                    ex0_valid <= 1'b1;
                    ex0_pc <= fetch_pc; ex0_instr <= id_word0;
                    ex0_rs1_data <= id0_rs1_capture_data; ex0_rs2_data <= id0_rs2_capture_data;
                    ex0_imm <= id0_imm; ex0_rd <= id0_rd;
                    ex0_alu_op <= id0_alu_op; ex0_alu_a_sel <= id0_alu_a_sel;
                    ex0_mul_decoded <= id0_alu_op>=4'd10 && id0_alu_op<=4'd13;
                    ex0_mul_op_decoded <= id0_alu_op - 4'd10;
                    ex0_alu_b_sel <= id0_alu_b_sel; ex0_reg_write <= id0_reg_write;
                    ex0_mem_read <= id0_mem_read; ex0_mem_write <= id0_mem_write;
                    ex0_lsu_type <= id0_lsu_type;
                    ex0_wb_sel <= id0_wb_sel;
                    // JAL 仍需流入 WB 写回 PC+4，仅取消它在 EX 的二次跳转。
                    ex0_flow_type <= if_jal_candidate ? 2'd0 : id0_flow_type;
                    ex0_branch_op <= id0_branch_op;
                    ex0_branch_pred_taken <= if_branch_pred_taken;
                    ex0_target_pred_valid <= if_jalr_pred_valid;
                    ex0_target_pred_word <= if_jalr_pred_target[31:2];
                    ex0_ras_push_hint <= id0_ras_push_hint;
                    ex0_ras_pop_hint <= id0_ras_pop_hint;
                    ex0_return_hint <= if_jalr_return;
                end
                if (accept1) begin
                    ex1_valid <= 1'b1;
                    ex1_fwd_slot0_rs1 <= id0_to_id1_pair_forwardable && pair_raw_rs1;
                    ex1_fwd_slot0_rs2 <= id0_to_id1_pair_forwardable && pair_raw_rs2;
                end
                if (accept2)
                    ex2_valid <= 1'b1;
                // slot0预测跳转时不会接纳slot1；预测不跳Branch+ALU按两条前进。
                // 年长EX重定向仍由下方赋值覆盖。
                if (accept0)
                    fetch_pc <= if_issue_next_pc;
            end
            end // 访存等待期间保持 EX 载荷与有效位，不重复捕获指令。

            // 年长 EX 重定向优先级最高，覆盖本拍较年轻的 IF 选择。
            if (ex_redirect_if)
                fetch_pc <= ex0_control_next_pc;
            // slot1控制流比新IF年长；预测错误时恢复分支/返回的真实下一PC。
            if (ex1_redirect_if)
                fetch_pc <= ex1_control_next_pc;


            // ---------------- 异常与停机：先记录，再等待流水线排空 ----------------
            // 年长重定向/故障优先于错误路径上的年轻非法指令或 EBREAK。
            if (ex_advance && !halted_o && !stop_pending && bad_slot0 && !pair_stall &&
                !ex0_redirect && !ex1_redirect && !ex0_fault && !ex1_fault) begin
                stop_pending <= 1'b1;
                pending_pc <= fetch_pc;
                pending_instr <= id_word0;
                pending_cause <= 4'd1;
                pending_mcause <= fetch_pc[1:0]!=0 ? 0 : fetch_fault0 ? 1 :
                    id_word0==32'h00000073 ? 11 : id_word0==32'h00100073 ? 3 : 2;
                pending_tval <= (fetch_pc[1:0]!=0 || fetch_fault0 || id_word0==32'h00100073) ? fetch_pc :
                                 id_word0==32'h00000073 ? 0 : id_word0;
            end
            if (ex_advance && !halted_o && !stop_pending && (ex0_lsu_misaligned || ex0_bus_fault)) begin
                stop_pending <= 1'b1;
                pending_pc <= ex0_pc;
                pending_instr <= ex0_instr;
                pending_cause <= 4'd3;
                pending_mcause<=ex0_mem_write ? (ex0_bus_fault ? 7:6) : (ex0_bus_fault ? 5:4);
                pending_tval<=ex0_mem_addr;
            end
            if (ex_advance && !halted_o && !stop_pending && !ex0_fault && ex1_fault) begin
                stop_pending <= 1'b1;
                pending_pc <= ex1_pc;
                pending_instr <= ex1_instr;
                pending_cause <= ex1_target_misaligned ? 4'd2 : 4'd3;
                pending_mcause<=ex1_target_misaligned ? 0 :
                    ex1_mem_write ? (ex1_bus_fault ? 7:6) : (ex1_bus_fault ? 5:4);
                pending_tval<=ex1_target_misaligned ? (ex1_is_jalr ? ex1_jalr_target : ex1_branch_target) : ex1_mem_addr;
            end
            if (ex_advance && !halted_o && !stop_pending && ex0_valid && ex0_target_misaligned) begin
                stop_pending <= 1'b1;
                pending_pc <= ex0_pc;
                pending_instr <= ex0_instr;
                pending_cause <= 4'd2;
                pending_mcause<=0;pending_tval<=ex0_control_target;
            end
            if (halt_drain_done && !MACHINE_MODE) begin
                halted_o <= 1'b1;
                halt_pc_o <= pending_pc;
                halt_instr_o <= pending_instr;
                halt_cause_o <= pending_cause;
                stop_pending <= 1'b0;
            end
            if(special_fire) begin
                wb0_valid<=1;wb0_special<=1;wb0_pc<=fetch_pc;wb0_instr<=id_word0;
                wb0_rd<=id0_rd;wb0_data<=csr_data;wb0_reg_write<=id0_csr;
                fetch_pc<=id0_mret ? csr_epc : fetch_pc_plus4;
            end
            if(machine_trap) begin
                stop_pending<=0;fetch_pc<=csr_vector;
                trap_valid_o<=1;trap_pc_o<=machine_pc;trap_cause_o<=machine_cause;trap_tval_o<=machine_tval;
            end
        end
    end

    assign commit0_valid_o = wb0_valid && !halted_o;
    assign commit0_pc_o = commit0_valid_o ? wb0_pc : 32'b0;
    assign commit0_instr_o = commit0_valid_o ? wb0_instr : 32'b0;
    assign commit0_rd_write_o = wb0_we;
    assign commit0_rd_addr_o = wb0_we ? wb0_rd : 5'b0;
    assign commit0_rd_data_o = wb0_we ? wb0_data : 32'b0;

    assign commit1_valid_o = wb1_valid && !halted_o;
    assign commit1_pc_o = commit1_valid_o ? wb1_pc : 32'b0;
    assign commit1_instr_o = commit1_valid_o ? wb1_instr : 32'b0;
    assign commit1_rd_write_o = wb1_we;
    assign commit1_rd_addr_o = wb1_we ? wb1_rd : 5'b0;
    assign commit1_rd_data_o = wb1_we ? wb1_data : 32'b0;

    assign commit2_valid_o = THIRD_ALU_SLOT && wb2_valid && !halted_o;
    assign commit2_pc_o = commit2_valid_o ? wb2_pc : 32'b0;
    assign commit2_instr_o = commit2_valid_o ? wb2_instr : 32'b0;
    assign commit2_rd_write_o = wb2_we;
    assign commit2_rd_addr_o = wb2_we ? wb2_rd : 5'b0;
    assign commit2_rd_data_o = wb2_we ? wb2_data : 32'b0;

    // 保留诊断网络，便于波形中观察为什么第二槽位未发射。
    wire  _unused_ok = &{1'b0, id0_branch_op, id1_branch_op, id0_lsu_type,
                        id1_lsu_type, id0_wb_sel, pair_dependency,
                        pair_structural_conflict};

    // 功能子模块统一放在显式信号声明之后，避免隐式网络。
    rv32_tri_predict #(
        .BHT_INDEX_BITS(BHT_INDEX_BITS), .BRANCH_PREDICT(BRANCH_PREDICT), .IF_EARLY_JAL(IF_EARLY_JAL), .JALR_BTB(JALR_BTB), .LOCAL_MEMORY_LOOKAHEAD(LOCAL_MEMORY_LOOKAHEAD), .LOOP_COUNTER_BITS(LOOP_COUNTER_BITS), .LOOP_PREDICT(LOOP_PREDICT), .PARALLEL_BHT_READ(PARALLEL_BHT_READ), .RAS_PREDICT(RAS_PREDICT), .SLOT1_DYNAMIC_BHT(SLOT1_DYNAMIC_BHT), .SLOT1_MUL_PAIR(SLOT1_MUL_PAIR), .SLOT1_RET_PREDICT(SLOT1_RET_PREDICT)
    ) u_predict (
        .bht_train_index(bht_train_index), .bht_train_taken(bht_train_taken),
        .bht_train_valid(bht_train_valid), .btb_train_pc(btb_train_pc),
        .btb_train_target(btb_train_target), .btb_train_valid(btb_train_valid),
        .clk(clk), .fetch_pc(fetch_pc),
        .fetch_pc_plus4(fetch_pc_plus4), .fetch_started(fetch_started),
        .id0_flow_type(id0_flow_type), .id0_issue_class(id0_issue_class),
        .id0_rd(id0_rd), .id0_reg_write(id0_reg_write),
        .id0_rs1(id0_rs1), .id1_flow_type(id1_flow_type),
        .id1_illegal(id1_illegal), .id1_issue_class(id1_issue_class),
        .id1_rd(id1_rd), .id1_rs1(id1_rs1),
        .id2_issue_class(id2_issue_class), .id_word0(id_word0),
        .id_word1(id_word1), .loop_train_pc(loop_train_pc),
        .loop_train_taken(loop_train_taken), .loop_train_valid(loop_train_valid),
        .ras_pop(ras_pop), .ras_push(ras_push),
        .ras_push_pc(ras_push_pc), .ras_update_valid(ras_update_valid),
        .rst_n(rst_n), .id0_eligible(id0_eligible),
        .id0_is_control(id0_is_control), .id0_is_mul(id0_is_mul),
        .id0_ras_pop_hint(id0_ras_pop_hint), .id0_ras_push_hint(id0_ras_push_hint),
        .id1_eligible(id1_eligible), .id1_ras_push_hint(id1_ras_push_hint),
        .id1_ret_candidate(id1_ret_candidate), .id1_return(id1_return),
        .id1_simple_alu(id1_simple_alu), .id1_simple_branch(id1_simple_branch),
        .id1_simple_load(id1_simple_load), .id1_simple_mul(id1_simple_mul),
        .id1_simple_store(id1_simple_store), .id2_simple_alu(id2_simple_alu),
        .if_branch_pred_taken(if_branch_pred_taken), .if_branch_target(if_branch_target),
        .if_btb_target(if_btb_target), .if_jal_candidate(if_jal_candidate),
        .if_jal_target(if_jal_target), .if_jalr_pred_target(if_jalr_pred_target),
        .if_jalr_pred_valid(if_jalr_pred_valid), .if_jalr_return(if_jalr_return),
        .if_ras_target(if_ras_target), .if_slot1_branch_pred_taken(if_slot1_branch_pred_taken),
        .if_slot1_branch_target(if_slot1_branch_target), .if_slot1_jal_candidate(if_slot1_jal_candidate),
        .if_slot1_jal_target(if_slot1_jal_target)
    );

    rv32_tri_execute #(
        .BRANCH_ALU_PAIR(BRANCH_ALU_PAIR), .BRANCH_PREDICT(BRANCH_PREDICT), .DOUBLE_LOAD(DOUBLE_LOAD), .FAST_MUL_LOW(FAST_MUL_LOW), .LOAD_BRANCH_PAIR(LOAD_BRANCH_PAIR), .LOAD_STORE_PAIR(LOAD_STORE_PAIR), .LOCAL_BRANCH_SOURCE_COFACTOR(LOCAL_BRANCH_SOURCE_COFACTOR), .LOCAL_LOAD0_PAYLOAD_VIEW(LOCAL_LOAD0_PAYLOAD_VIEW), .LOCAL_MEMORY_LOOKAHEAD(LOCAL_MEMORY_LOOKAHEAD), .LOCAL_MEMORY_RESPONSE_CLASS(LOCAL_MEMORY_RESPONSE_CLASS), .MEM_HANDSHAKE(MEM_HANDSHAKE), .MUL_ALU_PAIR(MUL_ALU_PAIR), .REGISTER_LOAD_BRANCH_RESULT(REGISTER_LOAD_BRANCH_RESULT), .REGISTER_MUL_RESULT(REGISTER_MUL_RESULT), .SERIALIZE_LOCAL_ADDRESS_RAW(SERIALIZE_LOCAL_ADDRESS_RAW), .SLOT1_MUL_PAIR(SLOT1_MUL_PAIR), .SLOT1_RET_PREDICT(SLOT1_RET_PREDICT), .SPECULATIVE_RAM_PAIR(SPECULATIVE_RAM_PAIR), .THIRD_ALU_BYPASS(THIRD_ALU_BYPASS), .THIRD_ALU_SLOT(THIRD_ALU_SLOT), .CAPTURE_ADDRESS(CAPTURE_ADDRESS)
    ) u_execute (
        .clk(clk), .dmem1_rdata_i(dmem1_rdata_i),
        .dmem1_req_ready_i(dmem1_req_ready_i), .dmem1_rsp_error_i(dmem1_rsp_error_i),
        .dmem1_rsp_valid_i(dmem1_rsp_valid_i), .dmem_local_payload_i(dmem_local_payload_i),
        .dmem_local_ram_i(dmem_local_ram_i), .dmem_rdata_i(dmem_rdata_i),
        .dmem_req_ready_i(dmem_req_ready_i), .dmem_rsp_error_i(dmem_rsp_error_i),
        .dmem_rsp_valid_i(dmem_rsp_valid_i), .ex0_alu_a_sel(ex0_alu_a_sel),
        .ex0_alu_b_sel(ex0_alu_b_sel), .ex0_alu_op(ex0_alu_op),
        .ex0_branch_op(ex0_branch_op), .ex0_branch_pred_taken(ex0_branch_pred_taken),
        .ex0_fault(ex0_fault), .ex0_flow_type(ex0_flow_type),
        .ex0_imm(ex0_imm), .ex0_load_ready_fault(ex0_load_ready_fault),
        .ex0_lsu_type(ex0_lsu_type), .ex0_mem_read(ex0_mem_read),
        .ex0_mem_write(ex0_mem_write), .ex0_mul_decoded(ex0_mul_decoded),
        .ex0_mul_op_decoded(ex0_mul_op_decoded), .ex0_pc(ex0_pc),
        .ex0_rd(ex0_rd), .ex0_reg_write(ex0_reg_write),
        .ex0_rs1_data(ex0_rs1_data), .ex0_rs2_data(ex0_rs2_data),
        .ex0_target_pred(ex0_target_pred), .ex0_target_pred_valid(ex0_target_pred_valid),
        .ex0_valid(ex0_valid), .ex1_alu_a_sel(ex1_alu_a_sel),
        .ex1_alu_b_sel(ex1_alu_b_sel), .ex1_alu_op(ex1_alu_op),
        .ex1_branch_op(ex1_branch_op), .ex1_branch_pred_taken(ex1_branch_pred_taken),
        .ex1_flow_type(ex1_flow_type), .ex1_fwd_slot0_rs1(ex1_fwd_slot0_rs1),
        .ex1_fwd_slot0_rs2(ex1_fwd_slot0_rs2), .ex1_imm(ex1_imm),
        .ex1_lsu_type(ex1_lsu_type), .ex1_mem_read(ex1_mem_read),
        .ex1_mem_write(ex1_mem_write), .ex1_mul_decoded(ex1_mul_decoded),
        .ex1_mul_op_decoded(ex1_mul_op_decoded), .ex1_pc(ex1_pc),
        .ex1_rd(ex1_rd), .ex1_reg_write(ex1_reg_write),
        .ex1_rs1_data(ex1_rs1_data), .ex1_rs2_data(ex1_rs2_data),
        .ex1_valid(ex1_valid), .ex2_alu_a_sel(ex2_alu_a_sel),
        .ex2_alu_b_sel(ex2_alu_b_sel), .ex2_alu_op(ex2_alu_op),
        .ex2_imm(ex2_imm), .ex2_instr(ex2_instr),
        .ex2_pc(ex2_pc), .ex2_rs1_data(ex2_rs1_data),
        .ex2_rs2_data(ex2_rs2_data), .ex2_valid(ex2_valid),
        .halted_o(halted_o), .id0_alu_op(id0_alu_op),
        .id0_is_mul(id0_is_mul), .id0_rs1_capture_data(id0_rs1_capture_data),
        .id0_rs2_capture_data(id0_rs2_capture_data), .id1_alu_op(id1_alu_op),
        .id1_mul_capture_rs1(id1_mul_capture_rs1), .id1_mul_capture_rs2(id1_mul_capture_rs2),
        .id1_rs1_capture_data(id1_rs1_capture_data), .id1_rs2_capture_data(id1_rs2_capture_data),
        .id1_simple_mul(id1_simple_mul), .local_ex0_alu_result(local_ex0_alu_result),
        .local_ex_address0(local_ex_address0), .local_ex_address1(local_ex_address1),
        .local_id0_alu_result(local_id0_alu_result), .rst_n(rst_n),
        .stop_pending(stop_pending), .dmem1_addr_o(dmem1_addr_o),
        .dmem1_read_o(dmem1_read_o), .dmem1_rsp_ready_o(dmem1_rsp_ready_o),
        .dmem1_wdata_o(dmem1_wdata_o), .dmem1_write_o(dmem1_write_o),
        .dmem1_wstrb_o(dmem1_wstrb_o), .dmem_addr_o(dmem_addr_o),
        .dmem_read_o(dmem_read_o), .dmem_rsp_ready_o(dmem_rsp_ready_o),
        .dmem_wdata_o(dmem_wdata_o), .dmem_write_o(dmem_write_o),
        .dmem_wstrb_o(dmem_wstrb_o), .ex0_branch_taken(ex0_branch_taken),
        .ex0_control_next_pc(ex0_control_next_pc), .ex0_control_target(ex0_control_target),
        .ex0_is_branch(ex0_is_branch), .ex0_is_jalr(ex0_is_jalr),
        .ex0_is_mul(ex0_is_mul), .ex0_kill_slot1(ex0_kill_slot1),
        .ex0_mem_addr(ex0_mem_addr), .ex0_mul_busy(ex0_mul_busy),
        .ex0_redirect(ex0_redirect), .ex0_result(ex0_result),
        .ex0_target_misaligned(ex0_target_misaligned), .ex1_branch_taken(ex1_branch_taken),
        .ex1_branch_target(ex1_branch_target), .ex1_control_next_pc(ex1_control_next_pc),
        .ex1_is_branch(ex1_is_branch), .ex1_is_jalr(ex1_is_jalr),
        .ex1_is_mul(ex1_is_mul), .ex1_jalr_target(ex1_jalr_target),
        .ex1_mem_active(ex1_mem_active), .ex1_mem_addr(ex1_mem_addr),
        .ex1_no_shift_result(ex1_no_shift_result), .ex1_ram_pair_replay(ex1_ram_pair_replay),
        .ex1_redirect(ex1_redirect), .ex1_replay(ex1_replay),
        .ex1_result(ex1_result), .ex1_target_misaligned(ex1_target_misaligned),
        .ex2_result(ex2_result), .ex_advance(ex_advance),
        .ex_double_load(ex_double_load), .ex_load_store(ex_load_store),
        .fast_mul_low(fast_mul_low), .load0_capture_data(load0_capture_data),
        .lsu1_misaligned(lsu1_misaligned), .lsu_load_data(lsu_load_data),
        .lsu_misaligned(lsu_misaligned), .memory_error0(memory_error0),
        .memory_error1(memory_error1), .mul_forward_result(mul_forward_result),
        .mul_result(mul_result), .primary_active(primary_active),
        .primary_slot1(primary_slot1), .slot1_load_data(slot1_load_data)
    );

    rv32_tri_operands #(
        .EX2_FORWARD_FROM_WB_ONLY(EX2_FORWARD_FROM_WB_ONLY), .LOAD_ADDR_BYPASS(LOAD_ADDR_BYPASS), .LOAD_MUL_BYPASS(LOAD_MUL_BYPASS), .LOAD_USE_BYPASS(LOAD_USE_BYPASS), .LOCAL_LOAD0_READY_COFACTOR(LOCAL_LOAD0_READY_COFACTOR), .LOCAL_LOAD1_FROM_WB_ONLY(LOCAL_LOAD1_FROM_WB_ONLY), .LOCAL_MEMORY_LOOKAHEAD(LOCAL_MEMORY_LOOKAHEAD), .LOCAL_MEMORY_RESPONSE_CLASS(LOCAL_MEMORY_RESPONSE_CLASS), .LOCAL_SHIFT_FROM_WB_ONLY(LOCAL_SHIFT_FROM_WB_ONLY), .LOCAL_SMALL_SHIFT_FORWARD(LOCAL_SMALL_SHIFT_FORWARD), .STORE_DATA_BYPASS(STORE_DATA_BYPASS), .THIRD_ALU_SLOT(THIRD_ALU_SLOT)
    ) u_operands (
        .dmem_local_ram_i(dmem_local_ram_i), .ex0_fault(ex0_fault),
        .ex0_is_mul(ex0_is_mul), .ex0_load_ready_fault(ex0_load_ready_fault),
        .ex0_mem_addr(ex0_mem_addr), .ex0_mem_read(ex0_mem_read),
        .ex0_pc(ex0_pc), .ex0_rd(ex0_rd),
        .ex0_reg_write(ex0_reg_write), .ex0_result(ex0_result),
        .ex0_valid(ex0_valid), .ex0_wb_sel(ex0_wb_sel),
        .ex1_alu_a_sel(ex1_alu_a_sel), .ex1_alu_b_sel(ex1_alu_b_sel),
        .ex1_alu_op(ex1_alu_op), .ex1_fwd_slot0_rs1(ex1_fwd_slot0_rs1),
        .ex1_imm(ex1_imm), .ex1_is_mul(ex1_is_mul),
        .ex1_mem_addr(ex1_mem_addr), .ex1_mem_read(ex1_mem_read),
        .ex1_no_shift_result(ex1_no_shift_result), .ex1_pc(ex1_pc),
        .ex1_ram_pair_replay(ex1_ram_pair_replay), .ex1_rd(ex1_rd),
        .ex1_reg_write(ex1_reg_write), .ex1_result(ex1_result),
        .ex1_rs1_data(ex1_rs1_data), .ex1_valid(ex1_valid),
        .ex1_wb_sel(ex1_wb_sel), .ex2_rd(ex2_rd),
        .ex2_reg_write(ex2_reg_write), .ex2_result(ex2_result),
        .ex2_valid(ex2_valid), .ex_double_load(ex_double_load),
        .fast_mul_low(fast_mul_low), .id0_eligible(id0_eligible),
        .id0_is_mul(id0_is_mul), .id0_mem_read(id0_mem_read),
        .id0_mem_write(id0_mem_write), .id0_rs1(id0_rs1),
        .id0_rs1_data(id0_rs1_data), .id0_rs2(id0_rs2),
        .id0_rs2_data(id0_rs2_data), .id0_use_rs1(id0_use_rs1),
        .id0_use_rs2(id0_use_rs2), .id1_eligible(id1_eligible),
        .id1_mem_read(id1_mem_read), .id1_mem_write(id1_mem_write),
        .id1_rs1(id1_rs1), .id1_rs1_data(id1_rs1_data),
        .id1_rs2(id1_rs2), .id1_rs2_data(id1_rs2_data),
        .id1_simple_mul(id1_simple_mul), .id1_simple_store(id1_simple_store),
        .id1_use_rs1(id1_use_rs1), .id1_use_rs2(id1_use_rs2),
        .id2_rs1(id2_rs1), .id2_rs1_data(id2_rs1_data),
        .id2_rs2(id2_rs2), .id2_rs2_data(id2_rs2_data),
        .id2_use_rs1(id2_use_rs1), .id2_use_rs2(id2_use_rs2),
        .load0_capture_data(load0_capture_data), .lsu1_misaligned(lsu1_misaligned),
        .lsu_load_data(lsu_load_data), .lsu_misaligned(lsu_misaligned),
        .mul_forward_result(mul_forward_result), .slot1_load_data(slot1_load_data),
        .ex0_forward_data(ex0_forward_data), .ex0_forwardable(ex0_forwardable),
        .ex0_load_ready(ex0_load_ready), .ex0_writes(ex0_writes),
        .ex1_forward_data(ex1_forward_data), .ex1_forwardable(ex1_forwardable),
        .ex1_load_ready(ex1_load_ready), .ex1_shift_wb(ex1_shift_wb),
        .ex1_writes(ex1_writes), .ex2_id_forward_data(ex2_id_forward_data),
        .ex2_writes(ex2_writes), .id0_rs1_capture_data(id0_rs1_capture_data),
        .id0_rs2_capture_data(id0_rs2_capture_data), .id0_wait_ex(id0_wait_ex),
        .id1_rs1_capture_data(id1_rs1_capture_data), .id1_rs2_capture_data(id1_rs2_capture_data),
        .id1_wait_ex(id1_wait_ex), .id2_rs1_capture_data(id2_rs1_capture_data),
        .id2_rs2_capture_data(id2_rs2_capture_data), .id2_wait_ex(id2_wait_ex)
    );

    rv32_tri_issue #(
        .ALLOW_ALU_WAW(ALLOW_ALU_WAW), .BRANCH_ALU_PAIR(BRANCH_ALU_PAIR), .BRANCH_PREDICT(BRANCH_PREDICT), .DOUBLE_LOAD(DOUBLE_LOAD), .EX2_FORWARD_FROM_WB_ONLY(EX2_FORWARD_FROM_WB_ONLY), .LOAD_ADDR_BYPASS(LOAD_ADDR_BYPASS), .LOAD_BRANCH_PAIR(LOAD_BRANCH_PAIR), .LOAD_STORE_PAIR(LOAD_STORE_PAIR), .LOAD_USE_BYPASS(LOAD_USE_BYPASS), .LOCAL_MEMORY_LOOKAHEAD(LOCAL_MEMORY_LOOKAHEAD), .LOCAL_PAIR_DIRECT_DECODE(LOCAL_PAIR_DIRECT_DECODE), .LOCAL_PAIR_HAZARD_VIEW(LOCAL_PAIR_HAZARD_VIEW), .MACHINE_MODE(MACHINE_MODE), .MEM_HANDSHAKE(MEM_HANDSHAKE), .MUL_ALU_PAIR(MUL_ALU_PAIR), .SERIALIZE_LOCAL_ADDRESS_RAW(SERIALIZE_LOCAL_ADDRESS_RAW), .SLOT1_MUL_PAIR(SLOT1_MUL_PAIR), .SPECULATIVE_RAM_PAIR(SPECULATIVE_RAM_PAIR), .THIRD_ALU_BYPASS(THIRD_ALU_BYPASS), .THIRD_ALU_SLOT(THIRD_ALU_SLOT), .THIRD_SOURCE_COFACTOR(THIRD_SOURCE_COFACTOR), .FETCH_PREDECODE(FETCH_PREDECODE), .ROM_SCHEDULE_ACTIVE(ROM_SCHEDULE_ACTIVE)
    ) u_issue (
        .csr_illegal(csr_illegal), .ex0_forward_data(ex0_forward_data),
        .ex0_forwardable(ex0_forwardable), .ex0_is_mul(ex0_is_mul),
        .ex0_load_ready(ex0_load_ready), .ex0_mem_read(ex0_mem_read),
        .ex0_rd(ex0_rd), .ex0_writes(ex0_writes),
        .ex1_forward_data(ex1_forward_data), .ex1_forwardable(ex1_forwardable),
        .ex1_is_mul(ex1_is_mul), .ex1_load_ready(ex1_load_ready),
        .ex1_mem_read(ex1_mem_read), .ex1_rd(ex1_rd),
        .ex1_shift_wb(ex1_shift_wb), .ex1_writes(ex1_writes),
        .ex2_id_forward_data(ex2_id_forward_data), .ex2_rd(ex2_rd),
        .ex2_writes(ex2_writes), .fast_mul_low(fast_mul_low),
        .fetch_fault0(fetch_fault0), .fetch_head0_valid(fetch_head0_valid),
        .fetch_head1_valid(fetch_head1_valid), .fetch_head2_valid(fetch_head2_valid),
        .fetch_pc(fetch_pc), .fetch_ready(fetch_ready),
        .fetch_started(fetch_started), .halted_o(halted_o),
        .id0_alu_b_sel(id0_alu_b_sel), .id0_alu_op(id0_alu_op),
        .id0_csr(id0_csr), .id0_eligible(id0_eligible),
        .id0_flow_type(id0_flow_type), .id0_imm(id0_imm),
        .id0_is_control(id0_is_control), .id0_is_mul(id0_is_mul),
        .id0_issue_class(id0_issue_class), .id0_mem_read(id0_mem_read),
        .id0_mem_write(id0_mem_write), .id0_rd(id0_rd),
        .id0_reg_write(id0_reg_write), .id0_rs1(id0_rs1),
        .id0_rs1_capture_data(id0_rs1_capture_data), .id0_rs1_data(id0_rs1_data),
        .id0_rs2(id0_rs2), .id0_rs2_capture_data(id0_rs2_capture_data),
        .id0_use_rs1(id0_use_rs1), .id0_use_rs2(id0_use_rs2),
        .id0_wait_ex(id0_wait_ex), .id0_wb_sel(id0_wb_sel),
        .id1_eligible(id1_eligible), .id1_flow_type(id1_flow_type),
        .id1_mem_read(id1_mem_read), .id1_mem_write(id1_mem_write),
        .id1_rd(id1_rd), .id1_reg_write(id1_reg_write),
        .id1_rs1(id1_rs1), .id1_rs1_capture_data(id1_rs1_capture_data),
        .id1_rs1_data(id1_rs1_data), .id1_rs2(id1_rs2),
        .id1_rs2_capture_data(id1_rs2_capture_data), .id1_simple_alu(id1_simple_alu),
        .id1_simple_branch(id1_simple_branch), .id1_simple_load(id1_simple_load),
        .id1_simple_mul(id1_simple_mul), .id1_simple_store(id1_simple_store),
        .id1_use_rs1(id1_use_rs1), .id1_use_rs2(id1_use_rs2),
        .id1_wait_ex(id1_wait_ex), .id2_instr(id2_instr),
        .id2_rd(id2_rd), .id2_reg_write(id2_reg_write),
        .id2_rs1(id2_rs1), .id2_rs2(id2_rs2),
        .id2_simple_alu(id2_simple_alu), .id2_use_rs1(id2_use_rs1),
        .id2_use_rs2(id2_use_rs2), .id2_wait_ex(id2_wait_ex),
        .id_word0(id_word0), .id_word1(id_word1),
        .if_branch_pred_taken(if_branch_pred_taken), .ifetch_error_i(ifetch_error_i),
        .machine_special(machine_special), .pipeline_empty(pipeline_empty),
        .accept0(accept0), .accept1(accept1),
        .accept2(accept2), .bad_slot0(bad_slot0),
        .id0_to_id1_pair_forwardable(id0_to_id1_pair_forwardable), .id1_mul_capture_rs1(id1_mul_capture_rs1),
        .id1_mul_capture_rs2(id1_mul_capture_rs2), .pair_dependency(pair_dependency),
        .pair_raw_rs1(pair_raw_rs1), .pair_raw_rs2(pair_raw_rs2),
        .pair_stall(pair_stall), .pair_structural_conflict(pair_structural_conflict)
    );
endmodule

`default_nettype wire
