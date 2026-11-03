// ID/EX 级间寄存器：锁存指令身份、读出的源寄存器值、立即数和译码控制。
// EX 使用同一时钟沿捕获的整组字段；valid_o=0 时该槽为空，控制信号不得产生架构副作用。
module rv32_id_ex_reg(
    input         clk,
    input         rst_n,
    input         en_i,
    input         flush_i,

    // 指令身份
    input         valid_i,
    input  [31:0] instr_i,
    input  [31:0] pc_i,
    input  [31:0] pc_plus4_i,

    // ID阶段产生的数据
    input  [31:0] rs1_data_i,
    input  [31:0] rs2_data_i,
    input  [31:0] imm_i,
    input  [4:0]  rs1_addr_i,
    input  [4:0]  rs2_addr_i,
    input  [4:0]  rd_addr_i,

    // 译码控制信号：use_rs1/use_rs2 标记真实源操作数，供前递和相关性检测使用。
    input  [3:0]  alu_op_i,
    input  [1:0]  alu_a_sel_i,
    input         alu_b_sel_i,
    input  [1:0]  wb_sel_i,
    input  [1:0]  flow_type_i,
    input  [2:0]  branch_op_i,
    input  [2:0]  lsu_type_i,
    input         reg_write_i,
    input         mem_read_i,
    input         mem_write_i,
    input         use_rs1_i,
    input         use_rs2_i,
    input  [1:0]  sys_op_i,
    input         illegal_i,

    // 输出到EX阶段
    output reg        valid_o,
    output reg [31:0] instr_o,
    output reg [31:0] pc_o,
    output reg [31:0] pc_plus4_o,

    output reg [31:0] rs1_data_o,
    output reg [31:0] rs2_data_o,
    output reg [31:0] imm_o,
    output reg [4:0]  rs1_addr_o,
    output reg [4:0]  rs2_addr_o,
    output reg [4:0]  rd_addr_o,

    output reg [3:0]  alu_op_o,
    output reg [1:0]  alu_a_sel_o,
    output reg        alu_b_sel_o,
    output reg [1:0]  wb_sel_o,
    output reg [1:0]  flow_type_o,
    output reg [2:0]  branch_op_o,
    output reg [2:0]  lsu_type_o,
    output reg        reg_write_o,
    output reg        mem_read_o,
    output reg        mem_write_o,
    output reg        use_rs1_o,
    output reg        use_rs2_o,
    output reg [1:0]  sys_op_o,
    output reg        illegal_o
);


always@(posedge clk or negedge rst_n)begin
    // 优先级：异步复位 > flush 清空整槽 > en 整组推进 > 保持。
    // flush 即使 en_i=0 也生效，便于在暂停时杀掉错误路径或插入气泡。
    if(!rst_n)begin
        valid_o     <= 1'b0;
        pc_o        <= 32'b0;
        instr_o     <= 32'b0;
        pc_plus4_o  <= 32'b0;
        
        rs1_data_o  <= 32'b0;
        rs2_data_o  <= 32'b0;
        imm_o       <= 32'b0;
        rs1_addr_o  <= 5'b0;
        rs2_addr_o  <= 5'b0;
        rd_addr_o   <= 5'b0;
                    
        alu_op_o    <= 4'b0;
        alu_a_sel_o <= 2'b0;
        alu_b_sel_o <= 1'b0;
        wb_sel_o    <= 2'b0;
        flow_type_o <= 2'b0;
        branch_op_o <= 3'b0;
        lsu_type_o  <= 3'b0;
        reg_write_o <= 1'b0;
        mem_read_o  <= 1'b0;
        mem_write_o <= 1'b0;
        use_rs1_o   <= 1'b0;
        use_rs2_o   <= 1'b0;
        sys_op_o    <= 2'b0;
        illegal_o   <= 1'b0; 
    end
    else if(flush_i)begin
        valid_o     <= 1'b0;
        pc_o        <= 32'b0;
        instr_o     <= 32'b0;
        pc_plus4_o  <= 32'b0;
        
        rs1_data_o  <= 32'b0;
        rs2_data_o  <= 32'b0;
        imm_o       <= 32'b0;
        rs1_addr_o  <= 5'b0;
        rs2_addr_o  <= 5'b0;
        rd_addr_o   <= 5'b0;
                    
        alu_op_o    <= 4'b0;
        alu_a_sel_o <= 2'b0;
        alu_b_sel_o <= 1'b0;
        wb_sel_o    <= 2'b0;
        flow_type_o <= 2'b0;
        branch_op_o <= 3'b0;
        lsu_type_o  <= 3'b0;
        reg_write_o <= 1'b0;
        mem_read_o  <= 1'b0;
        mem_write_o <= 1'b0;
        use_rs1_o   <= 1'b0;
        use_rs2_o   <= 1'b0;
        sys_op_o    <= 2'b0;
        illegal_o   <= 1'b0;               
    end
    else if(en_i)begin
        valid_o     <= valid_i;
        pc_o        <= pc_i;
        pc_plus4_o  <= pc_plus4_i;
        instr_o     <= instr_i;
        
        rs1_data_o  <= rs1_data_i;
        rs2_data_o  <= rs2_data_i;
        imm_o       <= imm_i;
        rs1_addr_o  <= rs1_addr_i;
        rs2_addr_o  <= rs2_addr_i;
        rd_addr_o   <= rd_addr_i;
                    
        alu_op_o    <= alu_op_i;
        alu_a_sel_o <= alu_a_sel_i;
        alu_b_sel_o <= alu_b_sel_i;
        wb_sel_o    <= wb_sel_i;
        flow_type_o <= flow_type_i;
        branch_op_o <= branch_op_i;
        lsu_type_o  <= lsu_type_i;
        reg_write_o <= reg_write_i;
        mem_read_o  <= mem_read_i;
        mem_write_o <= mem_write_i;
        use_rs1_o   <= use_rs1_i;
        use_rs2_o   <= use_rs2_i;
        sys_op_o    <= sys_op_i;
        illegal_o   <= illegal_i;
    end
end




endmodule




