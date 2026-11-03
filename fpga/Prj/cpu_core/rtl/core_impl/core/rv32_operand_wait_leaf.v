`default_nettype none
// 提前判断生产者阻塞；译码得到的源寄存器使用标志在调用层单独限定。
module rv32_operand_wait_leaf(
    input wire match0,match1,match2,
    input wire block_load0,block_load1,fast0,fast1,wait_ex2,
    output wire wait_o
) /* synthesis syn_hier = "fixed" */;
    wire load_wait = (block_load0 && match0) || (block_load1 && match1);
    wire mul_wait = match1 ? fast1 : (match0 && fast0);
    assign wait_o = (wait_ex2 && match2) || (!match2 && (load_wait || mul_wait));
endmodule
`default_nettype wire
