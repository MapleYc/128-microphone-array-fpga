`default_nettype none
// 机器态核与取指、访存握手接口封装；正式配置显式启用第三个轻量 ALU 槽。
module rv32_core_bus #(
    parameter [31:0] RESET_PC=0,
    parameter LOCAL_MEMORY_LOOKAHEAD=0,
    parameter LOCAL_MEMORY_RESPONSE_CLASS=0,
    parameter ROM_SCHEDULE=0,
    parameter LOCAL_LOAD0_PAYLOAD_VIEW=0
)(
    input wire clk,rst_n,
    output wire i_req_valid_o,input wire i_req_ready_i,output wire [31:0] i_req_pc_o,
    input wire i_rsp_valid_i,output wire i_rsp_ready_o,
    input wire [95:0] i_rsp_data_i,input wire [2:0] i_rsp_error_i,
    input wire [32:0] i_rsp_schedule_i,
    output wire d0_req_valid_o,input wire d0_req_ready_i,output wire d0_req_write_o,
    output wire [31:0] d0_req_addr_o,d0_req_data_o,output wire [3:0] d0_req_strb_o,
    input wire d0_rsp_valid_i,output wire d0_rsp_ready_o,
    input wire [31:0] d0_rsp_data_i,input wire d0_rsp_error_i,
    output wire d1_req_valid_o,input wire d1_req_ready_i,output wire d1_req_write_o,
    output wire [31:0] d1_req_addr_o,d1_req_data_o,output wire [3:0] d1_req_strb_o,
    input wire d1_rsp_valid_i,output wire d1_rsp_ready_o,
    input wire [31:0] d1_rsp_data_i,input wire d1_rsp_error_i,
    input wire [1:0] dmem_local_ram_i,
    input wire [31:0] dmem_local_payload_i,
    input wire irq_external_i,irq_software_i,irq_timer_i,
    output wire [2:0] commit_valid_o,commit_write_o,
    output wire [95:0] commit_pc_o,commit_instr_o,commit_data_o,
    output wire [14:0] commit_rd_o,
    output wire trap_valid_o,output wire [31:0] trap_pc_o,trap_cause_o,trap_tval_o,
    output wire [31:0] imem_next_pc_o,dmem_preview_addr0_o,dmem_preview_addr1_o,
    output wire [31:0] dmem_preview_load1_o,
    output wire dmem_ram_read_ahead1_o,
    output wire [13:0] imem_next_word1_o,imem_next_word2_o,
    output wire [1:0] dmem_preview_valid_o
);
    wire [31:0] pc,pc1,pc2;
    wire fetch_valid;
    wire [95:0] fetch_data;
    wire [2:0] fetch_error;
    wire read0,write0,read1,write1;
    assign d0_req_valid_o=read0||write0;
    assign d0_req_write_o=write0;
    assign d1_req_valid_o=read1||write1;
    assign d1_req_write_o=write1;
    generate if(LOCAL_MEMORY_LOOKAHEAD) begin : g_local_fetch
        // 本地 ROM 与 PC 寄存器在同一边沿采样同一个下一地址，输出对应当前 PC。
        // 本地路径直接使用同步 ROM 响应，不经过通用请求/响应串行等待。
        assign i_req_valid_o=rst_n;assign i_req_pc_o=pc;
        assign i_rsp_ready_o=rst_n;
        assign fetch_valid=rst_n && i_rsp_valid_i;
        assign fetch_data=i_rsp_data_i;assign fetch_error=i_rsp_error_i;
    end else begin : g_elastic_fetch
    rv32_fetch_port u_fetch(
        .clk(clk),.rst_n(rst_n),.pc_i(pc),.req_valid_o(i_req_valid_o),
        .req_ready_i(i_req_ready_i),.req_pc_o(i_req_pc_o),
        .rsp_valid_i(i_rsp_valid_i),.rsp_ready_o(i_rsp_ready_o),
        .rsp_data_i(i_rsp_data_i),.rsp_error_i(i_rsp_error_i),
        .valid_o(fetch_valid),.data_o(fetch_data),.error_o(fetch_error));
    end endgenerate
    rv32_core_tri #(
        .RESET_PC(RESET_PC),.MACHINE_MODE(1),.MEM_HANDSHAKE(1),.ROM_SCHEDULE(ROM_SCHEDULE),
        .LOCAL_MEMORY_LOOKAHEAD(LOCAL_MEMORY_LOOKAHEAD),
        .LOCAL_LOAD0_PAYLOAD_VIEW(LOCAL_LOAD0_PAYLOAD_VIEW),
        .LOCAL_MEMORY_RESPONSE_CLASS(LOCAL_MEMORY_RESPONSE_CLASS),
        .THIRD_ALU_SLOT(1),.BRANCH_ALU_PAIR(1),.MUL_ALU_PAIR(1),.SLOT1_MUL_PAIR(1),
        .STORE_DATA_BYPASS(1),.LOAD_ADDR_BYPASS(1),.LOOP_PREDICT(1),.LOOP_COUNTER_BITS(8),
        .JALR_BTB(1),.RAS_PREDICT(1),.SLOT1_RET_PREDICT(1),.LOAD_BRANCH_PAIR(1)
    ) u_core(
        .clk(clk),.rst_n(rst_n),
        .imem_addr0_o(pc),.imem_addr1_o(pc1),.imem_addr2_o(pc2),
        .imem_rdata0_i(fetch_data[31:0]),.imem_rdata1_i(fetch_data[63:32]),.imem_rdata2_i(fetch_data[95:64]),
        .ifetch_valid_i(fetch_valid),.ifetch_error_i(fetch_error),.imem_schedule_i(i_rsp_schedule_i),
        .dmem_read_o(read0),.dmem_write_o(write0),.dmem_addr_o(d0_req_addr_o),
        .dmem_wdata_o(d0_req_data_o),.dmem_wstrb_o(d0_req_strb_o),.dmem_rdata_i(d0_rsp_data_i),
        .dmem_req_ready_i(d0_req_ready_i),.dmem_rsp_valid_i(d0_rsp_valid_i),
        .dmem_rsp_ready_o(d0_rsp_ready_o),.dmem_rsp_error_i(d0_rsp_error_i),
        .dmem1_read_o(read1),.dmem1_write_o(write1),.dmem1_addr_o(d1_req_addr_o),
        .dmem1_wdata_o(d1_req_data_o),.dmem1_wstrb_o(d1_req_strb_o),.dmem1_rdata_i(d1_rsp_data_i),
        .dmem1_req_ready_i(d1_req_ready_i),.dmem1_rsp_valid_i(d1_rsp_valid_i),
        .dmem1_rsp_ready_o(d1_rsp_ready_o),.dmem1_rsp_error_i(d1_rsp_error_i),
        .dmem_local_ram_i(dmem_local_ram_i),.dmem_local_payload_i(dmem_local_payload_i),
        .irq_external_i(irq_external_i),.irq_software_i(irq_software_i),.irq_timer_i(irq_timer_i),
        .trap_valid_o(trap_valid_o),.trap_pc_o(trap_pc_o),.trap_cause_o(trap_cause_o),.trap_tval_o(trap_tval_o),
        .imem_next_pc_o(imem_next_pc_o),
        .imem_next_word1_o(imem_next_word1_o),.imem_next_word2_o(imem_next_word2_o),
        .dmem_preview_addr0_o(dmem_preview_addr0_o),
        .dmem_preview_load1_o(dmem_preview_load1_o),
        .dmem_ram_read_ahead1_o(dmem_ram_read_ahead1_o),
        .dmem_preview_addr1_o(dmem_preview_addr1_o),.dmem_preview_valid_o(dmem_preview_valid_o),
        .halted_o(),.halt_pc_o(),.halt_instr_o(),.halt_cause_o(),
        .commit0_valid_o(commit_valid_o[0]),.commit0_pc_o(commit_pc_o[31:0]),
        .commit0_instr_o(commit_instr_o[31:0]),.commit0_rd_write_o(commit_write_o[0]),
        .commit0_rd_addr_o(commit_rd_o[4:0]),.commit0_rd_data_o(commit_data_o[31:0]),
        .commit1_valid_o(commit_valid_o[1]),.commit1_pc_o(commit_pc_o[63:32]),
        .commit1_instr_o(commit_instr_o[63:32]),.commit1_rd_write_o(commit_write_o[1]),
        .commit1_rd_addr_o(commit_rd_o[9:5]),.commit1_rd_data_o(commit_data_o[63:32]),
        .commit2_valid_o(commit_valid_o[2]),.commit2_pc_o(commit_pc_o[95:64]),
        .commit2_instr_o(commit_instr_o[95:64]),.commit2_rd_write_o(commit_write_o[2]),
        .commit2_rd_addr_o(commit_rd_o[14:10]),.commit2_rd_data_o(commit_data_o[95:64]));
endmodule
`default_nettype wire
