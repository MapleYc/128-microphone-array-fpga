// 流水线冒险控制：检测 ID 消费者与 EX Load 的 load-use 相关，
// 并按停止/错误等待 > 多周期 EX 忙 > EX 重定向 > load-use > 正常推进仲裁。
module rv32_hazard_unit(
    // ID阶段的消费者：IF/ID身份 + 译码器的源使用标志
    input        id_valid_i,
    input        id_use_rs1_i,
    input        id_use_rs2_i,
    input [4:0]  id_rs1_addr_i,
    input [4:0]  id_rs2_addr_i,

    // EX阶段的生产者：来自ID/EX
    input        ex_valid_i,
    input        ex_mem_read_i,
    input        ex_rd_write_i,
    input [4:0]  ex_rd_addr_i,

    // 控制事件
    input        ex_redirect_i,
    input        ex_wait_i,
    input        ex_busy_i,
    input        halt_i,

    // 控制PC及四组级间寄存器
    output reg   pc_en_o,
    output reg   if_id_en_o,
    output reg   if_id_flush_o,
    output reg   id_ex_en_o,
    output reg   id_ex_flush_o,
    output reg   ex_mem_en_o,
    output reg   ex_mem_flush_o,
    output reg   mem_wb_en_o,
    output reg   mem_wb_flush_o,

    // 停止请求和相关诊断
    output reg   halt_req_o,
    output reg   load_use_o
);

always@(*)begin
    // 仅有效 Load 写非 x0 的 rd，且 ID 指令确实使用相同源寄存器时插气泡。
    // load_use_o 是独立诊断结果；即使更高优先级事件发生，它也可能为 1。
    load_use_o = (id_valid_i && ex_valid_i && ex_mem_read_i && 
            ex_rd_write_i && (ex_rd_addr_i != 5'd0) && ((id_use_rs1_i && (id_rs1_addr_i == ex_rd_addr_i))
            || (id_use_rs2_i && (id_rs2_addr_i == ex_rd_addr_i))));
    // 停止/有效 EX WAIT：PC 不更新，四组级间寄存器清空；
    // 各级寄存器需要以 flush 优先于 en 才能执行这里的控制组合。
    if(halt_i || (ex_wait_i && ex_valid_i))begin
        pc_en_o = 1'b0;
        if_id_en_o = 1'b0;
        if_id_flush_o = 1'b1;
        id_ex_en_o = 1'b0;
        id_ex_flush_o = 1'b1;
        ex_mem_en_o = 1'b0;
        ex_mem_flush_o = 1'b1;
        mem_wb_en_o = 1'b0;
        mem_wb_flush_o = 1'b1;
        halt_req_o = 1'b1;
    end
    // 多周期执行等待：保持 PC、IF/ID 和 ID/EX 中的年轻/当前指令；
    // EX/MEM 注入气泡，MEM/WB 正常推进，使更老的后端指令可以排空退休。
    else if(ex_valid_i && ex_busy_i)begin
        pc_en_o = 1'b0;
        if_id_en_o = 1'b0;
        if_id_flush_o = 1'b0;
        id_ex_en_o = 1'b0;
        id_ex_flush_o = 1'b0;
        ex_mem_en_o = 1'b0;
        ex_mem_flush_o = 1'b1;
        mem_wb_en_o = 1'b1;
        mem_wb_flush_o = 1'b0;
        halt_req_o = 1'b0;
    end
    // EX 重定向：丢弃年轻的 IF/ID 和 ID/EX，保留 EX/MEM、MEM/WB 中的较老指令。
    else if(ex_valid_i && ex_redirect_i)begin
        pc_en_o = 1'b1;
        if_id_en_o = 1'b0;
        if_id_flush_o = 1'b1;
        id_ex_en_o = 1'b0;
        id_ex_flush_o = 1'b1;
        ex_mem_en_o = 1'b1;
        ex_mem_flush_o = 1'b0;
        mem_wb_en_o = 1'b1;
        mem_wb_flush_o = 1'b0;
        halt_req_o = 1'b0;
    end
    // load-use：冻结 PC 和 IF/ID，在 ID/EX 注入气泡；让 Load 继续向后级推进。
    else if(load_use_o)begin
        pc_en_o = 1'b0;
        if_id_en_o = 1'b0;
        if_id_flush_o = 1'b0;
        id_ex_en_o = 1'b0;
        id_ex_flush_o = 1'b1;
        ex_mem_en_o = 1'b1;
        ex_mem_flush_o = 1'b0;
        mem_wb_en_o = 1'b1;
        mem_wb_flush_o = 1'b0;
        halt_req_o = 1'b0;
    end
    // 无冒险时四级寄存器和 PC 正常推进。
    else begin
        pc_en_o = 1'b1;
        if_id_en_o = 1'b1;
        if_id_flush_o = 1'b0;
        id_ex_en_o = 1'b1;
        id_ex_flush_o = 1'b0;
        ex_mem_en_o = 1'b1;
        ex_mem_flush_o = 1'b0;
        mem_wb_en_o = 1'b1;
        mem_wb_flush_o = 1'b0;
        halt_req_o = 1'b0;
    end
end




endmodule




