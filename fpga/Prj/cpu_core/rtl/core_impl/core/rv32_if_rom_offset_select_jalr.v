`default_nettype none
// 提前计算 RAS/BTB 各自目标偏移，再选择间接跳转来源，避免选择后串入加法。
module rv32_if_rom_offset_select_jalr(
    input wire [3:0] select_onehot,
    input wire [31:0] pc0,pc1,pc2,ras,btb,
    input wire jalr_return,
    output wire [13:0] word1,word2
);
    wire [13:0] p0w1 /* synthesis syn_keep=1 */ = pc0[15:2]+14'd1;
    wire [13:0] p0w2 /* synthesis syn_keep=1 */ = pc0[15:2]+14'd2;
    wire [13:0] p1w1 /* synthesis syn_keep=1 */ = pc1[15:2]+14'd1;
    wire [13:0] p1w2 /* synthesis syn_keep=1 */ = pc1[15:2]+14'd2;
    wire [13:0] p2w1 /* synthesis syn_keep=1 */ = pc2[15:2]+14'd1;
    wire [13:0] p2w2 /* synthesis syn_keep=1 */ = pc2[15:2]+14'd2;
    wire [13:0] rasw1 /* synthesis syn_keep=1 */ = ras[15:2]+14'd1;
    wire [13:0] btbw1 /* synthesis syn_keep=1 */ = btb[15:2]+14'd1;
    wire [13:0] p3w1 = jalr_return ? rasw1 : btbw1;
    wire [13:0] rasw2 /* synthesis syn_keep=1 */ = ras[15:2]+14'd2;
    wire [13:0] btbw2 /* synthesis syn_keep=1 */ = btb[15:2]+14'd2;
    wire [13:0] p3w2 = jalr_return ? rasw2 : btbw2;
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
