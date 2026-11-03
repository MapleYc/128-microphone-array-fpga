// EX/MEM 级间寄存器：把执行结果、Store 原始数据和后续所需控制一起推进到访存阶段。
// 指令身份及异常标志与数据同拍传递，避免 MEM/WB 误配不同指令的结果。
module rv32_ex_mem_reg(
    input         clk,
    input         rst_n,
    input         en_i,
    input         flush_i,

    // 指令身份
    input         valid_i,
    input  [31:0] instr_i,
    input  [31:0] pc_i,
    input  [31:0] pc_plus4_i,

    // EX阶段产生的数据
    input  [31:0] alu_result_i,
    input  [31:0] store_data_i,
    input  [4:0]  rd_addr_i,

    // 传递到MEM及WB的控制
    input  [1:0]  wb_sel_i,
    input  [2:0]  lsu_type_i,
    input         reg_write_i,
    input         mem_read_i,
    input         mem_write_i,

    // 系统和错误信息：target_misaligned_i 表示生效的控制流候选目标不满足 4 字节对齐。
    input  [1:0]  sys_op_i,
    input         illegal_i,
    input         target_misaligned_i,

    // 指令身份输出
    output reg        valid_o,
    output reg [31:0] instr_o,
    output reg [31:0] pc_o,
    output reg [31:0] pc_plus4_o,

    // MEM阶段使用的数据
    output reg [31:0] alu_result_o,
    output reg [31:0] store_data_o,
    output reg [4:0]  rd_addr_o,

    // MEM及WB控制输出
    output reg [1:0]  wb_sel_o,
    output reg [2:0]  lsu_type_o,
    output reg        reg_write_o,
    output reg        mem_read_o,
    output reg        mem_write_o,

    // 系统和错误信息输出
    output reg [1:0]  sys_op_o,
    output reg        illegal_o,
    output reg        target_misaligned_o
);



always@(posedge clk or negedge rst_n)begin
    // 优先级：异步复位 > flush 清空整槽 > en 整组推进 > 保持。
    // Store 是否真的写入内存仍须由下游使用 valid_o、mem_write_o 等共同判定。
    if(!rst_n)begin
        valid_o     <= 1'b0;
        pc_o        <= 32'b0;
        instr_o     <= 32'b0;
        pc_plus4_o  <= 32'b0;
        
        alu_result_o<= 32'b0;
        store_data_o<= 32'b0;
        rd_addr_o   <= 5'b0;
                    
        wb_sel_o    <= 2'b0;
        lsu_type_o  <= 3'b0;
        reg_write_o <= 1'b0;
        mem_read_o  <= 1'b0;
        mem_write_o <= 1'b0;
        
        sys_op_o    <= 2'b0;
        illegal_o   <= 1'b0;
        target_misaligned_o <= 1'b0;
    end
    else if(flush_i)begin
        valid_o     <= 1'b0;
        pc_o        <= 32'b0;
        instr_o     <= 32'b0;
        pc_plus4_o  <= 32'b0;
        
        alu_result_o<= 32'b0;
        store_data_o<= 32'b0;
        rd_addr_o   <= 5'b0;
                    
        wb_sel_o    <= 2'b0;
        lsu_type_o  <= 3'b0;
        reg_write_o <= 1'b0;
        mem_read_o  <= 1'b0;
        mem_write_o <= 1'b0;
        sys_op_o    <= 2'b0;
        illegal_o   <= 1'b0;
        target_misaligned_o <= 1'b0;        
    end
    else if(en_i)begin
        valid_o     <= valid_i;    
        pc_o        <= pc_i;       
        instr_o     <= instr_i;    
        pc_plus4_o  <= pc_plus4_i; 
                       
        alu_result_o<= alu_result_i;
        store_data_o<= store_data_i;
        rd_addr_o   <= rd_addr_i;   
                                  
        wb_sel_o    <= wb_sel_i;   
        lsu_type_o  <= lsu_type_i; 
        reg_write_o <= reg_write_i;
        mem_read_o  <= mem_read_i; 
        mem_write_o <= mem_write_i; 
        sys_op_o    <= sys_op_i;
        illegal_o   <= illegal_i;
        target_misaligned_o <= target_misaligned_i;
    end
end




endmodule




