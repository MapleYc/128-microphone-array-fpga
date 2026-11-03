`default_nettype none
// 每次请求连续三条指令，只允许一笔未完成事务，并用组首 PC 标记响应。
// 重定向不能撤回受反压的请求，也不能把旧响应当作新地址的指令。
module rv32_fetch_port (
    input wire clk,rst_n,input wire [31:0] pc_i,
    output wire req_valid_o,input wire req_ready_i,output wire [31:0] req_pc_o,
    input wire rsp_valid_i,output wire rsp_ready_o,
    input wire [95:0] rsp_data_i,input wire [2:0] rsp_error_i,
    output wire valid_o,output wire [95:0] data_o,output wire [2:0] error_o
);
    reg held,pending,cached;
    reg [31:0] held_pc,pending_pc,cached_pc;
    reg [95:0] cached_data;
    reg [2:0] cached_error;
    wire hit=cached && cached_pc==pc_i;
    assign req_valid_o=rst_n && !pending && (held || !hit);
    assign req_pc_o=held ? held_pc : pc_i;
    wire request_fire=req_valid_o && req_ready_i;
    assign rsp_ready_o=rst_n && (pending || request_fire);
    wire response_fire=rsp_valid_i && rsp_ready_o;
    wire [31:0] response_pc=pending ? pending_pc : req_pc_o;
    wire response_hit=response_fire && response_pc==pc_i;
    assign valid_o=rst_n && (hit || response_hit);
    assign data_o=hit ? cached_data : rsp_data_i;
    assign error_o=hit ? cached_error : rsp_error_i;
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            held<=0;pending<=0;cached<=0;held_pc<=0;pending_pc<=0;
            cached_pc<=0;cached_data<=0;cached_error<=0;
        end else begin
            if(req_valid_o && !req_ready_i && !held) begin held<=1;held_pc<=req_pc_o;end
            if(request_fire) begin held<=0;pending<=1;pending_pc<=req_pc_o;end
            if(response_fire) begin
                pending<=0;cached<=1;cached_pc<=response_pc;
                cached_data<=rsp_data_i;cached_error<=rsp_error_i;
            end
        end
    end
endmodule
`default_nettype wire
