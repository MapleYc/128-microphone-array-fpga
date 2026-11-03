`default_nettype none
// 分支方向、循环、间接跳转与返回地址预测。
// 保持原寄存边界与年龄优先级；组合输出由父模块统一接纳和提交。
module rv32_tri_predict #(
    parameter integer BHT_INDEX_BITS = 8,
    parameter integer BRANCH_PREDICT = 2,
    parameter IF_EARLY_JAL = 1'b1,
    parameter JALR_BTB = 1'b0,
    parameter LOCAL_MEMORY_LOOKAHEAD = 1'b0,
    parameter integer LOOP_COUNTER_BITS = 16,
    parameter LOOP_PREDICT = 1'b0,
    parameter PARALLEL_BHT_READ = 1'b1,
    parameter RAS_PREDICT = 1'b0,
    parameter SLOT1_DYNAMIC_BHT = 1'b1,
    parameter SLOT1_MUL_PAIR = 1'b0,
    parameter SLOT1_RET_PREDICT = 1'b0
) (
    input wire [BHT_INDEX_BITS-1:0] bht_train_index,
    input wire  bht_train_taken,
    input wire  bht_train_valid,
    input wire [31:0] btb_train_pc,
    input wire [31:0] btb_train_target,
    input wire  btb_train_valid,
    input wire  clk,
    input wire [31:0] fetch_pc,
    input wire [31:0] fetch_pc_plus4,
    input wire  fetch_started,
    input wire [1:0] id0_flow_type,
    input wire [6:0] id0_issue_class,
    input wire [4:0] id0_rd,
    input wire  id0_reg_write,
    input wire [4:0] id0_rs1,
    input wire [1:0] id1_flow_type,
    input wire  id1_illegal,
    input wire [6:0] id1_issue_class,
    input wire [4:0] id1_rd,
    input wire [4:0] id1_rs1,
    input wire [6:0] id2_issue_class,
    input wire [31:0] id_word0,
    input wire [31:0] id_word1,
    input wire [31:0] loop_train_pc,
    input wire  loop_train_taken,
    input wire  loop_train_valid,
    input wire  ras_pop,
    input wire  ras_push,
    input wire [31:0] ras_push_pc,
    input wire  ras_update_valid,
    input wire  rst_n,
    output  wire id0_eligible,
    output  wire id0_is_control,
    output  wire id0_is_mul,
    output  wire id0_ras_pop_hint,
    output  wire id0_ras_push_hint,
    output  wire id1_eligible,
    output  wire id1_ras_push_hint,
    output  wire id1_ret_candidate,
    output  wire id1_return,
    output  wire id1_simple_alu,
    output  wire id1_simple_branch,
    output  wire id1_simple_load,
    output  wire id1_simple_mul,
    output  wire id1_simple_store,
    output  wire id2_simple_alu,
    output  wire if_branch_pred_taken,
    output wire [31:0] if_branch_target /* synthesis syn_keep=1 */,
    output wire [31:0] if_btb_target,
    output  wire if_jal_candidate,
    output wire [31:0] if_jal_target /* synthesis syn_keep=1 */,
    output wire [31:0] if_jalr_pred_target,
    output  wire if_jalr_pred_valid,
    output  wire if_jalr_return,
    output wire [31:0] if_ras_target,
    output  wire if_slot1_branch_pred_taken,
    output wire [31:0] if_slot1_branch_target /* synthesis syn_keep=1 */,
    output  wire if_slot1_jal_candidate,
    output wire [31:0] if_slot1_jal_target /* synthesis syn_keep=1 */
);
    localparam integer BHT_ENTRIES = 1 << BHT_INDEX_BITS;
    reg [1:0] bht_counter[0:BHT_ENTRIES-1];
    reg  bht_valid[0:BHT_ENTRIES-1];
    integer  bht_reset_idx;
    // 提前跳转直接拼接 B/J 型立即数，避免通用译码/立即数选择串入 PC 路径。
    wire [31:0] if_branch_imm0 = {{20{id_word0[31]}}, id_word0[7],
                                 id_word0[30:25], id_word0[11:8], 1'b0};
    wire [31:0] if_branch_imm1 = {{20{id_word1[31]}}, id_word1[7],
                                 id_word1[30:25], id_word1[11:8], 1'b0};
    wire [31:0] if_jal_imm0 = {{12{id_word0[31]}}, id_word0[19:12],
                              id_word0[20], id_word0[30:21], 1'b0};
    wire [31:0] if_jal_imm1 = {{12{id_word1[31]}}, id_word1[19:12],
                              id_word1[20], id_word1[30:21], 1'b0};
    // 每组最多预测一个分支；单读模式按原始 opcode 选择索引，双读模式提前查表。
    // 不以 accept1 选索引，避免预测结果经 PC/配对条件反馈造成组合环。
    wire  if_bht_select1 = SLOT1_DYNAMIC_BHT && id_word0[6:0]!=7'b1100011;
    wire [BHT_INDEX_BITS-1:0] if_bht_index = if_bht_select1
                        ? fetch_pc_plus4[BHT_INDEX_BITS+1:2] : fetch_pc[BHT_INDEX_BITS+1:2];
    wire  if_bht_hit = bht_valid[if_bht_index];
    wire  if_bht_taken = bht_counter[if_bht_index][1];
    // 相邻 PC 在前一上升沿已经确定，可提前读预测表；指令译码在 ROM 输出后进行。
    // 保留单读选择路径以支持参数关闭时的相同周期行为。
    wire  if_bht_hit0 = PARALLEL_BHT_READ ? bht_valid[fetch_pc[BHT_INDEX_BITS+1:2]] : if_bht_hit;
    wire  if_bht_taken0 = PARALLEL_BHT_READ ? bht_counter[fetch_pc[BHT_INDEX_BITS+1:2]][1] : if_bht_taken;
    wire  if_bht_hit1 = PARALLEL_BHT_READ ? bht_valid[fetch_pc_plus4[BHT_INDEX_BITS+1:2]] : if_bht_hit;
    wire  if_bht_taken1 = PARALLEL_BHT_READ ? bht_counter[fetch_pc_plus4[BHT_INDEX_BITS+1:2]][1] : if_bht_taken;
    // 循环表复用单分支PC选择；不得用accept1选地址，以免预测与配对形成组合环。
    wire [31:0] if_loop_pc = if_bht_select1 ? fetch_pc_plus4 : fetch_pc;
    wire  if_loop_hit;
    wire  if_loop_taken;


    generate if (LOOP_PREDICT && BRANCH_PREDICT==2) begin : g_loop_predict
        reg [15:0] entry_valid;
        reg [25:0] entry_tag [0:15];
        reg [LOOP_COUNTER_BITS-1:0] iteration [0:15], trip [0:15];
        reg [1:0] confidence [0:15];
        // 缓存更新后状态的预测结果，IF不再串入计数加法及大小比较。
        reg eligible [0:15], pred_taken [0:15];
        wire [3:0] train_index = loop_train_pc[5:2];
        wire replacement = !entry_valid[train_index] || entry_tag[train_index]!=loop_train_pc[31:6];
        wire overflow = &iteration[train_index];
        wire [LOOP_COUNTER_BITS-1:0] next_iteration = iteration[train_index] + 1'b1;
        wire same_trip = !overflow && trip[train_index]==next_iteration;
        // 并行读取两条预测表记录，最后只选择两个预测控制位。
        wire hit0 /* synthesis syn_keep=1 */ = entry_valid[fetch_pc[5:2]] && eligible[fetch_pc[5:2]] &&
                                               entry_tag[fetch_pc[5:2]]==fetch_pc[31:6];
        wire hit1 /* synthesis syn_keep=1 */ = entry_valid[fetch_pc_plus4[5:2]] && eligible[fetch_pc_plus4[5:2]] &&
                                               entry_tag[fetch_pc_plus4[5:2]]==fetch_pc_plus4[31:6];
        wire taken0 /* synthesis syn_keep=1 */ = pred_taken[fetch_pc[5:2]];
        wire taken1 /* synthesis syn_keep=1 */ = pred_taken[fetch_pc_plus4[5:2]];
        assign if_loop_hit = if_bht_select1 ? hit1 : hit0;
        assign if_loop_taken = if_bht_select1 ? taken1 : taken0;
        // EX只有一个训练口，所有表项共用一次计数/比较，不复制16套算术网络。
        // 仅valid需要复位；其余字段在分配时写入，valid=0时不参与预测。
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n)
                entry_valid <= 16'b0;
            else if (loop_train_valid)
                entry_valid[train_index] <= replacement || !(loop_train_taken && overflow);
        end
        always @(posedge clk) begin
            if (rst_n && loop_train_valid) begin
                if (replacement) begin
                    entry_tag[train_index] <= loop_train_pc[31:6];
                    iteration[train_index] <= loop_train_taken ? 1 : 0;
                    trip[train_index] <= loop_train_taken ? 0 : 1;
                    confidence[train_index] <= 2'b00;
                    eligible[train_index] <= 1'b0;
                    pred_taken[train_index] <= 1'b1;
                end else if (loop_train_taken) begin
                    if (overflow) begin
                        confidence[train_index] <= 2'b00;
                        eligible[train_index] <= 1'b0;
                    end else begin
                        iteration[train_index] <= next_iteration;
                        eligible[train_index] <= confidence[train_index]==2'b11 &&
                                                 trip[train_index]!=0 && next_iteration<trip[train_index];
                        pred_taken[train_index] <= ({1'b0,next_iteration} + 1'b1)!={1'b0,trip[train_index]};
                    end
                end else begin
                    iteration[train_index] <= 0;
                    if (same_trip) begin
                        if (confidence[train_index]!=2'b11)
                            confidence[train_index] <= confidence[train_index] + 1'b1;
                    end else begin
                        trip[train_index] <= next_iteration;
                        confidence[train_index] <= 2'b00;
                    end
                    eligible[train_index] <= same_trip && confidence[train_index]>=2'b10 && trip[train_index]!=0;
                    pred_taken[train_index] <= trip[train_index]!=1;
                end
            end
        end
    end else begin : g_no_loop_predict
        assign if_loop_hit = 1'b0;
        assign if_loop_taken = 1'b0;
    end endgenerate
    wire  if_bimodal_taken0 = (BRANCH_PREDICT==1 || !if_bht_hit0)
                             ? id_word0[31] : if_bht_taken0;
    wire  if_bimodal_taken1 = (BRANCH_PREDICT==1 || !SLOT1_DYNAMIC_BHT || !if_bht_hit1)
                             ? id_word1[31] : if_bht_taken1;
    assign if_branch_target = fetch_pc + if_branch_imm0;
    wire  if_loop_override0 = LOOP_PREDICT && BRANCH_PREDICT==2 &&
                             id0_flow_type==2'd1 && id_word0[31] &&
                             if_branch_target[1:0]==2'b00 && if_loop_hit;
    assign if_branch_pred_taken = BRANCH_PREDICT!=0 && id0_flow_type==2'd1 &&
                                if_branch_target[1:0]==2'b00 &&
                                (if_loop_override0 ? if_loop_taken : if_bimodal_taken0);
    assign if_slot1_branch_target = fetch_pc_plus4 + if_branch_imm1;
    wire  if_loop_override1 = LOOP_PREDICT && BRANCH_PREDICT==2 && SLOT1_DYNAMIC_BHT &&
                             id1_flow_type==2'd1 && id_word1[31] &&
                             if_slot1_branch_target[1:0]==2'b00 && if_loop_hit;
    // 参数关闭时保留原静态后向跳预测，便于固定镜像 A/B。
    assign if_slot1_branch_pred_taken = BRANCH_PREDICT!=0 && id1_flow_type==2'd1 &&
                                      if_slot1_branch_target[1:0]==2'b00 &&
                                      (if_loop_override1 ? if_loop_taken : if_bimodal_taken1);
    // 取指数据在本拍可用；JAL 无需寄存器操作数，可直接由 J 型立即数得到下一拍 PC。
    // 未对齐目标不提前处理，保留原 EX 路径产生精确停机原因。
    assign if_jal_target = fetch_pc + if_jal_imm0;
    assign if_jal_candidate = IF_EARLY_JAL && fetch_started &&
                            id_word0[6:0]==7'b1101111 &&
                            if_jal_target[1:0]==2'b00;
    assign if_slot1_jal_target = fetch_pc_plus4 + if_jal_imm1;
    assign if_slot1_jal_candidate = IF_EARLY_JAL && fetch_started &&
                                  id_word1[6:0]==7'b1101111 &&
                                  if_slot1_jal_target[1:0]==2'b00;

    // 标准返回使用RAS，其余JALR使用完整PC tag的4项上次目标表。
    assign if_jalr_return = id0_rd==5'd0 && (id0_rs1==5'd1 || id0_rs1==5'd5) &&
                          id_word0[31:20]==12'b0;
    wire  if_ras_valid;
    wire  if_btb_valid;






    generate if (JALR_BTB) begin : g_jalr_btb
        reg [3:0] valid;
        reg [27:0] tag [0:3];
        reg [29:0] target [0:3];
        wire [1:0] train_index = btb_train_pc[3:2];
        assign if_btb_valid = valid[fetch_pc[3:2]] && tag[fetch_pc[3:2]]==fetch_pc[31:4];
        assign if_btb_target = {target[fetch_pc[3:2]],2'b00};
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) valid <= 4'b0;
            else if (btb_train_valid) valid[train_index] <= 1'b1;
        end
        always @(posedge clk) begin
            if (rst_n && btb_train_valid) begin
                tag[train_index] <= btb_train_pc[31:4];
                target[train_index] <= btb_train_target[31:2];
            end
        end
    end else begin : g_no_jalr_btb
        assign if_btb_valid = 1'b0;
        assign if_btb_target = 32'b0;
    end endgenerate
    generate if (RAS_PREDICT) begin : g_ras
        reg [4:0] depth;
        reg [31:0] top;
        reg [31:0] stack [0:15];
        wire [3:0] replaced_top_index = depth[3:0] - 4'd1;
        wire [3:0] popped_top_index = depth[3:0] - 4'd2;
        assign if_ras_valid = depth!=0 && top[1:0]==2'b00;
        assign if_ras_target = top;
        // EX解析后更新，无IF投机修改；同拍IF读取注册的旧栈顶，失配由EX恢复。
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n) depth <= 5'd0;
            else if (ras_update_valid) begin
                if (ras_pop && ras_push)
                    depth <= depth==0 ? 5'd1 : depth;
                else if (ras_push)
                    depth <= depth==16 ? 5'd0 : depth+5'd1;
                else if (ras_pop && depth!=0)
                    depth <= depth-5'd1;
            end
        end
        always @(posedge clk) begin
            if (rst_n && ras_update_valid) begin
                if (ras_push) begin
                    if (ras_pop) begin
                        stack[depth==0 ? 4'd0 : replaced_top_index] <= ras_push_pc;
                        top <= ras_push_pc;
                    end else if (depth<16) begin
                        stack[depth[3:0]] <= ras_push_pc;
                        top <= ras_push_pc;
                    end
                end else if (ras_pop && depth>1)
                    top <= stack[popped_top_index];
            end
        end
    end else begin : g_no_ras
        assign if_ras_valid = 1'b0;
        assign if_ras_target = 32'b0;
    end endgenerate
    assign if_jalr_pred_target = if_jalr_return ? if_ras_target : if_btb_target;
    // JALR只有opcode=67/funct3=000合法，不经通用控制译码选择预测PC。
    wire  if_jalr_legal = id_word0[6:0]==7'h67 && id_word0[14:12]==3'b000;
    assign if_jalr_pred_valid = fetch_started && if_jalr_legal &&
                              (if_jalr_return ? if_ras_valid : if_btb_valid) &&
                              if_jalr_pred_target[1:0]==2'b00;
    // 调用/返回提示随已接纳指令保存；EX只处理实际年龄/故障，不重复译码整段instr。
    wire  id0_rd_link = id0_rd==5'd1 || id0_rd==5'd5;
    wire  id0_rs1_link = id0_rs1==5'd1 || id0_rs1==5'd5;
    assign id0_ras_push_hint = (id_word0[6:0]==7'h6f || if_jalr_legal) && id0_rd_link;
    assign id0_ras_pop_hint = if_jalr_legal && id0_rs1_link && (!id0_rd_link || id0_rd!=id0_rs1);
    assign id1_ras_push_hint = id_word1[6:0]==7'h6f && (id1_rd==5'd1 || id1_rd==5'd5);
    // 只放开标准ret，复用同一个RAS读口；冷/空栈保留下一拍slot0重放。
    assign id1_return = id_word1[6:0]==7'h67 && id_word1[14:12]==3'b000 &&
                      id1_rd==5'd0 && (id1_rs1==5'd1 || id1_rs1==5'd5) &&
                      id_word1[31:20]==12'b0;
    assign id1_ret_candidate = !LOCAL_MEMORY_LOOKAHEAD && SLOT1_RET_PREDICT && RAS_PREDICT && fetch_started &&
                             id1_return && if_ras_valid;
    wire  id1_ret_independent = !(id0_reg_write && id0_rd!=5'd0 && id0_rd==id1_rs1);

    assign id0_is_control = id0_issue_class[6];
    assign id0_is_mul = id0_issue_class[5];
    assign id0_eligible = id0_issue_class[0];
    assign id1_simple_alu = id1_issue_class[1];
    assign id1_simple_load = id1_issue_class[2];
    assign id1_simple_store = id1_issue_class[3];
    assign id1_simple_branch = id1_issue_class[4];
    assign id1_simple_mul = id1_issue_class[5];
    assign id1_eligible = (SLOT1_MUL_PAIR && id1_simple_mul) || id1_simple_alu ||
                        id1_simple_load ||
                        id1_simple_store ||
                        id1_simple_branch ||
                        (!id1_illegal && if_slot1_jal_candidate) ||
                        (id1_ret_candidate && id1_ret_independent);
    assign id2_simple_alu = id2_issue_class[1];


    // BHT 仅复位有效位；计数器在首次训练时初始化。
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (bht_reset_idx=0; bht_reset_idx<BHT_ENTRIES; bht_reset_idx=bht_reset_idx+1)
                bht_valid[bht_reset_idx] <= 1'b0;
        end else begin
            if (bht_train_valid) begin
                bht_valid[bht_train_index] <= 1'b1;
                if (!bht_valid[bht_train_index])
                    bht_counter[bht_train_index] <= bht_train_taken ? 2'b10 : 2'b01;
                else if (bht_train_taken && bht_counter[bht_train_index]!=2'b11)
                    bht_counter[bht_train_index] <= bht_counter[bht_train_index] + 2'd1;
                else if (!bht_train_taken && bht_counter[bht_train_index]!=2'b00)
                    bht_counter[bht_train_index] <= bht_counter[bht_train_index] - 2'd1;
            end
        end
    end

endmodule
`default_nettype wire
