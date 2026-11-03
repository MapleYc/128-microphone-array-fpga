`default_nettype none
// 固定寄存的取指队首，接收带地址标记的三字同步 ROM 响应。
module rv32_local_fetch_window #(parameter [31:0] RESET_PC=0,parameter PREDECODE=1'b1)(
    input wire clk,rst_n,
    input wire [1:0] pop_i,
    input wire flush_i,
    input wire [31:0] target_i,
    input wire [95:0] response_i,
    output wire [95:0] heads_o,
    output wire [32:0] metadata_o,
    output wire [2:0] count_o,
    output wire [31:0] request_pc_o,
    output wire [31:0] sequential_request_pc_o
);
    reg [31:0] q0,q1,q2,q3,q4,q5;
    reg [2:0] count;
    reg pending;
    reg [31:0] next_request_pc;
    wire [2:0] remain=count-{1'b0,pop_i};
    wire [3:0] filled={1'b0,remain}+(pending ? 4'd3 : 4'd0);
    wire request_fire=filled<=4'd3;
    reg [31:0] s0,s1,s2,s3,s4,s5;
    always @* begin
        s0=q0;s1=q1;s2=q2;s3=q3;s4=q4;s5=q5;
        case(pop_i)
            1:begin s0=q1;s1=q2;s2=q3;s3=q4;s4=q5;end
            2:begin s0=q2;s1=q3;s2=q4;s3=q5;end
            3:begin s0=q3;s1=q4;s2=q5;end
            default:begin end
        endcase
        if(pending)case(remain)
            0:begin s0=response_i[31:0];s1=response_i[63:32];s2=response_i[95:64];end
            1:begin s1=response_i[31:0];s2=response_i[63:32];s3=response_i[95:64];end
            2:begin s2=response_i[31:0];s3=response_i[63:32];s4=response_i[95:64];end
            3:begin s3=response_i[31:0];s4=response_i[63:32];s5=response_i[95:64];end
            default:begin end
        endcase
    end
    assign heads_o={q2,q1,q0};
    // 元数据与 q0 至 q5 采用相同的移位、追加和时钟使能，不增加周期。
    // 这里不保存实时就绪、生产者匹配或异常判断结果。
    generate if(PREDECODE)begin:g_metadata
        wire [10:0] response0,response1,response2;
        reg [10:0] m0,m1,m2,m3,m4,m5;
        reg [10:0] t0,t1,t2,t3,t4,t5;
        rv32_fetch_issue_metadata u_pre0(.instr_i(response_i[31:0]),.metadata_o(response0));
        rv32_fetch_issue_metadata u_pre1(.instr_i(response_i[63:32]),.metadata_o(response1));
        rv32_fetch_issue_metadata u_pre2(.instr_i(response_i[95:64]),.metadata_o(response2));
        always @* begin
            t0=m0;t1=m1;t2=m2;t3=m3;t4=m4;t5=m5;
            case(pop_i)
                1:begin t0=m1;t1=m2;t2=m3;t3=m4;t4=m5;end
                2:begin t0=m2;t1=m3;t2=m4;t3=m5;end
                3:begin t0=m3;t1=m4;t2=m5;end
                default:begin end
            endcase
            if(pending)case(remain)
                0:begin t0=response0;t1=response1;t2=response2;end
                1:begin t1=response0;t2=response1;t3=response2;end
                2:begin t2=response0;t3=response1;t4=response2;end
                3:begin t3=response0;t4=response1;t5=response2;end
                default:begin end
            endcase
        end
        always @(posedge clk)if(rst_n && !flush_i && (pop_i!=0 || pending))begin
            m0<=t0;m1<=t1;m2<=t2;m3<=t3;m4<=t4;m5<=t5;
        end
        assign metadata_o={m2,m1,m0};
    end else begin:g_no_metadata
        assign metadata_o=33'b0;
    end endgenerate
    assign count_o=count;
    assign sequential_request_pc_o=next_request_pc;
    assign request_pc_o=!rst_n ? RESET_PC : flush_i ? target_i : next_request_pc;
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n)begin count<=0;pending<=0;next_request_pc<=RESET_PC;end
        else if(flush_i)begin count<=0;pending<=1;next_request_pc<=target_i+32'd12;end
        else begin
            count<=filled[2:0];pending<=request_fire;
            if(request_fire)next_request_pc<=next_request_pc+32'd12;
        end
    end
    // 指令载荷不复位，仅由有效数量决定是否可以解释。
    always @(posedge clk)if(rst_n && !flush_i && (pop_i!=0 || pending))begin
        q0<=s0;q1<=s1;q2<=s2;q3<=s3;q4<=s4;q5<=s5;
    end
    // synthesis translate_off
    always @(posedge clk)if(rst_n)begin
        if((^pop_i)===1'bx || (^count)===1'bx ||
           (pending!==1'b0 && pending!==1'b1) ||
           (flush_i!==1'b0 && flush_i!==1'b1))
            $fatal(1,"[TB_ERROR] fetch window unknown valid control");
        if(flush_i && (^target_i)===1'bx)$fatal(1,"[TB_ERROR] fetch window unknown target");
        if(!flush_i && (pop_i>count || filled>6))
            $fatal(1,"[TB_ERROR] fetch window conservation");
        if(!flush_i && pending && (^response_i)===1'bx)
            $fatal(1,"[TB_ERROR] fetch window unknown valid response");
    end
    // synthesis translate_on
endmodule
`default_nettype wire
