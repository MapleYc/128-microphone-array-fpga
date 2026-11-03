`timescale 1ns/1ps
// Bundled-data handshake. Source holds an atomic snapshot until the next
// request; ACK crosses two flops before the destination copies that bundle.
// A stopped source never blocks UART. A late response cannot certify a newer
// request: a launch while busy invalidates that response (accept_response=0).
module diag_snapshot_cdc #(parameter integer WIDTH=32)(
    input wire dst_clk,dst_rst_n,launch,
    input wire src_clk,input wire [WIDTH-1:0] src_data,
    output reg [WIDTH-1:0] data,output reg valid,output wire busy
);
    wire src_rst_n;
    reset_release source_reset(.clk(src_clk),.arst_n(dst_rst_n),.rst_n(src_rst_n));
    reg request_toggle,acknowledge;
    (* async_reg="true",syn_preserve=1 *) reg request_meta,request_sync,ack_meta,ack_sync;
    (* syn_preserve=1 *) reg [WIDTH-1:0] held;
    reg waiting,accept_response;
    assign busy=waiting;
    always @(posedge src_clk or negedge src_rst_n) begin
        if(!src_rst_n)begin request_meta<=0;request_sync<=0;acknowledge<=0;held<=0;end
        else begin
            request_meta<=request_toggle;request_sync<=request_meta;
            if(request_sync!=acknowledge)begin held<=src_data;acknowledge<=request_sync;end
        end
    end
    always @(posedge dst_clk or negedge dst_rst_n) begin
        if(!dst_rst_n)begin
            request_toggle<=0;ack_meta<=0;ack_sync<=0;data<=0;valid<=0;
            waiting<=0;accept_response<=0;
        end else begin
            ack_meta<=acknowledge;ack_sync<=ack_meta;
            if(waiting && ack_sync==request_toggle)begin
                waiting<=0;data<=held;valid<=accept_response;
            end
            if(launch)begin
                valid<=0;
                if(!waiting)begin request_toggle<=~request_toggle;waiting<=1;accept_response<=1;end
                else accept_response<=0;
            end
        end
    end
endmodule
