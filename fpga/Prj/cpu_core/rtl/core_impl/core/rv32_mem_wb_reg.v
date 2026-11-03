// MEM/WB 级间寄存器：保存 ALU 与 Load 候选结果、写回选择和指令异常状态。
// 本模块不执行写回；WB 阶段需结合 valid_o、reg_write_o 和异常标志产生最终写使能。
module rv32_mem_wb_reg(
    input         clk,
    input         rst_n,
    input         en_i,
    input         flush_i,

    // 指令身份
    input         valid_i,
    input  [31:0] instr_i,
    input  [31:0] pc_i,
    input  [31:0] pc_plus4_i,

    // MEM 阶段产生或传递的数据：load_data_i 已由 LSU 完成字节/半字选择与扩展。
    input  [31:0] alu_result_i,
    input  [31:0] load_data_i,
    input  [4:0]  rd_addr_i,

    // 写回控制
    input  [1:0]  wb_sel_i,
    input         reg_write_i,

    // 系统和错误信息
    input  [1:0]  sys_op_i,
    input         illegal_i,
    input         target_misaligned_i,
    input         data_misaligned_i,

    // 指令身份输出
    output reg        valid_o,
    output reg [31:0] instr_o,
    output reg [31:0] pc_o,
    output reg [31:0] pc_plus4_o,

    // WB阶段使用的数据
    output reg [31:0] alu_result_o,
    output reg [31:0] load_data_o,
    output reg [4:0]  rd_addr_o,

    // 写回控制输出
    output reg [1:0]  wb_sel_o,
    output reg        reg_write_o,

    // 系统和错误信息输出
    output reg [1:0]  sys_op_o,
    output reg        illegal_o,
    output reg        target_misaligned_o,
    output reg        data_misaligned_o
);



always@(posedge clk or negedge rst_n)begin
    // 优先级：异步复位 > flush 清空整槽 > en 整组推进 > 保持。
    if(!rst_n)begin
        valid_o     <= 1'b0;
        pc_o        <= 32'b0;
        instr_o     <= 32'b0;
        pc_plus4_o  <= 32'b0;
        
        alu_result_o<= 32'b0;
        load_data_o <= 32'b0;
        rd_addr_o   <= 5'b0;
                    
        wb_sel_o    <= 2'b0;
        reg_write_o <= 1'b0;
        
        sys_op_o    <= 2'b0;
        illegal_o   <= 1'b0;
        target_misaligned_o <= 1'b0;
        data_misaligned_o <= 1'b0;
    end
    else if(flush_i)begin
        valid_o     <= 1'b0;
        pc_o        <= 32'b0;
        instr_o     <= 32'b0;
        pc_plus4_o  <= 32'b0;
        
        alu_result_o<= 32'b0;
        load_data_o <= 32'b0;
        rd_addr_o   <= 5'b0;
                    
        wb_sel_o    <= 2'b0;
        reg_write_o <= 1'b0;
        
        sys_op_o    <= 2'b0;
        illegal_o   <= 1'b0;
        target_misaligned_o <= 1'b0;
        data_misaligned_o <= 1'b0;      
    end
    else if(en_i)begin
        valid_o     <= valid_i;    
        pc_o        <= pc_i;       
        instr_o     <= instr_i;    
        pc_plus4_o  <= pc_plus4_i; 
                       
        alu_result_o<= alu_result_i;
        load_data_o <= load_data_i;
        rd_addr_o   <= rd_addr_i;   
                                  
        wb_sel_o    <= wb_sel_i;   
        reg_write_o <= reg_write_i;
        
        sys_op_o    <= sys_op_i;
        illegal_o   <= illegal_i;
        target_misaligned_o <= target_misaligned_i;
        data_misaligned_o <= data_misaligned_i; 
    end
end















endmodule



