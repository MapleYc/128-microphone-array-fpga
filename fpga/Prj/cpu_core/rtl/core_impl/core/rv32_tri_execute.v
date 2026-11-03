`default_nettype none
// 三槽执行、共享乘法器及主次访存接口。
// 保持原寄存边界与年龄优先级；组合输出由父模块统一接纳和提交。
module rv32_tri_execute #(
    parameter BRANCH_ALU_PAIR = 1'b0,
    parameter integer BRANCH_PREDICT = 2,
    parameter DOUBLE_LOAD = 1'b1,
    parameter FAST_MUL_LOW = 1'b1,
    parameter LOAD_BRANCH_PAIR = 1'b0,
    parameter LOAD_STORE_PAIR = 1'b1,
    parameter LOCAL_BRANCH_SOURCE_COFACTOR = 1'b1,
    parameter LOCAL_LOAD0_PAYLOAD_VIEW = 1'b0,
    parameter LOCAL_MEMORY_LOOKAHEAD = 1'b0,
    parameter LOCAL_MEMORY_RESPONSE_CLASS = 1'b0,
    parameter MEM_HANDSHAKE = 1'b0,
    parameter MUL_ALU_PAIR = 1'b0,
    parameter REGISTER_LOAD_BRANCH_RESULT = 1'b1,
    parameter REGISTER_MUL_RESULT = 1'b1,
    parameter SERIALIZE_LOCAL_ADDRESS_RAW = 1'b1,
    parameter SLOT1_MUL_PAIR = 1'b0,
    parameter SLOT1_RET_PREDICT = 1'b0,
    parameter SPECULATIVE_RAM_PAIR = 1'b1,
    parameter THIRD_ALU_BYPASS = 1'b1,
    parameter THIRD_ALU_SLOT = 1'b0,
    parameter  CAPTURE_ADDRESS = 0
) (
    input wire  clk,
    input wire [31:0] dmem1_rdata_i,
    input wire  dmem1_req_ready_i,
    input wire  dmem1_rsp_error_i,
    input wire  dmem1_rsp_valid_i,
    input wire [31:0] dmem_local_payload_i,
    input wire [1:0] dmem_local_ram_i,
    input wire [31:0] dmem_rdata_i,
    input wire  dmem_req_ready_i,
    input wire  dmem_rsp_error_i,
    input wire  dmem_rsp_valid_i,
    input wire [1:0] ex0_alu_a_sel,
    input wire  ex0_alu_b_sel,
    input wire [3:0] ex0_alu_op,
    input wire [2:0] ex0_branch_op,
    input wire  ex0_branch_pred_taken,
    input wire  ex0_fault,
    input wire [1:0] ex0_flow_type,
    input wire [31:0] ex0_imm,
    input wire  ex0_load_ready_fault,
    input wire [2:0] ex0_lsu_type,
    input wire  ex0_mem_read,
    input wire  ex0_mem_write,
    input wire  ex0_mul_decoded,
    input wire [1:0] ex0_mul_op_decoded,
    input wire [31:0] ex0_pc,
    input wire [4:0] ex0_rd,
    input wire  ex0_reg_write,
    input wire [31:0] ex0_rs1_data,
    input wire [31:0] ex0_rs2_data,
    input wire [31:0] ex0_target_pred,
    input wire  ex0_target_pred_valid,
    input wire  ex0_valid,
    input wire [1:0] ex1_alu_a_sel,
    input wire  ex1_alu_b_sel,
    input wire [3:0] ex1_alu_op,
    input wire [2:0] ex1_branch_op,
    input wire  ex1_branch_pred_taken,
    input wire [1:0] ex1_flow_type,
    input wire  ex1_fwd_slot0_rs1,
    input wire  ex1_fwd_slot0_rs2,
    input wire [31:0] ex1_imm,
    input wire [2:0] ex1_lsu_type,
    input wire  ex1_mem_read,
    input wire  ex1_mem_write,
    input wire  ex1_mul_decoded,
    input wire [1:0] ex1_mul_op_decoded,
    input wire [31:0] ex1_pc,
    input wire [4:0] ex1_rd,
    input wire  ex1_reg_write,
    input wire [31:0] ex1_rs1_data,
    input wire [31:0] ex1_rs2_data,
    input wire  ex1_valid,
    input wire [1:0] ex2_alu_a_sel,
    input wire  ex2_alu_b_sel,
    input wire [3:0] ex2_alu_op,
    input wire [31:0] ex2_imm,
    input wire [31:0] ex2_instr,
    input wire [31:0] ex2_pc,
    input wire [31:0] ex2_rs1_data,
    input wire [31:0] ex2_rs2_data,
    input wire  ex2_valid,
    input wire  halted_o,
    input wire [3:0] id0_alu_op,
    input wire  id0_is_mul,
    input wire [31:0] id0_rs1_capture_data,
    input wire [31:0] id0_rs2_capture_data,
    input wire [3:0] id1_alu_op,
    input wire [31:0] id1_mul_capture_rs1,
    input wire [31:0] id1_mul_capture_rs2,
    input wire [31:0] id1_rs1_capture_data,
    input wire [31:0] id1_rs2_capture_data,
    input wire  id1_simple_mul,
    input wire [31:0] local_ex0_alu_result,
    input wire [31:0] local_ex_address0,
    input wire [31:0] local_ex_address1,
    input wire [31:0] local_id0_alu_result,
    input wire  rst_n,
    input wire  stop_pending,
    output wire [31:0] dmem1_addr_o,
    output  wire dmem1_read_o,
    output  wire dmem1_rsp_ready_o,
    output wire [31:0] dmem1_wdata_o,
    output  wire dmem1_write_o,
    output wire [3:0] dmem1_wstrb_o,
    output wire [31:0] dmem_addr_o,
    output  wire dmem_read_o,
    output  wire dmem_rsp_ready_o,
    output wire [31:0] dmem_wdata_o,
    output  wire dmem_write_o,
    output wire [3:0] dmem_wstrb_o,
    output  wire ex0_branch_taken,
    output wire [31:0] ex0_control_next_pc,
    output wire [31:0] ex0_control_target,
    output  wire ex0_is_branch,
    output  wire ex0_is_jalr,
    output  wire ex0_is_mul,
    output  wire ex0_kill_slot1,
    output wire [31:0] ex0_mem_addr,
    output  wire ex0_mul_busy,
    output  wire ex0_redirect,
    output wire [31:0] ex0_result,
    output  wire ex0_target_misaligned,
    output  wire ex1_branch_taken,
    output wire [31:0] ex1_branch_target,
    output wire [31:0] ex1_control_next_pc,
    output  wire ex1_is_branch,
    output  wire ex1_is_jalr,
    output  wire ex1_is_mul,
    output wire [31:0] ex1_jalr_target,
    output  wire ex1_mem_active,
    output wire [31:0] ex1_mem_addr,
    output wire [31:0] ex1_no_shift_result,
    output  wire ex1_ram_pair_replay,
    output  wire ex1_redirect,
    output  wire ex1_replay,
    output wire [31:0] ex1_result,
    output  wire ex1_target_misaligned,
    output wire [31:0] ex2_result,
    output  wire ex_advance,
    output  wire ex_double_load,
    output  wire ex_load_store,
    output  wire fast_mul_low,
    output wire [31:0] load0_capture_data,
    output  wire lsu1_misaligned,
    output wire [31:0] lsu_load_data,
    output  wire lsu_misaligned,
    output  wire memory_error0,
    output  wire memory_error1,
    output wire [31:0] mul_forward_result,
    output wire [31:0] mul_result,
    output  wire primary_active,
    output  wire primary_slot1,
    output wire [31:0] slot1_load_data
);

















    wire [31:0] lsu_load_data_raw;
    wire [31:0] load_branch_result;
    wire  load_branch_result_valid;
    wire  load_branch_phase_ready;


    wire [31:0] ex1_independent_result;
    // 乘法操作类型在既有指令捕获边沿译码，避免放在共享乘法器的 EX 输入选择之前。
    // 控制字段与 ex*_alu_op 同沿捕获、同条件保持，不构成新增流水级。


    assign ex0_is_mul = ex0_valid && ex0_mul_decoded;

    wire [31:0] mul_result_raw;

    assign ex1_is_mul = SLOT1_MUL_PAIR && ex1_valid && ex1_mul_decoded;
    // 同组 MUL0 到 ALU1 的相关仍使用已寄存结果。
    // 直接 APM 结果仅供 WB 使用，不进入 EX/ID 数据前递。
    assign fast_mul_low = FAST_MUL_LOW && LOCAL_MEMORY_LOOKAHEAD && REGISTER_MUL_RESULT &&
        ((ex0_is_mul && ex0_mul_op_decoded==2'd0 &&
          !(ex1_valid && (ex1_fwd_slot0_rs1 || ex1_fwd_slot0_rs2))) ||
         (ex1_is_mul && ex1_mul_op_decoded==2'd0));
    wire  mul_phase_ready;
    generate if (REGISTER_MUL_RESULT) begin : g_mul_result_register
        reg [31:0] result_q;
        reg result_valid;
        assign mul_result = fast_mul_low ? mul_result_raw : result_q;
        assign mul_forward_result = result_q;
        assign mul_phase_ready = !(ex0_is_mul || ex1_is_mul) || fast_mul_low || result_valid;
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                result_q <= 32'b0;
                result_valid <= 1'b0;
            end else if (ex_advance) begin
                result_valid <= 1'b0;
            end else if (!result_valid && (ex0_is_mul || ex1_is_mul)) begin
                result_q <= mul_result_raw;
                result_valid <= 1'b1;
            end
        end
    end else begin : g_mul_result_original
        assign mul_result = mul_result_raw;
        assign mul_forward_result = mul_result_raw;
        assign mul_phase_ready = 1'b1;
    end endgenerate
    // 年轻 MUL 只消费老 AND/OR/XOR 的独立短逻辑结果，包含对应立即数指令。
    // 不把通用 ALU/乘法结果送回乘法器输入，避免组合反馈和长串联路径。
    wire [31:0] mul_logic_b = ex0_alu_b_sel ? ex0_imm : ex0_rs2_data;
    wire [31:0] mul_pair_logic_result = ex0_alu_op==4'd9 ? (ex0_rs1_data & mul_logic_b) :
                                             ex0_alu_op==4'd8 ? (ex0_rs1_data | mul_logic_b) : (ex0_rs1_data ^ mul_logic_b);
    // 本地模式的逻辑到乘法相关在已有操作数捕获边沿处理，不增加指令级或寄存器。
    wire [31:0] ex1_mul_a = LOCAL_MEMORY_LOOKAHEAD ? ex1_rs1_data :
                          ex1_fwd_slot0_rs1 ? mul_pair_logic_result : ex1_rs1_data;
    wire [31:0] ex1_mul_b = LOCAL_MEMORY_LOOKAHEAD ? ex1_rs2_data :
                          ex1_fwd_slot0_rs2 ? mul_pair_logic_result : ex1_rs2_data;
    wire [31:0] ex0_a = ex0_alu_a_sel==2'd0 ? ex0_rs1_data :
                           ex0_alu_a_sel==2'd1 ? ex0_pc : 32'b0;
    // MUL只向普通ALU前递；RAM Load使用下方独立的分支输入，不进入ALU/LSU地址。
    wire [31:0] ex0_pair_result = (MUL_ALU_PAIR && ex0_is_mul) ? mul_forward_result : ex0_result;
    wire [31:0] ex1_rs1_operand = ex1_fwd_slot0_rs1 ? ex0_pair_result : ex1_rs1_data;
    wire [31:0] ex1_rs2_operand = ex1_fwd_slot0_rs2 ? ex0_pair_result : ex1_rs2_data;
    // MUL0 的同组 RAW 只允许普通 ALU1 消费，不允许 Branch1 消费。
    // 有效 Branch1 可使用普通 ALU0 或专用 Load0 结果，避免把不可达乘法路径接到分支比较器。
    wire [31:0] ex1_branch_nonload_rs1 = (LOCAL_MEMORY_LOOKAHEAD && LOCAL_BRANCH_SOURCE_COFACTOR)
        ? (ex1_fwd_slot0_rs1 ? ex0_result : ex1_rs1_data) : ex1_rs1_operand;
    wire [31:0] ex1_branch_nonload_rs2 = (LOCAL_MEMORY_LOOKAHEAD && LOCAL_BRANCH_SOURCE_COFACTOR)
        ? (ex1_fwd_slot0_rs2 ? ex0_result : ex1_rs2_data) : ex1_rs2_operand;
    wire [31:0] ex1_branch_rs1 = (LOAD_BRANCH_PAIR && ex0_mem_read && ex1_fwd_slot0_rs1)
                               ? (REGISTER_LOAD_BRANCH_RESULT ? load_branch_result : lsu_load_data) : ex1_branch_nonload_rs1;
    wire [31:0] ex1_branch_rs2 = (LOAD_BRANCH_PAIR && ex0_mem_read && ex1_fwd_slot0_rs2)
                               ? (REGISTER_LOAD_BRANCH_RESULT ? load_branch_result : lsu_load_data) : ex1_branch_nonload_rs2;
    wire [31:0] ex1_a = ex1_alu_a_sel==2'd0 ? ex1_rs1_operand :
                            ex1_alu_a_sel==2'd1 ? ex1_pc : 32'b0;
    wire [31:0] ex0_b = ex0_alu_b_sel ? ex0_imm : ex0_rs2_data;
    wire [31:0] ex1_b = ex1_alu_b_sel ? ex1_imm : ex1_rs2_operand;
    // 本地 RAM 预读阶段已经在 EX 捕获前计算 ALU0，直接复用该结果。
    generate if(LOCAL_MEMORY_LOOKAHEAD) begin : g_local_alu0_result
        assign ex0_result=local_ex0_alu_result;
    end else begin : g_original_alu0_result
        rv32_alu u_alu0 (.a_i(ex0_a), .b_i(ex0_b), .alu_op_i(ex0_alu_op), .result_o(ex0_result));
    end endgenerate

    rv32_alu u_alu1 (.a_i(ex1_a), .b_i(ex1_b), .alu_op_i(ex1_alu_op),
        .result_o(ex1_result), .result_no_shift_o(ex1_no_shift_result));
    generate if(LOCAL_MEMORY_LOOKAHEAD && THIRD_ALU_SLOT && THIRD_ALU_BYPASS) begin : g_independent_alu1
        // 仅当 ALU1 不依赖槽 0 时，才允许 ALU1 到 ALU2 的相关。
        // 此时已寄存操作数等价于前递后操作数，且不会接入 MUL/ALU0 串联路径。
        wire [31:0] a=ex1_alu_a_sel==0 ? ex1_rs1_data : ex1_alu_a_sel==1 ? ex1_pc : 32'b0;
        wire [31:0] b=ex1_alu_b_sel ? ex1_imm : ex1_rs2_data;
        rv32_alu u_alu(.a_i(a),.b_i(b),.alu_op_i(ex1_alu_op),.result_o(ex1_independent_result));
    end else begin : g_shared_alu1_result
        assign ex1_independent_result=ex1_result;
    end endgenerate
    // 复用已有指令、目的寄存器和控制寄存器，不添加操作数标签。
    // 发射规则禁止形成生产者 0、生产者 1、消费者 2 的三级组合串联。
    wire  ex2_fwd0_rs1 = THIRD_ALU_BYPASS && ex2_valid && ex0_valid &&
                        ex0_reg_write && ex0_rd!=0 && ex2_alu_a_sel==0 && ex2_instr[19:15]==ex0_rd;
    wire  ex2_fwd1_rs1 = THIRD_ALU_BYPASS && ex2_valid && ex1_valid &&
                        ex1_reg_write && ex1_rd!=0 && ex2_alu_a_sel==0 && ex2_instr[19:15]==ex1_rd;
    wire  ex2_fwd0_rs2 = THIRD_ALU_BYPASS && ex2_valid && ex0_valid &&
                        ex0_reg_write && ex0_rd!=0 && !ex2_alu_b_sel && ex2_instr[24:20]==ex0_rd;
    wire  ex2_fwd1_rs2 = THIRD_ALU_BYPASS && ex2_valid && ex1_valid &&
                        ex1_reg_write && ex1_rd!=0 && !ex2_alu_b_sel && ex2_instr[24:20]==ex1_rd;
    wire [31:0] ex2_rs1_operand = ex2_fwd1_rs1 ? ex1_independent_result :
                                 ex2_fwd0_rs1 ? ex0_result : ex2_rs1_data;
    wire [31:0] ex2_rs2_operand = ex2_fwd1_rs2 ? ex1_independent_result :
                                 ex2_fwd0_rs2 ? ex0_result : ex2_rs2_data;
    wire [31:0] ex2_a = ex2_alu_a_sel==2'd0 ? ex2_rs1_operand :
                       ex2_alu_a_sel==2'd1 ? ex2_pc : 32'b0;
    wire [31:0] ex2_b = ex2_alu_b_sel ? ex2_imm : ex2_rs2_operand;
    generate if (THIRD_ALU_SLOT) begin : g_third_alu
        rv32_alu u_alu2 (.a_i(ex2_a), .b_i(ex2_b), .alu_op_i(ex2_alu_op), .result_o(ex2_result));
    end else begin : g_no_third_alu
        assign ex2_result = 32'b0;
    end endgenerate

    // 两槽位共享一个 Zmmul 单元；发射判定禁止同组两条 MUL。
    wire [1:0] ex0_mul_op = ex0_mul_op_decoded;
    wire [1:0] selected_mul_op = ex1_is_mul ? ex1_mul_op_decoded : ex0_mul_op;
    assign ex0_mul_busy = 1'b0;
    // APM 输入寄存器使用原 ID/EX 捕获边沿，不增加流水级。



    // 有效槽 0 MUL 优先；发射规则禁止同组接纳两条 MUL。
    generate if (LOCAL_MEMORY_LOOKAHEAD) begin : g_mul_capture
        wire select_id1 = SLOT1_MUL_PAIR && id1_simple_mul && !id0_is_mul;
        wire [1:0] capture_op = select_id1 ? (id1_alu_op - 4'd10) : (id0_alu_op - 4'd10);
        rv32_mul_unit_capture u_mul0 (
            .clk(clk), .rst_n(rst_n), .capture_i(ex_advance),
            .op_d_i(capture_op), .op_ex_i(selected_mul_op),
            .a_d_i(select_id1 ? id1_mul_capture_rs1 : id0_rs1_capture_data),
            .b_d_i(select_id1 ? id1_mul_capture_rs2 : id0_rs2_capture_data),
            .result_o(mul_result_raw)
        );
    end else begin : g_mul_combinational
        rv32_mul_unit u_mul0 (
            .op_i(selected_mul_op), .a_i(ex1_is_mul ? ex1_mul_a : ex0_rs1_data), .b_i(ex1_is_mul ? ex1_mul_b : ex0_rs2_data),
            .result_o(mul_result_raw)
        );
    end endgenerate

    // 条件分支和 JALR 在 EX 判定；未对齐或关闭提前路径的 JAL 也在此解析。
    // 当前核心不支持压缩指令，因此目标需 4 字节对齐。

    rv32_branch_cmp u_branch0 (
        .a_i(ex0_rs1_data), .b_i(ex0_rs2_data),
        .branch_op_i(ex0_branch_op), .taken_o(ex0_branch_taken)
    );

    rv32_branch_cmp u_branch1 (
        .a_i(ex1_branch_rs1), .b_i(ex1_branch_rs2),
        .branch_op_i(ex1_branch_op), .taken_o(ex1_branch_taken)
    );
    assign ex0_is_branch = ex0_valid && ex0_flow_type==2'd1;
    // 预测不跳分支后的年轻 ALU，只在分支实际不跳时生效。
    assign ex0_kill_slot1 = BRANCH_ALU_PAIR && ex0_is_branch && ex0_branch_taken;
    wire  ex0_is_jal = ex0_valid && ex0_flow_type==2'd2;
    assign ex0_is_jalr = ex0_valid && ex0_flow_type==2'd3;
    wire  ex0_control_resolve = ex0_is_branch || ex0_is_jal || ex0_is_jalr;
    wire  ex0_control_taken = ex0_is_jal || ex0_is_jalr ||
                             (ex0_is_branch && ex0_branch_taken);
    // JALR与访存地址均为rs1+imm，复用专用地址加法器，避免重复加法和通用ALU选择。
    // 有效地址、JALR 目标与故障 tval 必须对应同一条 EX 指令。
    // 下游保护条件保持原判定时刻。
    assign ex0_mem_addr = CAPTURE_ADDRESS ? local_ex_address0 : ex0_rs1_data + ex0_imm;
    assign ex0_control_target = ex0_is_jalr
                                   ? {ex0_mem_addr[31:1],1'b0} : ex0_result;
    assign ex0_control_next_pc = ex0_control_taken
                                    ? ex0_control_target : ex0_pc + 32'd4;
    assign ex0_target_misaligned = ex0_control_taken &&
                                 ex0_control_target[1:0]!=2'b00;
    wire  ex0_branch_mispredict = BRANCH_PREDICT!=0 && ex0_is_branch &&
                                 !ex0_target_misaligned &&
                                 (ex0_branch_taken != ex0_branch_pred_taken);
    // 已在 IF 处理的 JAL 进入 EX 时 flow_type 已清零，不会再次重定向。
    wire  ex0_jalr_mispredict = ex0_is_jalr &&
                               (!ex0_target_pred_valid || ex0_control_target!=ex0_target_pred);
    // 条件分支/间接跳转只有预测错误时重定向；实际未对齐异常仍独立检查。
    assign ex0_redirect = ex0_is_jal || ex0_jalr_mispredict ||
                        (BRANCH_PREDICT==0 ? ex0_is_branch : ex0_branch_mispredict);
    // Load的地址可能来自上一拍RAM返回值，IF不使用旧基址猜测RAM/MMIO。
    // 先接纳相关分支；EX用真实地址确认普通RAM。MMIO/越界则仅提交老Load，
    // 把年轻分支重放为slot0，沿用原WB旁路及顺序读取，不训练或报告分支异常。
    wire  ex1_load_branch_raw = LOAD_BRANCH_PAIR && ex0_valid && ex0_mem_read &&
                               ex1_valid && ex1_flow_type==2'd1 &&
                               (ex1_fwd_slot0_rs1 || ex1_fwd_slot0_rs2);
    wire  ex1_load_branch_replay = ex1_load_branch_raw && ex0_mem_addr[31:16]!=16'h1000;
    wire  load_branch_group = REGISTER_LOAD_BRANCH_RESULT && ex1_load_branch_raw && !ex1_load_branch_replay;
    assign ex1_is_branch = ex1_valid && ex1_flow_type==2'd1 && !ex1_load_branch_replay;
    assign ex1_is_jalr = SLOT1_RET_PREDICT && ex1_valid && ex1_flow_type==2'd3;
    // slot1仅接纳imm=0、无同组RAW的ret，实际目标取捕获rs1，仍清bit0/检查bit1。
    assign ex1_jalr_target = {ex1_rs1_data[31:1],1'b0};
    // ret不使用立即数；复用EX1已有imm寄存器保存预测目标，避免新增30个FF。
    wire [31:0] ex1_target_pred = ex1_imm;
    wire  ex1_target_pred_valid = ex1_is_jalr;
    // 有效条件分支的目标为 PC 加立即数，不经过桶形移位网络。
    function automatic [31:0] branch_target_sum;
        input reg [31:0] pc,imm;
        begin branch_target_sum=pc+imm; end
    endfunction
    assign ex1_branch_target = branch_target_sum(ex1_pc,ex1_imm);
    wire [31:0] ex1_branch_next_pc = ex1_branch_taken
                                   ? ex1_branch_target : ex1_pc + 32'd4;
    assign ex1_target_misaligned = (ex1_is_branch && ex1_branch_taken && ex1_branch_target[1:0]!=2'b00) ||
                                 (ex1_is_jalr && ex1_jalr_target[1]);
    wire  ex1_branch_mispredict = ex1_is_branch && !ex1_target_misaligned &&
                                 (BRANCH_PREDICT==0 ||
                                  ex1_branch_taken!=ex1_branch_pred_taken);
    wire  ex1_jalr_mispredict = ex1_is_jalr && ex1_jalr_target!=ex1_target_pred;

    assign ex1_replay = ex1_load_branch_replay || ex1_ram_pair_replay;
    assign ex1_redirect = ex1_branch_mispredict || ex1_jalr_mispredict || ex1_replay;
    assign ex1_control_next_pc = ex1_replay ? ex1_pc :
                                    ex1_is_jalr ? ex1_jalr_target : ex1_branch_next_pc;

    // 主 LSU 处理单条访存或双 Load 的 slot0；双 Load 的 slot1 使用下方只读 LSU。
    // Store/MMIO 仍走主 LSU，发射单元禁止其与另一条访存同拍执行。
    wire [31:0] lsu_addr;
    wire [31:0] lsu_wdata;
    wire [3:0] lsu_wstrb;
    wire  lsu_read;
    wire  lsu_write;

    assign ex1_mem_active = ex1_valid && (ex1_mem_read || ex1_mem_write);
    assign ex_double_load = DOUBLE_LOAD && ex0_valid && ex0_mem_read &&
                          ex1_valid && ex1_mem_read;
    // 配对仅接纳基址寄存器和有符号偏移相同、字对齐的 LW/SW。
    // Load0 保持原读响应路径；RAM 使用独立的下降沿读与上升沿广播写端口。
    assign ex_load_store = LOAD_STORE_PAIR && DOUBLE_LOAD && ex0_valid && ex0_mem_read &&
                         ex1_valid && ex1_mem_write;
    assign primary_slot1 = ex1_mem_active && !ex_double_load && !ex_load_store;
    // 访存只执行 rs1+立即数，不经通用 ALU 结果选择/移位网络；
    // slot1 保留同组 rs1 前递，避免无关 rs2 路径串入半周期 RAM 地址路径。
    // 有效访存的同组前递源只能是普通ALU；MUL/Load配对的年轻槽位不访存。
    // 独立此输入，防止不可执行的MUL/Load→ALU→LSU路径参与半周期时序分析。
    // 本地串行模式禁止访存地址源 rs1 的同组 RAW 配对。
    // 通用模式的地址前递与 Store 数据前递仍按原规则保留。
    wire [31:0] ex1_lsu_rs1 = (LOCAL_MEMORY_LOOKAHEAD && SERIALIZE_LOCAL_ADDRESS_RAW)
                            ? ex1_rs1_data :
                            (ex1_fwd_slot0_rs1 ? ex0_result : ex1_rs1_data);
    wire [31:0] ex1_lsu_rs2 = ex1_fwd_slot0_rs2 ? ex0_result : ex1_rs2_data;
    // 本地模式将同组访存基址 RAW 串行化；无效或非访存地址载荷不被体系结构消费。
    assign ex1_mem_addr = CAPTURE_ADDRESS ? local_ex_address1 : ex1_lsu_rs1 + ex1_imm;
    // 保持原配对类型：即使配对被拒绝，主端口仍只执行较老 Load0。
    // 年轻访存不得提前访问 MMIO；Load0 完成后从 ex1_pc 重放，较老故障优先。
    // 本地串行地址模式使用捕获时的地址类别；同址 LW/SW 共享基址与偏移，LS 配对不使用过期的 class1。
    // 其他配置仍使用原有效地址检查。
    assign ex1_ram_pair_replay =
        (LOCAL_MEMORY_LOOKAHEAD && LOCAL_MEMORY_RESPONSE_CLASS && SERIALIZE_LOCAL_ADDRESS_RAW)
        ? (SPECULATIVE_RAM_PAIR && (ex_double_load || ex_load_store) &&
           !(dmem_local_ram_i[0] &&
             (ex_double_load ? dmem_local_ram_i[1] : ex0_mem_addr[1:0]==2'b00)))
        : (SPECULATIVE_RAM_PAIR && (ex_double_load || ex_load_store) &&
           !(ex0_mem_addr[31:16]==16'h1000 && ex1_mem_addr[31:16]==16'h1000 &&
             (!ex_load_store || (ex0_mem_addr==ex1_mem_addr && ex0_mem_addr[1:0]==2'b00))));
    wire [31:0] memory_data0;
    wire [31:0] memory_data1;
    wire [31:0] memory_local_payload0;

    wire [31:0] lsu1_addr;
    wire [31:0] lsu1_load_data;
    wire  lsu1_read;

    rv32_lsu u_lsu (
        .addr_i(primary_slot1 ? ex1_mem_addr : ex0_mem_addr),
        .store_data_i(ex1_mem_active ? ex1_lsu_rs2 : ex0_rs2_data),
        .mem_rdata_i(memory_data0),
        .lsu_type_i(primary_slot1 ? ex1_lsu_type : ex0_lsu_type),
        .load_i(((ex0_valid && ex0_mem_read) || (primary_slot1 && ex1_mem_read)) &&
                !halted_o && !stop_pending && mul_phase_ready),
        .store_i(((ex0_valid && ex0_mem_write) || (ex1_valid && ex1_mem_write && !ex_load_store)) &&
                 !halted_o && !stop_pending && mul_phase_ready),
        .mem_addr_o(lsu_addr), .mem_wdata_o(lsu_wdata), .mem_wstrb_o(lsu_wstrb),
        .mem_read_o(lsu_read), .mem_write_o(lsu_write),
        .load_data_o(lsu_load_data_raw), .misaligned_o(lsu_misaligned)
    );
    assign primary_active = (lsu_read || lsu_write) && !lsu_misaligned;


    wire  primary_complete;
    wire  secondary_complete;


    wire  request0;
    wire  request1;
    wire  compound_store_active = ex_load_store && !ex1_ram_pair_replay &&
                                 primary_complete && !memory_error0 && !lsu_misaligned;
    wire  secondary_active = (lsu1_read && !lsu1_misaligned) || compound_store_active;
    // 捕获 MUL 期间保持整个 EX 指令组并禁止 LSU 活动，防止 Store/MMIO 重复产生副作用。
    assign ex_advance = mul_phase_ready && load_branch_phase_ready &&
                        (!MEM_HANDSHAKE || (primary_complete && secondary_complete));
    generate if (REGISTER_LOAD_BRANCH_RESULT) begin : g_load_branch_result_register
        reg [31:0] result_q;
        reg result_valid;
        assign load_branch_result = result_q;
        assign load_branch_result_valid = result_valid;
        // 较老指令的故障处理不依赖年轻分支的过期比较数据。
        assign load_branch_phase_ready = !load_branch_group || result_valid || ex0_fault;
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                result_q <= 32'b0;
                result_valid <= 1'b0;
            end else if (ex_advance) begin
                result_valid <= 1'b0;
            end else if (load_branch_group && !result_valid && mul_phase_ready &&
                         primary_complete && secondary_complete && !ex0_fault) begin
                result_q <= lsu_load_data_raw;
                result_valid <= 1'b1;
            end
        end
    end else begin : g_load_branch_result_original
        assign load_branch_result = 32'b0;
        assign load_branch_result_valid = 1'b0;
        assign load_branch_phase_ready = 1'b1;
    end endgenerate
    generate if(LOCAL_MEMORY_LOOKAHEAD && LOCAL_LOAD0_PAYLOAD_VIEW) begin:g_load0_payload
        wire [31:0] formatted;
        // 数据消费仍由 ex0_load_ready、捕获优先级和发射许可决定；有效 Load0 属于主端口 0。
        rv32_load_lane_select u_format(.offset_i(ex0_mem_addr[1:0]),
            .type_i(ex0_lsu_type),.load_i(1'b1),.store_i(1'b0),
            .data_i(memory_local_payload0),.data_o(formatted));
        assign load0_capture_data=load_branch_group && load_branch_result_valid
            ? load_branch_result : formatted;
    end else begin:g_load0_payload_original
        assign load0_capture_data=lsu_load_data;
    end endgenerate
    // Load 提交与下一组前递使用同一个已捕获值。
    // 固定周期端口在捕获后可释放地址，不影响 WB 数据。
    assign lsu_load_data = load_branch_group && load_branch_result_valid
                          ? load_branch_result : lsu_load_data_raw;
    generate if(MEM_HANDSHAKE) begin : g_memory_protocol
        rv32_mem_port u_port0 (
            .clk(clk),.rst_n(rst_n),.active_i(primary_active),.clear_i(ex_advance),
            .req_valid_o(request0),.req_ready_i(dmem_req_ready_i),
            .rsp_valid_i(dmem_rsp_valid_i),.rsp_ready_o(dmem_rsp_ready_o),
            .rsp_data_i(dmem_rdata_i),.rsp_error_i(dmem_rsp_error_i),
            .complete_o(primary_complete),.data_o(memory_data0),.error_o(memory_error0),
            .local_payload_i(dmem_local_payload_i),.local_payload_o(memory_local_payload0));
        rv32_mem_port u_port1 (
            .clk(clk),.rst_n(rst_n),.active_i(secondary_active),.clear_i(ex_advance),
            .req_valid_o(request1),.req_ready_i(dmem1_req_ready_i),
            .rsp_valid_i(dmem1_rsp_valid_i),.rsp_ready_o(dmem1_rsp_ready_o),
            .rsp_data_i(dmem1_rdata_i),.rsp_error_i(dmem1_rsp_error_i),
            .complete_o(secondary_complete),.data_o(memory_data1),.error_o(memory_error1));
    end else begin : g_fixed_memory
        assign request0=1;assign request1=1;
        assign primary_complete=1;assign secondary_complete=1;
        assign memory_data0=dmem_rdata_i;assign memory_data1=dmem1_rdata_i;
        assign memory_local_payload0=dmem_local_payload_i;
        assign memory_error0=0;assign memory_error1=0;
        assign dmem_rsp_ready_o=0;assign dmem1_rsp_ready_o=0;
    end endgenerate
    assign dmem_read_o = lsu_read && !lsu_misaligned && request0 &&
                         !(!MEM_HANDSHAKE && load_branch_group && load_branch_result_valid);
    // 有效 Load 请求才允许年轻的同址字写入。
    // Store 使能和故障不反馈到 Load 输入或对齐检查。
    assign dmem_write_o = (lsu_write || (!MEM_HANDSHAKE && ex_load_store && !ex1_ram_pair_replay && lsu_read)) && !lsu_misaligned && request0;
    assign dmem_addr_o = (dmem_read_o || dmem_write_o) ? lsu_addr : 32'b0;
    assign dmem_wdata_o = dmem_write_o ? (ex_load_store ? ex1_rs2_data : lsu_wdata) : 32'b0;
    assign dmem_wstrb_o = dmem_write_o ? (ex_load_store ? 4'b1111 : lsu_wstrb) : 4'b0;
    // 有效双 Load 时 EX0 的 flow_type 为零；仅简化该 LSU 输入，其他检查保持原语义。
    wire  secondary_load_enable_original = ex_double_load && !ex1_ram_pair_replay && !halted_o && !stop_pending && mul_phase_ready &&
                    !(MEM_HANDSHAKE ? (lsu_misaligned || ex0_target_misaligned) : ex0_fault);
    wire  secondary_load_enable = LOCAL_MEMORY_LOOKAHEAD
        ? (ex_double_load && !ex1_ram_pair_replay && !halted_o && !stop_pending && mul_phase_ready && !lsu_misaligned)
        : secondary_load_enable_original;
    generate if (DOUBLE_LOAD) begin : g_load1
        rv32_lsu u_lsu1 (
            .addr_i(ex1_mem_addr), .store_data_i(32'b0), .mem_rdata_i(memory_data1),
            .lsu_type_i(ex1_lsu_type),
            .load_i(secondary_load_enable),
            .store_i(1'b0), .mem_addr_o(lsu1_addr), .mem_wdata_o(),
            .mem_wstrb_o(), .mem_read_o(lsu1_read), .mem_write_o(),
            .load_data_o(lsu1_load_data), .misaligned_o(lsu1_misaligned)
        );
    end else begin : g_no_load1
        assign lsu1_addr = 32'b0;
        assign lsu1_read = 1'b0;
        assign lsu1_load_data = 32'b0;
        assign lsu1_misaligned = 1'b0;
    end endgenerate
    assign dmem1_read_o = lsu1_read && !lsu1_misaligned && request1;
    assign dmem1_write_o = MEM_HANDSHAKE && compound_store_active && request1;
    assign dmem1_addr_o = dmem1_write_o ? ex1_mem_addr : dmem1_read_o ? lsu1_addr : 32'b0;
    assign dmem1_wdata_o = dmem1_write_o ? ex1_rs2_data : 32'b0;
    assign dmem1_wstrb_o = dmem1_write_o ? 4'b1111 : 4'b0;
    assign slot1_load_data = ex_double_load ? lsu1_load_data : lsu_load_data;

endmodule
`default_nettype wire
