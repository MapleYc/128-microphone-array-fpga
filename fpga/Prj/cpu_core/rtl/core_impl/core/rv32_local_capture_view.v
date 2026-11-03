`default_nettype none
// 操作数数据选择保持最新生产者优先级。
// 未就绪生产者由发射逻辑拒绝；本模块只计算候选数据，不决定发射许可。
module rv32_local_capture_view(
    input wire [4:0] match_priority,
    input wire [31:0] ex2,ex1,load1,load0,ex0,rf,
    output wire [31:0] value
);
    wire [5:0] choose;
    assign choose[5]=match_priority[4];
    assign choose[4]=!match_priority[4] && match_priority[3];
    assign choose[3]=!(|match_priority[4:3]) && match_priority[2];
    assign choose[2]=!(|match_priority[4:2]) && match_priority[1];
    assign choose[1]=!(|match_priority[4:1]) && match_priority[0];
    assign choose[0]=!(|match_priority);
    assign value=({32{choose[5]}} & ex2) | ({32{choose[4]}} & ex1) |
                 ({32{choose[3]}} & load1) | ({32{choose[2]}} & load0) |
                 ({32{choose[1]}} & ex0) | ({32{choose[0]}} & rf);
endmodule
`default_nettype wire
