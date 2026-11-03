`default_nettype none
// 只允许一笔未完成访存；EX 持有请求描述符，直到 clear_i 释放。
// 握手期间地址、写数据和字节使能由调用方保持稳定；完成响应由内部寄存器保存。
module rv32_mem_port (
    input wire clk,rst_n,active_i,clear_i,
    output wire req_valid_o,input wire req_ready_i,
    input wire rsp_valid_i,output wire rsp_ready_o,
    input wire [31:0] rsp_data_i,input wire rsp_error_i,
    output wire complete_o,output wire [31:0] data_o,output wire error_o,
    input wire [31:0] local_payload_i,output wire [31:0] local_payload_o
);
    reg sent,done;
    reg [31:0] data;
    reg error;
    assign req_valid_o=rst_n && active_i && !sent && !done;
    wire req_fire=req_valid_o && req_ready_i;
    assign rsp_ready_o=rst_n && active_i && !done && (sent || req_fire);
    wire rsp_fire=rsp_valid_i && rsp_ready_o;
    assign complete_o=!active_i || done || rsp_fire;
    assign data_o=done ? data : rsp_data_i;
    // 只读数据观察通路；事务完成后仍返回已经捕获的响应数据。
    assign local_payload_o=done ? data : local_payload_i;
    assign error_o=active_i && (done ? error : rsp_fire && rsp_error_i);
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin sent<=0;done<=0;data<=0;error<=0;end
        else if(clear_i) begin sent<=0;done<=0;end
        else begin
            if(req_fire) sent<=1;
            if(rsp_fire) begin done<=1;sent<=0;data<=rsp_data_i;error<=rsp_error_i;end
        end
    end
endmodule
`default_nettype wire
