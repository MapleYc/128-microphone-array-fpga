// EX 控制流决策：计算 pc_i+4，并根据分支/JAL/JALR 给出候选下一 PC。
// 仅报告重定向及目标未对齐；真正的 PC 更新、异常停止和其他预测优先级由核心顶层决定。
module rv32_next_pc(
    input  [31:0] pc_i,             //当前pc
    input  [31:0] target_i,         //ALU算出的候选目标
    input  [1:0]  flow_type_i,      //对应EX级指令的控制流类型
    input         branch_taken_i,   //对应EX级条件分支是否成立
    input         flow_valid_i,     //当前EX流水槽中确实存在一条有效指令

    output [31:0] pc_plus4_o,       //pc_i 的顺序地址（pc_i+4）
    output [31:0] next_pc_o,        //本周期建议装入PC寄存器的地址
    output        redirect_o,       //存在合法对齐的普通控制流重定向
    output        target_misaligned_o   //生效的跳转/分支候选目标未按4字节对齐
);


localparam [1:0] SEQ = 2'd0;       //普通顺序执行
localparam [1:0] BRANCH = 2'd1;    //条件分支
localparam [1:0] JAL = 2'd2;       //pc相对无条件跳转
localparam [1:0] JALR = 2'd3;      //寄存器间接无条件跳转


reg [31:0] pc_plus4;
reg [31:0] next_pc;
reg        redirect;
reg        target_misaligned;

assign pc_plus4_o = pc_plus4;
assign next_pc_o  = next_pc;
assign redirect_o = redirect;
assign target_misaligned_o = target_misaligned;

always@(*)begin
    pc_plus4 = pc_i + 32'd4;
    // 无有效 EX 指令时只给出顺序候选地址，不发出重定向或未对齐报告。
    if(flow_valid_i)begin
        case(flow_type_i)
        SEQ:begin
            next_pc  = pc_plus4;
            redirect = 1'b0;
            target_misaligned = 1'b0;
        end
        BRANCH:begin
            // 未成立的分支不检查目标对齐；成立且对齐时才发出重定向。
            target_misaligned = (branch_taken_i && (target_i[1:0]!=2'b0)) ? 1'b1: 1'b0;
            if(branch_taken_i && (target_i[1:0]==2'b0))begin
                redirect = 1'b1;
                next_pc  = target_i;
            end
            else begin
                redirect = 1'b0;
                next_pc  = pc_plus4;
            end
        end
        JAL:begin
            if(target_i[1:0]==2'b0)begin
                redirect = 1'b1;
                next_pc  = target_i;
                target_misaligned = 1'b0;
            end
            else begin
                redirect = 1'b0;
                next_pc  = pc_plus4;
                target_misaligned = 1'b1;
            end
        end
        JALR:begin
            // JALR 按规范清除目标 bit0；RV32I 不支持压缩指令，故仍需 bit1=0。
            if(target_i[1]==1'b0)begin
                redirect = 1'b1;
                next_pc  = {target_i[31:1],1'b0};
                target_misaligned = 1'b0;
            end
            else begin
                redirect = 1'b0;
                next_pc  = pc_plus4;
                target_misaligned = 1'b1;
            end
        end
        default:begin
            next_pc = pc_plus4;
            redirect = 1'b0;
            target_misaligned = 1'b0;
        end
    endcase
    end
    else begin
        redirect = 1'b0;
        next_pc  = pc_plus4;
        target_misaligned = 1'b0;
    end
end














endmodule




