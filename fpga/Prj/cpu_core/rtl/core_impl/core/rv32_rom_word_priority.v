`default_nettype none
// 纯组合优先选择；即使控制信号重叠也保持既有优先级，不引入状态或时序例外。
module rv32_rom_word_priority(
    input wire [7:0] control,
    input wire [27:0] held,issue0,issue1,issue2,redirect0,redirect1,epc,step,trap,
    output wire [27:0] words
);
    // control 位序从高到低：陷阱、MRET、特殊指令、槽 1 重定向、槽 0 重定向、接纳槽 2、接纳槽 1、发射。
    wire older /* synthesis syn_keep=1 */ = control[7] || control[5] || control[4] || control[3];
    wire take_issue /* synthesis syn_keep=1 */ = control[0] && !older;
    wire [8:0] choose /* synthesis syn_keep=1 */;
    assign choose[0]=!(control[0] || control[3] || control[4] || control[5] || control[7]);
    assign choose[1]=take_issue && !control[2] && !control[1];
    assign choose[2]=take_issue && !control[2] && control[1];
    assign choose[3]=take_issue && control[2];
    assign choose[4]=control[3] && !control[4] && !control[5] && !control[7];
    assign choose[5]=control[4] && !control[5] && !control[7];
    assign choose[6]=control[5] && control[6] && !control[7];
    assign choose[7]=control[5] && !control[6] && !control[7];
    assign choose[8]=control[7];
    assign words=({28{choose[0]}} & held) | ({28{choose[1]}} & issue0) |
                 ({28{choose[2]}} & issue1) | ({28{choose[3]}} & issue2) |
                 ({28{choose[4]}} & redirect0) | ({28{choose[5]}} & redirect1) |
                 ({28{choose[6]}} & epc) | ({28{choose[7]}} & step) |
                 ({28{choose[8]}} & trap);
endmodule
`default_nettype wire
