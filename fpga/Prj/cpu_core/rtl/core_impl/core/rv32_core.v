`default_nettype none
// 五级流水 RV32I/Zmmul 核：IF 取指 → ID 译码/读寄存器 → EX 运算/跳转
// → MEM 访存 → WB 写回。接口按“本周期给出地址、本周期取得数据”设计；
// 实板 ROM/RAM 用反相读时钟，在 CPU 下降沿读出，供下一上升沿采样。
// halted_o 是调试/基准测试停机状态，并非 RISC-V 精确异常入口。
module rv32_core #(
    parameter [31:0] RESET_PC = 32'h0000_0000,
    // 关闭 IF 提前 JAL 时，EARLY_JAL 保留原有 ID 提前跳转路径。
    parameter EARLY_JAL = 1'b1,
    parameter IF_EARLY_JAL = 1'b1,
    // 条件分支预测：0=仅 EX 判定；1=IF 后向跳/前向不跳；
    // 2=IF 两位饱和计数器 BHT，尚未训练的表项先用静态方向预测。
    parameter integer BRANCH_PREDICT = 2,
    parameter integer BHT_INDEX_BITS = 8
)(
    input wire clk, input wire rst_n,
    output wire [31:0] imem_addr_o, input wire [31:0] imem_rdata_i,
    output wire dmem_read_o, output wire dmem_write_o,
    output wire [31:0] dmem_addr_o, output wire [31:0] dmem_wdata_o,
    output wire [3:0] dmem_wstrb_o, input wire [31:0] dmem_rdata_i,
    output reg halted_o, output reg [31:0] halt_pc_o,
    output reg [3:0] halt_cause_o,
    output wire commit_valid_o, output wire [31:0] commit_pc_o,
    output wire [31:0] commit_instr_o, output wire commit_rd_write_o,
    output wire [4:0] commit_rd_addr_o, output wire [31:0] commit_rd_data_o
);
    // wb_sel 在译码、级间寄存器和写回处保持相同编码。
    localparam [1:0] WB_ALU=2'd0, WB_LOAD=2'd1, WB_LINK=2'd2;
    localparam [1:0] FWD_REG=2'd0, FWD_MEM=2'd1, FWD_WB=2'd2, FWD_WAIT=2'd3;
    localparam integer BHT_ENTRIES = 1 << BHT_INDEX_BITS;
    wire pc_en, if_id_en, if_id_flush, id_ex_en, id_ex_flush;
    wire haz_if_id_en, haz_if_id_flush;
    wire ex_redirect;
    wire ex_mem_en, ex_mem_flush, mem_wb_en, mem_wb_flush;
    wire halt_req, load_use;
    wire [31:0] if_pc, if_pc_plus4, if_next_pc, ex_next_pc, ex_raw_next_pc;
    // IF 直接从当前指令提取 B 型偏移；只有合法、对齐的分支才可预测。
    wire [31:0] if_branch_imm = {{20{imem_rdata_i[31]}},imem_rdata_i[7],
                                 imem_rdata_i[30:25],imem_rdata_i[11:8],1'b0};
    wire [31:0] if_branch_target = if_pc + if_branch_imm;
    wire if_branch_opcode = imem_rdata_i[6:0]==7'b1100011;
    wire if_branch_funct3_ok = imem_rdata_i[14:12]==3'b000 ||
                               imem_rdata_i[14:12]==3'b001 ||
                               imem_rdata_i[14:12]==3'b100 ||
                               imem_rdata_i[14:12]==3'b101 ||
                               imem_rdata_i[14:12]==3'b110 ||
                               imem_rdata_i[14:12]==3'b111;
    wire [BHT_INDEX_BITS-1:0] if_bht_index = if_pc[BHT_INDEX_BITS+1:2];
    // BHT 每项一个有效位和两位计数器；索引取 PC 的指令地址位。
    reg [1:0] bht_counter [0:BHT_ENTRIES-1];
    reg bht_valid [0:BHT_ENTRIES-1];
    wire if_branch_pred_taken = BRANCH_PREDICT!=0 && if_branch_opcode &&
                                if_branch_funct3_ok && if_branch_target[1:0]==2'b00 &&
                                ((BRANCH_PREDICT==1 || !bht_valid[if_bht_index])
                                 ? imem_rdata_i[31] : bht_counter[if_bht_index][1]);
    wire [31:0] if_pred_next_pc = if_branch_pred_taken ? if_branch_target : if_pc_plus4;
    wire if_valid = rst_n && !halted_o;
    // JAL 由 opcode 唯一确定，不检查 funct3/funct7。IF 拿到指令后立即
    // 拼接 J 型偏移；本核不支持压缩指令，因此目标必须按 4 字节对齐。
    wire [31:0] if_jal_imm = {{12{imem_rdata_i[31]}}, imem_rdata_i[19:12],
                              imem_rdata_i[20], imem_rdata_i[30:21], 1'b0};
    wire [31:0] if_jal_target = if_pc + if_jal_imm;
    wire if_jal_candidate = IF_EARLY_JAL && if_valid &&
                            imem_rdata_i[6:0]==7'b1101111 &&
                            if_jal_target[1:0]==2'b00;
    reg id_branch_pred_taken, ex_branch_pred_taken;
    reg id_jal_if_handled;
    wire id_valid;
    wire [31:0] id_instr, id_pc, id_pc_plus4;
    wire [4:0] id_rs1_addr = id_instr[19:15];
    wire [4:0] id_rs2_addr = id_instr[24:20];
    wire [4:0] id_rd_addr = id_instr[11:7];
    wire [31:0] id_rs1_raw, id_rs2_raw, id_rs1_data, id_rs2_data, id_imm;
    wire [2:0] id_imm_type;
    wire [3:0] id_alu_op;
    wire [1:0] id_alu_a_sel, id_wb_sel, id_flow_type, id_sys_op;
    wire id_alu_b_sel, id_reg_write, id_mem_read, id_mem_write;
    wire id_use_rs1, id_use_rs2, id_illegal;
    wire [2:0] id_branch_op, id_lsu_type;
    wire [31:0] id_jal_target = id_pc + id_imm;
    // 没在 IF 处理的 JAL 可由 ID 旧路径处理，避免对同一指令重复跳转。
    wire id_jal_candidate = EARLY_JAL && id_valid && !id_jal_if_handled && !id_illegal &&
                            id_flow_type==2'd2 && id_jal_target[1:0]==2'b00;
    // 只有流水线允许前进时才接纳 ID 跳转；更老的 EX 重定向/错误以及暂停优先。
    wire id_jal_fire = id_jal_candidate && pc_en && haz_if_id_en &&
                       !haz_if_id_flush && id_ex_en && !id_ex_flush;
    assign if_id_en = haz_if_id_en && !id_jal_fire;
    assign if_id_flush = haz_if_id_flush || id_jal_fire;
    // IF JAL 必须与指令一起进入 IF/ID；如果本拍停顿或冲刷，不能只改 PC。
    wire if_jal_fire = if_jal_candidate && pc_en && haz_if_id_en &&
                       !haz_if_id_flush && !id_jal_fire;
    // 下一 PC 的年龄优先级：EX 恢复 > ID 旧路径 JAL > IF JAL > IF 分支预测。
    assign if_next_pc = ex_redirect ? ex_next_pc :
                        id_jal_fire ? id_jal_target :
                        if_jal_fire ? if_jal_target : if_pred_next_pc;
    wire ex_valid;
    wire [31:0] ex_instr, ex_pc, ex_pc_plus4, ex_rs1_data, ex_rs2_data, ex_imm;
    wire [BHT_INDEX_BITS-1:0] ex_bht_index = ex_pc[BHT_INDEX_BITS+1:2];
    wire [4:0] ex_rs1_addr, ex_rs2_addr, ex_rd_addr;
    wire [3:0] ex_alu_op;
    wire [1:0] ex_alu_a_sel, ex_wb_sel, ex_flow_type, ex_sys_op;
    wire ex_alu_b_sel, ex_reg_write, ex_mem_read, ex_mem_write;
    wire ex_use_rs1, ex_use_rs2, ex_illegal;
    wire [2:0] ex_branch_op, ex_lsu_type;
    wire [1:0] fwd_rs1_sel, fwd_rs2_sel;
    reg [31:0] ex_rs1_fwd, ex_rs2_fwd, ex_alu_a;
    wire [31:0] ex_alu_b, ex_alu_result, ex_alu_result_comb;
    wire ex_branch_taken, ex_raw_redirect;
    wire ex_target_misaligned, ex_wait, ex_busy, ex_branch_mispredict;
    wire ex_mul, mul_start, mul_consume;
    reg mul_result_valid;
    wire [1:0] ex_mul_op = ex_alu_op - 4'd10;
    reg [31:0] mul_result;
    wire [31:0] mul_result_comb;
    wire mem_valid;
    wire [31:0] mem_instr, mem_pc, mem_pc_plus4, mem_alu_result, mem_store_data;
    wire [4:0] mem_rd_addr;
    wire [1:0] mem_wb_sel, mem_sys_op;
    wire [2:0] mem_lsu_type;
    wire mem_reg_write, mem_read, mem_write, mem_illegal, mem_target_misaligned;
    wire [31:0] mem_load_data, lsu_addr, lsu_wdata;
    wire [3:0] lsu_wstrb;
    wire lsu_read, lsu_write, mem_data_misaligned;
    reg [31:0] mem_fwd_data;
    wire mem_rd_write_intent, mem_result_ready;
    wire wb_valid;
    wire [31:0] wb_instr, wb_pc, wb_pc_plus4, wb_alu_result, wb_load_data;
    wire [4:0] wb_rd_addr;
    wire [1:0] wb_wb_sel, wb_sys_op;
    wire wb_reg_write, wb_illegal, wb_target_misaligned, wb_data_misaligned;
    reg [31:0] wb_data;
    wire wb_rd_write_intent, wb_write_enable;
    wire ex_control_bad, mem_control_bad, wb_control_bad;
    wire mem_known_fault, ex_stop, mem_stop, wb_stop, stop_now;
    reg [3:0] ex_cause, mem_cause, wb_cause, stop_cause;
    reg [31:0] stop_pc;

    // IF：PC 寄存器和 IF/ID 寄存器同沿更新；imem_addr_o 始终是当前取指 PC。
    assign imem_addr_o = if_pc;
    rv32_pc_reg #(.RESET_PC(RESET_PC)) u_pc (
        .clk(clk), .rst_n(rst_n), .en_i(pc_en), .next_pc_i(if_next_pc), .pc_o(if_pc)
    );
    rv32_if_id_reg u_if_id (
        .clk(clk), .rst_n(rst_n), .en_i(if_id_en), .flush_i(if_id_flush),
        .valid_i(if_valid), .pc_i(if_pc), .pc_plus4_i(if_pc_plus4), .instr_i(imem_rdata_i),
        .valid_o(id_valid), .pc_o(id_pc), .pc_plus4_o(id_pc_plus4), .instr_o(id_instr)
    );
    rv32_decoder u_decoder (
        .instr_i(id_instr), .imm_type_o(id_imm_type), .alu_op_o(id_alu_op),
        .alu_a_sel_o(id_alu_a_sel), .alu_b_sel_o(id_alu_b_sel), .wb_sel_o(id_wb_sel),
        .flow_type_o(id_flow_type), .branch_op_o(id_branch_op), .lsu_type_o(id_lsu_type),
        .reg_write_o(id_reg_write), .mem_read_o(id_mem_read), .mem_write_o(id_mem_write),
        .use_rs1_o(id_use_rs1), .use_rs2_o(id_use_rs2), .sys_op_o(id_sys_op), .illegal_o(id_illegal)
    );
    rv32_imm_gen u_imm (
        .instr_i(id_instr), .imm_type_i(id_imm_type), .imm_o(id_imm)
    );
    rv32_regfile u_regfile (
        .clk(clk), .rst_n(rst_n), .we(wb_write_enable), .waddr(wb_rd_addr), .wdata(wb_data),
        .raddr1(id_rs1_addr), .rdata1(id_rs1_raw), .raddr2(id_rs2_addr), .rdata2(id_rs2_raw)
    );
    // 同沿 WB→ID 旁路：寄存器堆的非阻塞写入不会在同一上升沿及时到达 ID/EX。
    assign id_rs1_data = (id_use_rs1 && wb_write_enable && id_rs1_addr==wb_rd_addr)
                       ? wb_data : id_rs1_raw;
    assign id_rs2_data = (id_use_rs2 && wb_write_enable && id_rs2_addr==wb_rd_addr)
                       ? wb_data : id_rs2_raw;
    rv32_id_ex_reg u_id_ex (
        .clk(clk), .rst_n(rst_n), .en_i(id_ex_en), .flush_i(id_ex_flush),
        .valid_i(id_valid), .instr_i(id_instr), .pc_i(id_pc), .pc_plus4_i(id_pc_plus4),
        .rs1_data_i(id_rs1_data), .rs2_data_i(id_rs2_data), .imm_i(id_imm),
        .rs1_addr_i(id_rs1_addr), .rs2_addr_i(id_rs2_addr), .rd_addr_i(id_rd_addr),
        .alu_op_i(id_alu_op), .alu_a_sel_i(id_alu_a_sel), .alu_b_sel_i(id_alu_b_sel),
        // 提前处理过的 JAL 仍需向后推进，以在 WB 写入 rd=PC+4；
        // 但流控类型改为顺序执行，防止到 EX 时再次跳转。
        .wb_sel_i(id_wb_sel), .flow_type_i((id_jal_fire || id_jal_if_handled) ? 2'd0 : id_flow_type), .branch_op_i(id_branch_op),
        .lsu_type_i(id_lsu_type), .reg_write_i(id_reg_write), .mem_read_i(id_mem_read),
        .mem_write_i(id_mem_write), .use_rs1_i(id_use_rs1), .use_rs2_i(id_use_rs2),
        .sys_op_i(id_sys_op), .illegal_i(id_illegal),
        .valid_o(ex_valid), .instr_o(ex_instr), .pc_o(ex_pc), .pc_plus4_o(ex_pc_plus4),
        .rs1_data_o(ex_rs1_data), .rs2_data_o(ex_rs2_data), .imm_o(ex_imm),
        .rs1_addr_o(ex_rs1_addr), .rs2_addr_o(ex_rs2_addr), .rd_addr_o(ex_rd_addr),
        .alu_op_o(ex_alu_op), .alu_a_sel_o(ex_alu_a_sel), .alu_b_sel_o(ex_alu_b_sel),
        .wb_sel_o(ex_wb_sel), .flow_type_o(ex_flow_type), .branch_op_o(ex_branch_op),
        .lsu_type_o(ex_lsu_type), .reg_write_o(ex_reg_write), .mem_read_o(ex_mem_read),
        .mem_write_o(ex_mem_write), .use_rs1_o(ex_use_rs1), .use_rs2_o(ex_use_rs2),
        .sys_op_o(ex_sys_op), .illegal_o(ex_illegal)
    );

    // 预测位和“IF 已处理 JAL”标记必须与对应指令一起保持/冲刷。
    // 只有有效且未触发停机的 EX 分支才能训练 BHT。
    integer bht_reset_idx;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            id_branch_pred_taken <= 1'b0;
            ex_branch_pred_taken <= 1'b0;
            id_jal_if_handled <= 1'b0;
            for (bht_reset_idx=0; bht_reset_idx<BHT_ENTRIES;
                 bht_reset_idx=bht_reset_idx+1)
                bht_valid[bht_reset_idx] <= 1'b0;
        end else begin
            if (if_id_flush) begin
                id_branch_pred_taken <= 1'b0;
                id_jal_if_handled <= 1'b0;
            end else if (if_id_en) begin
                id_branch_pred_taken <= if_branch_pred_taken;
                id_jal_if_handled <= if_jal_fire;
            end
            if (id_ex_flush) ex_branch_pred_taken <= 1'b0;
            else if (id_ex_en) ex_branch_pred_taken <= id_branch_pred_taken;
            if (BRANCH_PREDICT==2 && ex_valid && ex_flow_type==2'd1 &&
                !halted_o && !stop_now) begin
                bht_valid[ex_bht_index] <= 1'b1;
                if (!bht_valid[ex_bht_index])
                    bht_counter[ex_bht_index] <= ex_branch_taken ? 2'b10 : 2'b01;
                else if (ex_branch_taken && bht_counter[ex_bht_index]!=2'b11)
                    bht_counter[ex_bht_index] <= bht_counter[ex_bht_index]+2'd1;
                else if (!ex_branch_taken && bht_counter[ex_bht_index]!=2'b00)
                    bht_counter[ex_bht_index] <= bht_counter[ex_bht_index]-2'd1;
            end
        end
    end

    // 前递判断使用“写意图”，与最终提交使能分开。Load 在 MEM 仍有写意图，
    // 但结果尚未就绪：若它与源寄存器相同，应等待，不能错误地改用更老的 WB 值。
    assign mem_rd_write_intent = mem_reg_write && !mem_stop;
    assign wb_rd_write_intent = wb_reg_write && !wb_stop;
    assign mem_result_ready = !mem_read && (mem_wb_sel==WB_ALU || mem_wb_sel==WB_LINK);
    always @(*) begin
        case (mem_wb_sel)
            WB_ALU:  mem_fwd_data = mem_alu_result;
            WB_LINK: mem_fwd_data = mem_pc_plus4;
            default: mem_fwd_data = 32'b0;
        endcase
        case (wb_wb_sel)
            WB_ALU:  wb_data = wb_alu_result;
            WB_LOAD: wb_data = wb_load_data;
            WB_LINK: wb_data = wb_pc_plus4;
            default: wb_data = 32'b0;
        endcase
    end
    rv32_forward_unit u_forward (
        .ex_valid_i(ex_valid), .ex_use_rs1_i(ex_use_rs1), .ex_use_rs2_i(ex_use_rs2),
        .ex_rs1_addr_i(ex_rs1_addr), .ex_rs2_addr_i(ex_rs2_addr),
        .mem_valid_i(mem_valid), .mem_rd_write_i(mem_rd_write_intent),
        .mem_rd_addr_i(mem_rd_addr), .mem_result_ready_i(mem_result_ready),
        .wb_valid_i(wb_valid), .wb_rd_write_i(wb_rd_write_intent), .wb_rd_addr_i(wb_rd_addr),
        .fwd_rs1_sel_o(fwd_rs1_sel), .fwd_rs2_sel_o(fwd_rs2_sel)
    );
    always @(*) begin
        case (fwd_rs1_sel)
            FWD_REG: ex_rs1_fwd = ex_rs1_data;
            FWD_MEM: ex_rs1_fwd = mem_fwd_data;
            FWD_WB:  ex_rs1_fwd = wb_data;
            default: ex_rs1_fwd = 32'b0; // 前递结果未就绪时停机，不得使用这里的默认零值。
        endcase
        case (fwd_rs2_sel)
            FWD_REG: ex_rs2_fwd = ex_rs2_data;
            FWD_MEM: ex_rs2_fwd = mem_fwd_data;
            FWD_WB:  ex_rs2_fwd = wb_data;
            default: ex_rs2_fwd = 32'b0;
        endcase
        case (ex_alu_a_sel)
            2'd0: ex_alu_a = ex_rs1_fwd;
            2'd1: ex_alu_a = ex_pc;
            2'd2: ex_alu_a = 32'b0;
            default: ex_alu_a = 32'b0;
        endcase
    end
    // EX 的 rs1/rs2 先经前递选择，再供 ALU、乘法器和分支比较器使用。
    assign ex_alu_b = ex_alu_b_sel ? ex_imm : ex_rs2_fwd;
    assign ex_wait = ex_valid && (fwd_rs1_sel==FWD_WAIT || fwd_rs2_sel==FWD_WAIT);
    assign ex_mul = ex_valid && !ex_illegal && ex_alu_op>=4'd10 && ex_alu_op<=4'd13;
    // 乘法启动后保持当前 EX 指令一拍。乘积写入独立结果寄存器后，
    // 下一拍按普通 ALU 结果进入 EX/MEM；这不是异常，不产生 halt_req。
    assign ex_busy = ex_mul && !mul_result_valid && !ex_wait;
    assign mul_start = ex_busy && !stop_now && !halted_o;
    assign mul_consume = ex_mul && mul_result_valid && ex_mem_en &&
                         !ex_mem_flush && !stop_now;
    rv32_alu u_alu (
        .a_i(ex_alu_a), .b_i(ex_alu_b), .alu_op_i(ex_alu_op), .result_o(ex_alu_result_comb)
    );
    rv32_mul_unit u_mul (
        // 译码器使用 10..13 表示 MUL/MULH/MULHSU/MULHU；乘法单元使用 0..3。
        .op_i(ex_mul_op), .a_i(ex_rs1_fwd), .b_i(ex_rs2_fwd),
        .result_o(mul_result_comb)
    );
    // 共享乘法单元已采用组合接口；单发射核心在本地保留原结果寄存器
    // 与启动/消费握手，继续使用原先的一拍 EX 等待和前递时序。
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            mul_result_valid <= 1'b0;
            mul_result <= 32'b0;
        end else if (mul_start) begin
            mul_result <= mul_result_comb;
            mul_result_valid <= 1'b1;
        end else if (mul_consume) begin
            mul_result_valid <= 1'b0;
        end
    end
    assign ex_alu_result = ex_mul ? mul_result : ex_alu_result_comb;
    rv32_branch_cmp u_branch (
        .a_i(ex_rs1_fwd), .b_i(ex_rs2_fwd), .branch_op_i(ex_branch_op), .taken_o(ex_branch_taken)
    );
    rv32_next_pc u_next_pc (
        .pc_i(if_pc), .target_i(ex_alu_result), .flow_type_i(ex_flow_type),
        .branch_taken_i(ex_branch_taken),
        .flow_valid_i(ex_valid && !ex_illegal && !ex_sys_op[1] && !ex_control_bad),
        .pc_plus4_o(if_pc_plus4), .next_pc_o(ex_raw_next_pc), .redirect_o(ex_raw_redirect),
        .target_misaligned_o(ex_target_misaligned)
    );
    // next_pc 保留实际跳转目标的对齐诊断；启用预测后，条件分支仅在
    // 实际方向与随指令传下来的预测方向不一致时冲刷流水线。
    assign ex_branch_mispredict = BRANCH_PREDICT!=0 && ex_valid &&
        ex_flow_type==2'd1 && !ex_illegal && !ex_sys_op[1] &&
        !ex_control_bad && !ex_target_misaligned && !ex_wait &&
        (ex_branch_taken != ex_branch_pred_taken);
    assign ex_redirect = (BRANCH_PREDICT!=0 && ex_flow_type==2'd1)
                       ? ex_branch_mispredict : ex_raw_redirect;
    assign ex_next_pc = (BRANCH_PREDICT!=0 && ex_flow_type==2'd1)
                      ? (ex_branch_taken ? ex_alu_result : ex_pc_plus4)
                      : ex_raw_next_pc;
    rv32_ex_mem_reg u_ex_mem (
        .clk(clk), .rst_n(rst_n), .en_i(ex_mem_en), .flush_i(ex_mem_flush),
        .valid_i(ex_valid), .instr_i(ex_instr), .pc_i(ex_pc), .pc_plus4_i(ex_pc_plus4),
        .alu_result_i(ex_alu_result), .store_data_i(ex_rs2_fwd), .rd_addr_i(ex_rd_addr),
        .wb_sel_i(ex_wb_sel), .lsu_type_i(ex_lsu_type), .reg_write_i(ex_reg_write),
        .mem_read_i(ex_mem_read), .mem_write_i(ex_mem_write), .sys_op_i(ex_sys_op),
        .illegal_i(ex_illegal), .target_misaligned_i(ex_target_misaligned),
        .valid_o(mem_valid), .instr_o(mem_instr), .pc_o(mem_pc), .pc_plus4_o(mem_pc_plus4),
        .alu_result_o(mem_alu_result), .store_data_o(mem_store_data), .rd_addr_o(mem_rd_addr),
        .wb_sel_o(mem_wb_sel), .lsu_type_o(mem_lsu_type), .reg_write_o(mem_reg_write),
        .mem_read_o(mem_read), .mem_write_o(mem_write), .sys_op_o(mem_sys_op),
        .illegal_o(mem_illegal), .target_misaligned_o(mem_target_misaligned)
    );

    // MEM 错误必须从驻留在级间寄存器的指令判定，不能从 halt_req 反推；
    // 否则“未对齐→停止访存→未对齐消失”可能形成组合反馈。
    assign mem_known_fault = mem_illegal || mem_target_misaligned || mem_sys_op[1] || mem_control_bad;
    rv32_lsu u_lsu (
        .addr_i(mem_alu_result), .store_data_i(mem_store_data), .mem_rdata_i(dmem_rdata_i),
        .lsu_type_i(mem_lsu_type), .load_i(mem_valid && !mem_known_fault && mem_read),
        .store_i(mem_valid && !mem_known_fault && mem_write),
        .mem_addr_o(lsu_addr), .mem_wdata_o(lsu_wdata), .mem_wstrb_o(lsu_wstrb),
        .mem_read_o(lsu_read), .mem_write_o(lsu_write), .load_data_o(mem_load_data),
        .misaligned_o(mem_data_misaligned)
    );
    assign dmem_read_o = rst_n && !halted_o && !halt_req && lsu_read;
    assign dmem_write_o = rst_n && !halted_o && !halt_req && lsu_write;
    assign dmem_addr_o = (dmem_read_o || dmem_write_o) ? lsu_addr : 32'b0;
    assign dmem_wdata_o = dmem_write_o ? lsu_wdata : 32'b0;
    assign dmem_wstrb_o = dmem_write_o ? lsu_wstrb : 4'b0;
    rv32_mem_wb_reg u_mem_wb (
        .clk(clk), .rst_n(rst_n), .en_i(mem_wb_en), .flush_i(mem_wb_flush),
        .valid_i(mem_valid), .instr_i(mem_instr), .pc_i(mem_pc), .pc_plus4_i(mem_pc_plus4),
        .alu_result_i(mem_alu_result), .load_data_i(mem_load_data), .rd_addr_i(mem_rd_addr),
        .wb_sel_i(mem_wb_sel), .reg_write_i(mem_reg_write), .sys_op_i(mem_sys_op),
        .illegal_i(mem_illegal), .target_misaligned_i(mem_target_misaligned),
        .data_misaligned_i(mem_data_misaligned),
        .valid_o(wb_valid), .instr_o(wb_instr), .pc_o(wb_pc), .pc_plus4_o(wb_pc_plus4),
        .alu_result_o(wb_alu_result), .load_data_o(wb_load_data), .rd_addr_o(wb_rd_addr),
        .wb_sel_o(wb_wb_sel), .reg_write_o(wb_reg_write), .sys_op_o(wb_sys_op),
        .illegal_o(wb_illegal), .target_misaligned_o(wb_target_misaligned),
        .data_misaligned_o(wb_data_misaligned)
    );

    // 控制信号自相矛盾属于内部故障；它与指令编码本身非法分开报告。
    assign ex_control_bad = ex_alu_a_sel==2'd3 || ex_wb_sel==2'd3 ||
                            (ex_mem_read && ex_mem_write) || ex_alu_op>4'd13;
    assign mem_control_bad = mem_wb_sel==2'd3 || (mem_read && mem_write);
    assign wb_control_bad = wb_wb_sel==2'd3;
    // 停机原因编码：1 非法指令；2 跳转目标未对齐；3 数据未对齐；
    // 4/5 系统指令；6 前递结果等待；7 内部控制错误。
    // 这是当前调试接口约定，不等同于 RISC-V mcause 编码。
    always @(*) begin
        ex_cause = 4'd0;
        if (ex_illegal) ex_cause = 4'd1;
        else if (ex_target_misaligned) ex_cause = 4'd2;
        else if (ex_sys_op==2'd2) ex_cause = 4'd4;
        else if (ex_sys_op==2'd3) ex_cause = 4'd5;
        else if (ex_wait) ex_cause = 4'd6;
        else if (ex_control_bad) ex_cause = 4'd7;
        mem_cause = 4'd0;
        if (mem_illegal) mem_cause = 4'd1;
        else if (mem_target_misaligned) mem_cause = 4'd2;
        else if (mem_data_misaligned) mem_cause = 4'd3;
        else if (mem_sys_op==2'd2) mem_cause = 4'd4;
        else if (mem_sys_op==2'd3) mem_cause = 4'd5;
        else if (mem_control_bad) mem_cause = 4'd7;
        wb_cause = 4'd0;
        if (wb_illegal) wb_cause = 4'd1;
        else if (wb_target_misaligned) wb_cause = 4'd2;
        else if (wb_data_misaligned) wb_cause = 4'd3;
        else if (wb_sys_op==2'd2) wb_cause = 4'd4;
        else if (wb_sys_op==2'd3) wb_cause = 4'd5;
        else if (wb_control_bad) wb_cause = 4'd7;
    end
    assign ex_stop = ex_valid && ex_cause!=4'd0;
    assign mem_stop = mem_valid && mem_cause!=4'd0;
    assign wb_stop = wb_valid && wb_cause!=4'd0;
    // 同一拍有多个停机候选时，选择更老的 WB，再 MEM，最后 EX。
    assign stop_now = wb_stop || mem_stop || ex_stop;
    always @(*) begin
        stop_pc = 32'b0;
        stop_cause = 4'b0;
        if (wb_stop) begin stop_pc=wb_pc; stop_cause=wb_cause; end
        else if (mem_stop) begin stop_pc=mem_pc; stop_cause=mem_cause; end
        else if (ex_stop) begin stop_pc=ex_pc; stop_cause=ex_cause; end
    end
    // 冒险控制单元统一产生 PC 与四组级间寄存器的 en/flush；
    // load-use 插入气泡，EX 重定向冲刷年轻指令，停机冻结整体流水线。
    rv32_hazard_unit u_hazard (
        .id_valid_i(id_valid), .id_use_rs1_i(id_use_rs1), .id_use_rs2_i(id_use_rs2),
        .id_rs1_addr_i(id_rs1_addr), .id_rs2_addr_i(id_rs2_addr),
        .ex_valid_i(ex_valid), .ex_mem_read_i(ex_mem_read),
        .ex_rd_write_i(ex_reg_write && !ex_illegal), .ex_rd_addr_i(ex_rd_addr),
        .ex_redirect_i(ex_redirect), .ex_wait_i(ex_wait), .ex_busy_i(ex_busy),
        .halt_i(halted_o || stop_now),
        .pc_en_o(pc_en), .if_id_en_o(haz_if_id_en), .if_id_flush_o(haz_if_id_flush),
        .id_ex_en_o(id_ex_en), .id_ex_flush_o(id_ex_flush),
        .ex_mem_en_o(ex_mem_en), .ex_mem_flush_o(ex_mem_flush),
        .mem_wb_en_o(mem_wb_en), .mem_wb_flush_o(mem_wb_flush),
        .halt_req_o(halt_req), .load_use_o(load_use)
    );
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            halted_o <= 1'b0;
            halt_pc_o <= 32'b0;
            halt_cause_o <= 4'b0;
        end else if (!halted_o && halt_req) begin
            halted_o <= 1'b1;
            halt_pc_o <= stop_pc;
            halt_cause_o <= stop_cause;
        end
    end
    // commit_* 是 WB 退休观测接口。有效退休与实际写寄存器分开：
    // Store/Branch 也可以退休，而写 x0 始终被屏蔽。
    assign commit_valid_o = rst_n && !halted_o && !halt_req && wb_valid && !wb_stop;
    assign wb_write_enable = commit_valid_o && wb_rd_write_intent && wb_rd_addr!=5'd0;
    assign commit_pc_o = commit_valid_o ? wb_pc : 32'b0;
    assign commit_instr_o = commit_valid_o ? wb_instr : 32'b0;
    assign commit_rd_write_o = wb_write_enable;
    assign commit_rd_addr_o = wb_write_enable ? wb_rd_addr : 5'b0;
    assign commit_rd_data_o = wb_write_enable ? wb_data : 32'b0;
endmodule
`default_nettype wire
