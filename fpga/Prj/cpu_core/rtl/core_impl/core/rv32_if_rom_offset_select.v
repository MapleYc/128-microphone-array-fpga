`default_nettype none
// 纯组合 ROM 地址选择：各候选偏移先计算，再做晚到的独热选择。
// 调用方保证控制流选择互斥；模块内部没有时钟或状态。
module rv32_if_rom_offset_select(
    input wire [3:0] select_onehot,
    input wire [31:0] pc0,pc1,pc2,pc3,
    output wire [13:0] word1,word2
);
    wire [13:0] p0w1 /* synthesis syn_keep=1 */ = pc0[15:2]+14'd1;
    wire [13:0] p0w2 /* synthesis syn_keep=1 */ = pc0[15:2]+14'd2;
    wire [13:0] p1w1 /* synthesis syn_keep=1 */ = pc1[15:2]+14'd1;
    wire [13:0] p1w2 /* synthesis syn_keep=1 */ = pc1[15:2]+14'd2;
    wire [13:0] p2w1 /* synthesis syn_keep=1 */ = pc2[15:2]+14'd1;
    wire [13:0] p2w2 /* synthesis syn_keep=1 */ = pc2[15:2]+14'd2;
    wire [13:0] p3w1 /* synthesis syn_keep=1 */ = pc3[15:2]+14'd1;
    wire [13:0] p3w2 /* synthesis syn_keep=1 */ = pc3[15:2]+14'd2;
    assign word1=({14{select_onehot[0]}} & p0w1) |
                 ({14{select_onehot[1]}} & p1w1) |
                 ({14{select_onehot[2]}} & p2w1) |
                 ({14{select_onehot[3]}} & p3w1);
    assign word2=({14{select_onehot[0]}} & p0w2) |
                 ({14{select_onehot[1]}} & p1w2) |
                 ({14{select_onehot[2]}} & p2w2) |
                 ({14{select_onehot[3]}} & p3w2);
endmodule
`default_nettype wire
