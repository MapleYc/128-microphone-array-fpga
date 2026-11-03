// EX 操作数前递选择器：分别为 rs1、rs2 判断数据来自寄存器堆、MEM 或 WB。
// 当多个在途指令写同一 rd 时，离 EX 最近的 EX/MEM 生产者优先。
module rv32_forward_unit(
    // 正在EX阶段使用操作数的指令
    input        ex_valid_i,
    input        ex_use_rs1_i,
    input        ex_use_rs2_i,
    input [4:0]  ex_rs1_addr_i,
    input [4:0]  ex_rs2_addr_i,

    // EX/MEM 寄存器中的较新生产者；result_ready=0 时不能取它的旧结果。
    input        mem_valid_i,
    input        mem_rd_write_i,
    input [4:0]  mem_rd_addr_i,
    input        mem_result_ready_i,

    // MEM/WB 寄存器中的较老生产者
    input        wb_valid_i,
    input        wb_rd_write_i,
    input [4:0]  wb_rd_addr_i,

    output reg [1:0] fwd_rs1_sel_o,
    output reg [1:0] fwd_rs2_sel_o
);


// 两路操作数使用相同的选择编码；FWD_WAIT 由上层处理暂停。
localparam FWD_REG  = 2'd0;
localparam FWD_MEM  = 2'd1;
localparam FWD_WB   = 2'd2;
localparam FWD_WAIT = 2'd3;

always@(*)begin
    // 源寄存器未被使用、EX 指令无效或读 x0 时，无需前递。
    if(!ex_valid_i || !ex_use_rs1_i || (ex_rs1_addr_i == 0))begin 
        fwd_rs1_sel_o = FWD_REG;
    end
    // 先匹配较新的 MEM。若数据尚未就绪，必须等待，不能误用较老的 WB 值。
    else if(mem_valid_i && mem_rd_write_i && (mem_rd_addr_i!= 0) && (mem_rd_addr_i == ex_rs1_addr_i) && mem_result_ready_i)begin
        fwd_rs1_sel_o = FWD_MEM;
    end
    else if(mem_valid_i && mem_rd_write_i && (mem_rd_addr_i!= 0) && (mem_rd_addr_i == ex_rs1_addr_i) && !mem_result_ready_i)begin
        fwd_rs1_sel_o = FWD_WAIT;
    end
    // MEM 不匹配时，才允许从较老的 WB 级取数。
    else if(wb_valid_i && wb_rd_write_i && (wb_rd_addr_i != 0) && (wb_rd_addr_i == ex_rs1_addr_i))begin
        fwd_rs1_sel_o = FWD_WB;
    end
    else begin
        fwd_rs1_sel_o = FWD_REG;
    end 
end

always@(*)begin
    // rs2 与 rs1 的优先级完全相同；包括 Store 写数据和分支比较的 rs2。
    if(!ex_valid_i || !ex_use_rs2_i || (ex_rs2_addr_i == 0))begin 
        fwd_rs2_sel_o = FWD_REG;
    end
    else if(mem_valid_i && mem_rd_write_i && (mem_rd_addr_i!= 0) && (mem_rd_addr_i == ex_rs2_addr_i) && mem_result_ready_i)begin
        fwd_rs2_sel_o = FWD_MEM;
    end
    else if(mem_valid_i && mem_rd_write_i && (mem_rd_addr_i!= 0) && (mem_rd_addr_i == ex_rs2_addr_i) && !mem_result_ready_i)begin
        fwd_rs2_sel_o = FWD_WAIT;
    end
    else if(wb_valid_i && wb_rd_write_i && (wb_rd_addr_i != 0) && (wb_rd_addr_i == ex_rs2_addr_i))begin
        fwd_rs2_sel_o = FWD_WB;
    end
    else begin
        fwd_rs2_sel_o = FWD_REG;
    end 
end



endmodule





