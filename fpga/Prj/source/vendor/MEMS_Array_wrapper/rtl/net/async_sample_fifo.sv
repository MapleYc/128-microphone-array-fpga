`timescale 1ns/1ps
// Gray-pointer dual-clock FIFO. Memory and a one-word read staging register.
// Resets must assert together; each domain deasserts its own reset synchronously.
module async_sample_fifo #(
    parameter integer WIDTH=100,
    parameter integer ADDRESS_BITS=10
) (
    input wire wr_clk,wr_rst_n,
    input wire [WIDTH-1:0] wr_data,
    input wire wr_valid,
    output wire wr_ready,
    input wire rd_clk,rd_rst_n,
    output reg [WIDTH-1:0] rd_data,
    output reg rd_valid,
    input wire rd_ready
);
    localparam integer DEPTH=1<<ADDRESS_BITS;
    reg [WIDTH-1:0] memory [0:DEPTH-1];
    reg [ADDRESS_BITS:0] wbin,wgray,rbin,rgray;
    (* syn_preserve=1 *) reg [ADDRESS_BITS:0] rgray_meta,rgray_sync;
    (* syn_preserve=1 *) reg [ADDRESS_BITS:0] wgray_meta,wgray_sync;
    reg full;
    wire push=wr_valid && wr_ready;
    wire [ADDRESS_BITS:0] wnext=wbin+push;
    wire [ADDRESS_BITS:0] wgray_next=(wnext>>1)^wnext;
    wire [ADDRESS_BITS:0] rnext=rbin+1'b1;
    wire empty=(rgray==wgray_sync);
    wire fetch=rd_rst_n && (!rd_valid || rd_ready) && !empty;
    assign wr_ready=wr_rst_n && !full;
    always @(posedge wr_clk) if(push) memory[wbin[ADDRESS_BITS-1:0]]<=wr_data;
    always @(posedge rd_clk) if(fetch) rd_data<=memory[rbin[ADDRESS_BITS-1:0]];
    always @(posedge wr_clk or negedge wr_rst_n) begin
        if(!wr_rst_n) begin
            wbin<=0;wgray<=0;rgray_meta<=0;rgray_sync<=0;full<=0;
        end else begin
            rgray_meta<=rgray;rgray_sync<=rgray_meta;
            wbin<=wnext;wgray<=wgray_next;
            full<=(wgray_next=={~rgray_sync[ADDRESS_BITS:ADDRESS_BITS-1],rgray_sync[ADDRESS_BITS-2:0]});
        end
    end
    always @(posedge rd_clk or negedge rd_rst_n) begin
        if(!rd_rst_n) begin
            rbin<=0;rgray<=0;wgray_meta<=0;wgray_sync<=0;rd_valid<=0;
        end else begin
            wgray_meta<=wgray;wgray_sync<=wgray_meta;
            if(!rd_valid || rd_ready) begin
                rd_valid<=!empty;
                if(!empty) begin rbin<=rnext;rgray<=(rnext>>1)^rnext;end
            end
        end
    end
    // synthesis translate_off
    initial if(ADDRESS_BITS<2 || WIDTH<1) $fatal(1,"Invalid async FIFO dimensions");
    // synthesis translate_on
endmodule
