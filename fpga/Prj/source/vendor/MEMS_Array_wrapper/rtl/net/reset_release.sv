`timescale 1ns/1ps
// Asynchronous assertion, three local clock edges before reset is released.
module reset_release(input wire clk,arst_n,output wire rst_n);
    (* syn_preserve=1 *) reg [2:0] release_pipe;
    always @(posedge clk or negedge arst_n)
        if(!arst_n) release_pipe<=0;
        else release_pipe<={release_pipe[1:0],1'b1};
    assign rst_n=release_pipe[2];
endmodule
