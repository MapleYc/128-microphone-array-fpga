`default_nettype none
// 跨组最新生产者选择、操作数前递及等待判断。
// 保持原寄存边界与年龄优先级；组合输出由父模块统一接纳和提交。
module rv32_tri_operands #(
    parameter EX2_FORWARD_FROM_WB_ONLY = 1'b1,
    parameter LOAD_ADDR_BYPASS = 1'b0,
    parameter LOAD_MUL_BYPASS = 1'b1,
    parameter LOAD_USE_BYPASS = 1'b1,
    parameter LOCAL_LOAD0_READY_COFACTOR = 1'b1,
    parameter LOCAL_LOAD1_FROM_WB_ONLY = 1'b1,
    parameter LOCAL_MEMORY_LOOKAHEAD = 1'b0,
    parameter LOCAL_MEMORY_RESPONSE_CLASS = 1'b0,
    parameter LOCAL_SHIFT_FROM_WB_ONLY = 1'b1,
    parameter LOCAL_SMALL_SHIFT_FORWARD = 1'b1,
    parameter STORE_DATA_BYPASS = 1'b0,
    parameter THIRD_ALU_SLOT = 1'b0
) (
    input wire [1:0] dmem_local_ram_i,
    input wire  ex0_fault,
    input wire  ex0_is_mul,
    input wire  ex0_load_ready_fault,
    input wire [31:0] ex0_mem_addr,
    input wire  ex0_mem_read,
    input wire [31:0] ex0_pc,
    input wire [4:0] ex0_rd,
    input wire  ex0_reg_write,
    input wire [31:0] ex0_result,
    input wire  ex0_valid,
    input wire [1:0] ex0_wb_sel,
    input wire [1:0] ex1_alu_a_sel,
    input wire  ex1_alu_b_sel,
    input wire [3:0] ex1_alu_op,
    input wire  ex1_fwd_slot0_rs1,
    input wire [31:0] ex1_imm,
    input wire  ex1_is_mul,
    input wire [31:0] ex1_mem_addr,
    input wire  ex1_mem_read,
    input wire [31:0] ex1_no_shift_result,
    input wire [31:0] ex1_pc,
    input wire  ex1_ram_pair_replay,
    input wire [4:0] ex1_rd,
    input wire  ex1_reg_write,
    input wire [31:0] ex1_result,
    input wire [31:0] ex1_rs1_data,
    input wire  ex1_valid,
    input wire [1:0] ex1_wb_sel,
    input wire [4:0] ex2_rd,
    input wire  ex2_reg_write,
    input wire [31:0] ex2_result,
    input wire  ex2_valid,
    input wire  ex_double_load,
    input wire  fast_mul_low,
    input wire  id0_eligible,
    input wire  id0_is_mul,
    input wire  id0_mem_read,
    input wire  id0_mem_write,
    input wire [4:0] id0_rs1,
    input wire [31:0] id0_rs1_data,
    input wire [4:0] id0_rs2,
    input wire [31:0] id0_rs2_data,
    input wire  id0_use_rs1,
    input wire  id0_use_rs2,
    input wire  id1_eligible,
    input wire  id1_mem_read,
    input wire  id1_mem_write,
    input wire [4:0] id1_rs1,
    input wire [31:0] id1_rs1_data,
    input wire [4:0] id1_rs2,
    input wire [31:0] id1_rs2_data,
    input wire  id1_simple_mul,
    input wire  id1_simple_store,
    input wire  id1_use_rs1,
    input wire  id1_use_rs2,
    input wire [4:0] id2_rs1,
    input wire [31:0] id2_rs1_data,
    input wire [4:0] id2_rs2,
    input wire [31:0] id2_rs2_data,
    input wire  id2_use_rs1,
    input wire  id2_use_rs2,
    input wire [31:0] load0_capture_data,
    input wire  lsu1_misaligned,
    input wire [31:0] lsu_load_data,
    input wire  lsu_misaligned,
    input wire [31:0] mul_forward_result,
    input wire [31:0] slot1_load_data,
    output wire [31:0] ex0_forward_data,
    output  wire ex0_forwardable,
    output  wire ex0_load_ready,
    output  wire ex0_writes,
    output wire [31:0] ex1_forward_data,
    output  wire ex1_forwardable,
    output  wire ex1_load_ready,
    output  wire ex1_shift_wb,
    output  wire ex1_writes,
    output wire [31:0] ex2_id_forward_data,
    output  wire ex2_writes,
    output wire [31:0] id0_rs1_capture_data,
    output wire [31:0] id0_rs2_capture_data,
    output  wire id0_wait_ex,
    output wire [31:0] id1_rs1_capture_data,
    output wire [31:0] id1_rs2_capture_data,
    output  wire id1_wait_ex,
    output wire [31:0] id2_rs1_capture_data,
    output wire [31:0] id2_rs2_capture_data,
    output  wire id2_wait_ex
);




    // 普通 RAM 在负沿提供 Load 数据，可在下一正沿进入非访存消费者的 EX 操作数。
    // 新通路只接操作数寄存器，下面用于 ID 地址/配对的 issue_data 保持原非 Load 前递。
    assign ex0_writes = ex0_valid && ex0_reg_write && ex0_rd!=5'd0;
    // 错误路径年轻 ALU 不进入 WB；误预测时 can_issue=0，下一组不会捕获它。
    // 这里保留普通 EX1 就绪条件，避免分支比较结果串入 ID 地址预计算。
    assign ex1_writes = ex1_valid && ex1_reg_write && ex1_rd!=5'd0;
    assign ex2_writes = THIRD_ALU_SLOT && ex2_valid && ex2_reg_write && ex2_rd!=5'd0;
    // 等待时仍由最新生产者遮挡旧值，但不连接其组合结果。
    // WB2 捕获真实结果，后续 ID 通过正常寄存器堆与 WB 路径取得数据。
    assign ex2_id_forward_data = EX2_FORWARD_FROM_WB_ONLY ? 32'b0 : ex2_result;
    assign ex0_forwardable = ex0_writes && !ex0_mem_read && !(fast_mul_low && ex0_is_mul);
    localparam  LOCAL_SHIFT_WB = LOCAL_MEMORY_LOOKAHEAD && LOCAL_SHIFT_FROM_WB_ONLY;
    // 小移位仅使用该槽已寄存操作数的固定连线，避免重接 ALU0/MUL0 到移位器再到下一组 ID 的组合链。
    wire  ex1_small_shift = LOCAL_SHIFT_WB && LOCAL_SMALL_SHIFT_FORWARD &&
        ex1_alu_a_sel==2'd0 && ex1_alu_b_sel && !ex1_fwd_slot0_rs1 &&
        (ex1_imm[4:0]==5'd1 || ex1_imm[4:0]==5'd2) &&
        (ex1_alu_op==4'd2 || ex1_alu_op==4'd6 || ex1_alu_op==4'd7);
    wire [31:0] ex1_small_shift1 = ex1_alu_op==4'd2 ? {ex1_rs1_data[30:0],1'b0} :
        { (ex1_alu_op==4'd7 && ex1_rs1_data[31]),ex1_rs1_data[31:1]};
    wire [31:0] ex1_small_shift2 = ex1_alu_op==4'd2 ? {ex1_rs1_data[29:0],2'b0} :
        {{2{ex1_alu_op==4'd7 && ex1_rs1_data[31]}},ex1_rs1_data[31:2]};
    wire [31:0] ex1_small_shift_data = ex1_imm[1] ? ex1_small_shift2 : ex1_small_shift1;
    assign ex1_shift_wb = LOCAL_SHIFT_WB && !ex1_small_shift && ex1_writes && !ex1_mem_read &&
        !ex1_is_mul && ex1_wb_sel==2'd0 &&
        (ex1_alu_op==4'd2 || ex1_alu_op==4'd6 || ex1_alu_op==4'd7);
    assign ex1_forwardable = ex1_writes && !ex1_mem_read && !(fast_mul_low && ex1_is_mul) && !ex1_shift_wb;
    // JAL 提前后，目标处第一条指令可能紧接着读 link 寄存器。
    // WB_LINK 的真实写回值是 PC+4，不能前递 ALU 计算的跳转目标。
    assign ex0_forward_data = ex0_wb_sel==2'd2 ? ex0_pc + 32'd4 :
                                       ex0_is_mul ? mul_forward_result : ex0_result;
    assign ex1_forward_data = ex1_wb_sel==2'd2 ? ex1_pc + 32'd4 : ex1_is_mul ? mul_forward_result :
        LOCAL_SHIFT_WB ? (ex1_small_shift ? ex1_small_shift_data : ex1_no_shift_result) : ex1_result;
    // 本地 SoC 在同步 RAM 采样地址时保存对应范围属性；通用握手配置仍检查完整有效地址。
    wire  ex0_load_in_local_ram = LOCAL_MEMORY_LOOKAHEAD && LOCAL_MEMORY_RESPONSE_CLASS
        ? dmem_local_ram_i[0] : ex0_mem_addr[31:16]==16'h1000;
    wire  ex1_load_in_local_ram = LOCAL_MEMORY_LOOKAHEAD && LOCAL_MEMORY_RESPONSE_CLASS
        ? (ex_double_load ? dmem_local_ram_i[1] : dmem_local_ram_i[0])
        : ex1_mem_addr[31:16]==16'h1000;
    // 有效 Load0 的 flow_type 为零，就绪条件不依赖互斥的分支目标比较器。
    // 真实 LSU/总线故障仍禁止前递，异常与年轻副作用取消规则不变。
    wire  ex0_load_ready_original = LOAD_USE_BYPASS && ex0_writes && ex0_mem_read &&
                          ex0_load_in_local_ram && !ex0_fault;
    assign ex0_load_ready = LOAD_USE_BYPASS && ex0_writes && ex0_mem_read &&
                          ex0_load_in_local_ram &&
                          !(LOCAL_MEMORY_LOOKAHEAD && LOCAL_LOAD0_READY_COFACTOR
                            ? ex0_load_ready_fault : ex0_fault);
    // 仅槽 1 的跨组 Load 数据等待既有 WB 边界；普通和提前发射入口共用该规则，槽 0 保持原行为。
    assign ex1_load_ready = !(LOCAL_MEMORY_LOOKAHEAD && LOCAL_LOAD1_FROM_WB_ONLY) &&
                          LOAD_USE_BYPASS && ex1_writes && ex1_mem_read &&
                          ex1_load_in_local_ram && !ex0_fault && !ex1_ram_pair_replay &&
                          !(ex_double_load ? lsu1_misaligned : lsu_misaligned);
    // LOAD_MUL_BYPASS 只放宽已有捕获寄存器输入处的乘法消费者许可。
    // 仍只使用普通 RAM 的无故障 Load，MMIO/越界返回值不会提前消费。
    wire  id0_load_bypass_consumer = id0_eligible && !id0_mem_read && !id0_mem_write &&
                                   (!id0_is_mul || LOAD_MUL_BYPASS);
    wire  id1_load_bypass_consumer = id1_eligible && !id1_mem_read && !id1_mem_write &&
                                   (!id1_simple_mul || LOAD_MUL_BYPASS);
    wire  id0_store_data_bypass = STORE_DATA_BYPASS && id0_eligible && id0_mem_write && !id0_mem_read;
    wire  id1_store_data_bypass = STORE_DATA_BYPASS && id1_simple_store;
    // 访存地址也可在正沿捕获上一拍 RAM Load；返回值只到 EX 寄存器，
    // 不接 ID 双 Load 地址预计算。下方显式禁止这类地址消费者双 Load 配对。
    wire  id0_load_addr_bypass = LOAD_ADDR_BYPASS && id0_eligible && (id0_mem_read || id0_mem_write);
    wire  id1_load_addr_bypass = LOAD_ADDR_BYPASS && id1_eligible && (id1_mem_read || id1_mem_write);
    // 提前计算本地候选操作数；实际消费仍受源使用标志、最新生产者就绪和发射许可控制。
    // 全部相关旁路开启时，数据选择不必重复判断指令类别，但发射资格不能省略。
    wire  id0_rs1_bypass_consumer = (LOCAL_MEMORY_LOOKAHEAD && LOAD_ADDR_BYPASS && LOAD_MUL_BYPASS)
        ? 1'b1 : (id0_load_bypass_consumer || id0_load_addr_bypass);
    wire  id1_rs1_bypass_consumer = (LOCAL_MEMORY_LOOKAHEAD && LOAD_ADDR_BYPASS && LOAD_MUL_BYPASS)
        ? 1'b1 : (id1_load_bypass_consumer || id1_load_addr_bypass);
    wire  id0_rs2_bypass_consumer = (LOCAL_MEMORY_LOOKAHEAD && STORE_DATA_BYPASS && LOAD_MUL_BYPASS)
        ? 1'b1 : (id0_load_bypass_consumer || id0_store_data_bypass);
    wire  id1_rs2_bypass_consumer = (LOCAL_MEMORY_LOOKAHEAD && STORE_DATA_BYPASS && LOAD_MUL_BYPASS)
        ? 1'b1 : (id1_load_bypass_consumer || id1_store_data_bypass);
    wire  id0_block_load0 = ex0_writes && ex0_mem_read && !(id0_load_bypass_consumer && ex0_load_ready);
    wire  id0_block_load1 = ex1_writes && ex1_mem_read && !(id0_load_bypass_consumer && ex1_load_ready);
    wire  id1_block_load0 = ex0_writes && ex0_mem_read && !(id1_load_bypass_consumer && ex0_load_ready);
    wire  id1_block_load1 = ex1_writes && ex1_mem_read && !(id1_load_bypass_consumer && ex1_load_ready);
    wire  id0_rs1_block_load0 = ex0_writes && ex0_mem_read && !(id0_rs1_bypass_consumer && ex0_load_ready);
    wire  id0_rs1_block_load1 = ex1_writes && ex1_mem_read && !(id0_rs1_bypass_consumer && ex1_load_ready);
    wire  id1_rs1_block_load0 = ex0_writes && ex0_mem_read && !(id1_rs1_bypass_consumer && ex0_load_ready);
    wire  id1_rs1_block_load1 = ex1_writes && ex1_mem_read && !(id1_rs1_bypass_consumer && ex1_load_ready);
    wire  id0_rs2_block_load0 = ex0_writes && ex0_mem_read && !(id0_rs2_bypass_consumer && ex0_load_ready);
    wire  id0_rs2_block_load1 = ex1_writes && ex1_mem_read && !(id0_rs2_bypass_consumer && ex1_load_ready);
    wire  id1_rs2_block_load0 = ex0_writes && ex0_mem_read && !(id1_rs2_bypass_consumer && ex0_load_ready);
    wire  id1_rs2_block_load1 = ex1_writes && ex1_mem_read && !(id1_rs2_bypass_consumer && ex1_load_ready);
    wire  id0_load_wait = (id0_use_rs1 && !(ex2_writes && id0_rs1==ex2_rd) &&
                                         ((id0_rs1_block_load0 && id0_rs1==ex0_rd) ||
                                          (id0_rs1_block_load1 && id0_rs1==ex1_rd))) ||
                         (id0_use_rs2 && !(ex2_writes && id0_rs2==ex2_rd) &&
                                         ((id0_rs2_block_load0 && id0_rs2==ex0_rd) ||
                                          (id0_rs2_block_load1 && id0_rs2==ex1_rd)));
    wire  id1_load_wait = (id1_use_rs1 && !(ex2_writes && id1_rs1==ex2_rd) &&
                                         ((id1_rs1_block_load0 && id1_rs1==ex0_rd) ||
                                          (id1_rs1_block_load1 && id1_rs1==ex1_rd))) ||
                         (id1_use_rs2 && !(ex2_writes && id1_rs2==ex2_rd) &&
                                         ((id1_rs2_block_load0 && id1_rs2==ex0_rd) ||
                                          (id1_rs2_block_load1 && id1_rs2==ex1_rd)));
    wire  id0_ex2_wait = EX2_FORWARD_FROM_WB_ONLY && ex2_writes &&
        ((id0_use_rs1 && id0_rs1==ex2_rd) || (id0_use_rs2 && id0_rs2==ex2_rd));
    wire  id1_ex2_wait = EX2_FORWARD_FROM_WB_ONLY && ex2_writes &&
        ((id1_use_rs1 && id1_rs1==ex2_rd) || (id1_use_rs2 && id1_rs2==ex2_rd));
    // 函数所有变化输入均显式传参，避免隐式全局依赖。
    // 年轻写入者即使未就绪也遮挡较老快速 MUL；所有发射入口使用同一年龄优先规则。
    function automatic shift_source_wait;
        input reg use_source, match2, match1, shift1;
        begin shift_source_wait=use_source && !match2 && match1 && shift1; end
    endfunction
    wire  id0_shift_wait = shift_source_wait(id0_use_rs1,ex2_writes && id0_rs1==ex2_rd,
            ex1_writes && id0_rs1==ex1_rd,ex1_shift_wb) ||
        shift_source_wait(id0_use_rs2,ex2_writes && id0_rs2==ex2_rd,
            ex1_writes && id0_rs2==ex1_rd,ex1_shift_wb);
    wire  id1_shift_wait = shift_source_wait(id1_use_rs1,ex2_writes && id1_rs1==ex2_rd,
            ex1_writes && id1_rs1==ex1_rd,ex1_shift_wb) ||
        shift_source_wait(id1_use_rs2,ex2_writes && id1_rs2==ex2_rd,
            ex1_writes && id1_rs2==ex1_rd,ex1_shift_wb);
    function automatic fast_mul_source_wait;
        input reg use_source,match2,match1,match0,fast1,fast0;
        begin
            fast_mul_source_wait=use_source && !match2 &&
                (match1 ? fast1 : (match0 && fast0));
        end
    endfunction
    wire  id0_fast_mul_wait = fast_mul_source_wait(id0_use_rs1,ex2_writes && id0_rs1==ex2_rd,
            ex1_writes && id0_rs1==ex1_rd,ex0_writes && id0_rs1==ex0_rd,
            fast_mul_low && ex1_is_mul,fast_mul_low && ex0_is_mul) ||
        fast_mul_source_wait(id0_use_rs2,ex2_writes && id0_rs2==ex2_rd,
            ex1_writes && id0_rs2==ex1_rd,ex0_writes && id0_rs2==ex0_rd,
            fast_mul_low && ex1_is_mul,fast_mul_low && ex0_is_mul);
    wire  id1_fast_mul_wait = fast_mul_source_wait(id1_use_rs1,ex2_writes && id1_rs1==ex2_rd,
            ex1_writes && id1_rs1==ex1_rd,ex0_writes && id1_rs1==ex0_rd,
            fast_mul_low && ex1_is_mul,fast_mul_low && ex0_is_mul) ||
        fast_mul_source_wait(id1_use_rs2,ex2_writes && id1_rs2==ex2_rd,
            ex1_writes && id1_rs2==ex1_rd,ex0_writes && id1_rs2==ex0_rd,
            fast_mul_low && ex1_is_mul,fast_mul_low && ex0_is_mul);
    // 提前计算生产者阻塞，晚到的源使用标志只选择最后两个条件。
    wire [3:0] source_wait_early;
    rv32_operand_wait_leaf u_wait_01(
        .match0(ex0_writes && id0_rs1==ex0_rd),
        .match1(ex1_writes && id0_rs1==ex1_rd),
        .match2(ex2_writes && id0_rs1==ex2_rd),
        .block_load0(id0_rs1_block_load0),
        .block_load1(id0_rs1_block_load1),
        .fast0(fast_mul_low && ex0_is_mul),.fast1(fast_mul_low && ex1_is_mul),
        .wait_ex2(EX2_FORWARD_FROM_WB_ONLY!=0),.wait_o(source_wait_early[0]));
    rv32_operand_wait_leaf u_wait_02(
        .match0(ex0_writes && id0_rs2==ex0_rd),
        .match1(ex1_writes && id0_rs2==ex1_rd),
        .match2(ex2_writes && id0_rs2==ex2_rd),
        .block_load0(id0_rs2_block_load0),
        .block_load1(id0_rs2_block_load1),
        .fast0(fast_mul_low && ex0_is_mul),.fast1(fast_mul_low && ex1_is_mul),
        .wait_ex2(EX2_FORWARD_FROM_WB_ONLY!=0),.wait_o(source_wait_early[1]));
    rv32_operand_wait_leaf u_wait_11(
        .match0(ex0_writes && id1_rs1==ex0_rd),
        .match1(ex1_writes && id1_rs1==ex1_rd),
        .match2(ex2_writes && id1_rs1==ex2_rd),
        .block_load0(id1_rs1_block_load0),
        .block_load1(id1_rs1_block_load1),
        .fast0(fast_mul_low && ex0_is_mul),.fast1(fast_mul_low && ex1_is_mul),
        .wait_ex2(EX2_FORWARD_FROM_WB_ONLY!=0),.wait_o(source_wait_early[2]));
    rv32_operand_wait_leaf u_wait_12(
        .match0(ex0_writes && id1_rs2==ex0_rd),
        .match1(ex1_writes && id1_rs2==ex1_rd),
        .match2(ex2_writes && id1_rs2==ex2_rd),
        .block_load0(id1_rs2_block_load0),
        .block_load1(id1_rs2_block_load1),
        .fast0(fast_mul_low && ex0_is_mul),.fast1(fast_mul_low && ex1_is_mul),
        .wait_ex2(EX2_FORWARD_FROM_WB_ONLY!=0),.wait_o(source_wait_early[3]));
    localparam  SOURCE_WAIT_COFACTOR = LOCAL_MEMORY_LOOKAHEAD && LOAD_ADDR_BYPASS && STORE_DATA_BYPASS && LOAD_MUL_BYPASS;
    assign id0_wait_ex = id0_shift_wait || (SOURCE_WAIT_COFACTOR
        ? ((id0_use_rs1 && source_wait_early[0]) || (id0_use_rs2 && source_wait_early[1]))
        : (id0_load_wait || id0_ex2_wait || id0_fast_mul_wait));
    assign id1_wait_ex = id1_shift_wait || (SOURCE_WAIT_COFACTOR
        ? ((id1_use_rs1 && source_wait_early[2]) || (id1_use_rs2 && source_wait_early[3]))
        : (id1_load_wait || id1_ex2_wait || id1_fast_mul_wait));
    // 前两槽同写 rd 时，EX1 优先于 EX0；启用第三槽时还需优先检查更年轻的 EX2。
    wire [31:0] id0_rs1_issue_data = (id0_use_rs1 && ex2_writes && id0_rs1==ex2_rd) ? ex2_id_forward_data :
        (id0_use_rs1 && ex1_forwardable && id0_rs1==ex1_rd) ? ex1_forward_data :
        (id0_use_rs1 && ex0_forwardable && id0_rs1==ex0_rd) ? ex0_forward_data : id0_rs1_data;
    wire [31:0] id0_rs2_issue_data = (id0_use_rs2 && ex2_writes && id0_rs2==ex2_rd) ? ex2_id_forward_data :
        (id0_use_rs2 && ex1_forwardable && id0_rs2==ex1_rd) ? ex1_forward_data :
        (id0_use_rs2 && ex0_forwardable && id0_rs2==ex0_rd) ? ex0_forward_data : id0_rs2_data;
    wire [31:0] id1_rs1_issue_data = (id1_use_rs1 && ex2_writes && id1_rs1==ex2_rd) ? ex2_id_forward_data :
        (id1_use_rs1 && ex1_forwardable && id1_rs1==ex1_rd) ? ex1_forward_data :
        (id1_use_rs1 && ex0_forwardable && id1_rs1==ex0_rd) ? ex0_forward_data : id1_rs1_data;
    wire [31:0] id1_rs2_issue_data = (id1_use_rs2 && ex2_writes && id1_rs2==ex2_rd) ? ex2_id_forward_data :
        (id1_use_rs2 && ex1_forwardable && id1_rs2==ex1_rd) ? ex1_forward_data :
        (id1_use_rs2 && ex0_forwardable && id1_rs2==ex0_rd) ? ex0_forward_data : id1_rs2_data;

    // 同一源寄存器按 EX2、EX1、EX0 的年龄顺序选择；只有寄存器 D 输入使用 RAM Load 返回值。
    generate if(LOCAL_MEMORY_LOOKAHEAD) begin : g_local_capture_views
        // 此处仅选择数据；发射、就绪和源使用检查保持独立，无人消费的候选数据被忽略。
        rv32_local_capture_view u_01(
            .match_priority({ex2_writes && id0_rs1==ex2_rd,
                ex1_forwardable && id0_rs1==ex1_rd,
                ex1_load_ready && id0_rs1==ex1_rd,
                ex0_load_ready && id0_rs1==ex0_rd,
                ex0_forwardable && id0_rs1==ex0_rd}),
            .ex2(ex2_id_forward_data),.ex1(ex1_forward_data),
            .load1(slot1_load_data),.load0(load0_capture_data),
            .ex0(ex0_forward_data),.rf(id0_rs1_data),.value(id0_rs1_capture_data));
        rv32_local_capture_view u_02(
            .match_priority({ex2_writes && id0_rs2==ex2_rd,
                ex1_forwardable && id0_rs2==ex1_rd,
                ex1_load_ready && id0_rs2==ex1_rd,
                ex0_load_ready && id0_rs2==ex0_rd,
                ex0_forwardable && id0_rs2==ex0_rd}),
            .ex2(ex2_id_forward_data),.ex1(ex1_forward_data),
            .load1(slot1_load_data),.load0(load0_capture_data),
            .ex0(ex0_forward_data),.rf(id0_rs2_data),.value(id0_rs2_capture_data));
        rv32_local_capture_view u_11(
            .match_priority({ex2_writes && id1_rs1==ex2_rd,
                ex1_forwardable && id1_rs1==ex1_rd,
                ex1_load_ready && id1_rs1==ex1_rd,
                ex0_load_ready && id1_rs1==ex0_rd,
                ex0_forwardable && id1_rs1==ex0_rd}),
            .ex2(ex2_id_forward_data),.ex1(ex1_forward_data),
            .load1(slot1_load_data),.load0(load0_capture_data),
            .ex0(ex0_forward_data),.rf(id1_rs1_data),.value(id1_rs1_capture_data));
        rv32_local_capture_view u_12(
            .match_priority({ex2_writes && id1_rs2==ex2_rd,
                ex1_forwardable && id1_rs2==ex1_rd,
                ex1_load_ready && id1_rs2==ex1_rd,
                ex0_load_ready && id1_rs2==ex0_rd,
                ex0_forwardable && id1_rs2==ex0_rd}),
            .ex2(ex2_id_forward_data),.ex1(ex1_forward_data),
            .load1(slot1_load_data),.load0(load0_capture_data),
            .ex0(ex0_forward_data),.rf(id1_rs2_data),.value(id1_rs2_capture_data));
    end else begin : g_original_capture_views
    assign id0_rs1_capture_data =
        (id0_use_rs1 && ex2_writes && id0_rs1==ex2_rd) ? ex2_id_forward_data : LOAD_USE_BYPASS ?
        ((id0_use_rs1 && ex1_forwardable && id0_rs1==ex1_rd) ? ex1_forward_data :
         (id0_rs1_bypass_consumer && id0_use_rs1 && ex1_load_ready && id0_rs1==ex1_rd) ? slot1_load_data :
         (id0_rs1_bypass_consumer && id0_use_rs1 && ex0_load_ready && id0_rs1==ex0_rd) ? lsu_load_data : id0_rs1_issue_data)
        : id0_rs1_issue_data;
    assign id0_rs2_capture_data =
        (id0_use_rs2 && ex2_writes && id0_rs2==ex2_rd) ? ex2_id_forward_data : LOAD_USE_BYPASS ?
        ((id0_use_rs2 && ex1_forwardable && id0_rs2==ex1_rd) ? ex1_forward_data :
         (id0_rs2_bypass_consumer && id0_use_rs2 && ex1_load_ready && id0_rs2==ex1_rd) ? slot1_load_data :
         (id0_rs2_bypass_consumer && id0_use_rs2 && ex0_load_ready && id0_rs2==ex0_rd) ? lsu_load_data : id0_rs2_issue_data)
        : id0_rs2_issue_data;
    assign id1_rs1_capture_data =
        (id1_use_rs1 && ex2_writes && id1_rs1==ex2_rd) ? ex2_id_forward_data : LOAD_USE_BYPASS ?
        ((id1_use_rs1 && ex1_forwardable && id1_rs1==ex1_rd) ? ex1_forward_data :
         (id1_rs1_bypass_consumer && id1_use_rs1 && ex1_load_ready && id1_rs1==ex1_rd) ? slot1_load_data :
         (id1_rs1_bypass_consumer && id1_use_rs1 && ex0_load_ready && id1_rs1==ex0_rd) ? lsu_load_data : id1_rs1_issue_data)
        : id1_rs1_issue_data;
    assign id1_rs2_capture_data =
        (id1_use_rs2 && ex2_writes && id1_rs2==ex2_rd) ? ex2_id_forward_data : LOAD_USE_BYPASS ?
        ((id1_use_rs2 && ex1_forwardable && id1_rs2==ex1_rd) ? ex1_forward_data :
         (id1_rs2_bypass_consumer && id1_use_rs2 && ex1_load_ready && id1_rs2==ex1_rd) ? slot1_load_data :
         (id1_rs2_bypass_consumer && id1_use_rs2 && ex0_load_ready && id1_rs2==ex0_rd) ? lsu_load_data : id1_rs2_issue_data)
        : id1_rs2_issue_data;
    end endgenerate

    // 第三槽跨组读取必须先选最新生产者，再判断它的就绪状态。
    // EX2 不执行 Load；年轻 EX2 仍遮挡较老生产者，等待自身 WB，不回退使用旧值。
    // 显式组合网避免函数隐式全局输入带来的模拟敏感性差异。
    wire  id2_rs1_match2 = id2_use_rs1 && ex2_writes && id2_rs1==ex2_rd;
    wire  id2_rs2_match2 = id2_use_rs2 && ex2_writes && id2_rs2==ex2_rd;
    wire  id2_rs1_match1 = id2_use_rs1 && ex1_writes && id2_rs1==ex1_rd && !id2_rs1_match2;
    wire  id2_rs2_match1 = id2_use_rs2 && ex1_writes && id2_rs2==ex1_rd && !id2_rs2_match2;
    wire  id2_rs1_match0 = id2_use_rs1 && ex0_writes && id2_rs1==ex0_rd &&
                          !id2_rs1_match2 && !id2_rs1_match1;
    wire  id2_rs2_match0 = id2_use_rs2 && ex0_writes && id2_rs2==ex0_rd &&
                          !id2_rs2_match2 && !id2_rs2_match1;
    wire  id2_ex2_wait = EX2_FORWARD_FROM_WB_ONLY && (id2_rs1_match2 || id2_rs2_match2);
    wire  id2_fast_mul_wait = fast_mul_low &&
        (((id2_rs1_match1 || id2_rs2_match1) && ex1_is_mul) ||
         ((id2_rs1_match0 || id2_rs2_match0) && ex0_is_mul));
    wire  id2_shift_wait = shift_source_wait(id2_use_rs1,id2_rs1_match2,id2_rs1_match1,ex1_shift_wb) ||
        shift_source_wait(id2_use_rs2,id2_rs2_match2,id2_rs2_match1,ex1_shift_wb);
    assign id2_wait_ex = id2_shift_wait || id2_ex2_wait || id2_fast_mul_wait ||
                       ((id2_rs1_match1 || id2_rs2_match1) && ex1_mem_read && !ex1_load_ready) ||
                       ((id2_rs1_match0 || id2_rs2_match0) && ex0_mem_read && !ex0_load_ready);
    assign id2_rs1_capture_data = id2_rs1_match2 ? ex2_id_forward_data :
        id2_rs1_match1 ? (ex1_mem_read ? (ex1_load_ready ? slot1_load_data : id2_rs1_data) : ex1_forward_data) :
        id2_rs1_match0 ? (ex0_mem_read ? (ex0_load_ready ? lsu_load_data : id2_rs1_data) : ex0_forward_data) :
        id2_rs1_data;
    assign id2_rs2_capture_data = id2_rs2_match2 ? ex2_id_forward_data :
        id2_rs2_match1 ? (ex1_mem_read ? (ex1_load_ready ? slot1_load_data : id2_rs2_data) : ex1_forward_data) :
        id2_rs2_match0 ? (ex0_mem_read ? (ex0_load_ready ? lsu_load_data : id2_rs2_data) : ex0_forward_data) :
        id2_rs2_data;

endmodule
`default_nettype wire
