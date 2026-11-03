`default_nettype none
// Coherent latest-value mailbox. Source holds the whole bundle until ACK.
// Both resets must assert together; deassert synchronously in each domain.
module meeting_cdc_snapshot #(parameter integer WIDTH=32)(
    input wire src_clk,src_rst_n,input wire [WIDTH-1:0] src_data,
    input wire dst_clk,dst_rst_n,output reg [WIDTH-1:0] dst_data
);
    reg [WIDTH-1:0] held;
    reg request,ack;
    (* async_reg="true" *) reg [1:0] ack_sync,req_sync;
    always @(posedge src_clk or negedge src_rst_n) begin
        if(!src_rst_n)begin held<=0;request<=0;ack_sync<=0;end
        else begin
            ack_sync<={ack_sync[0],ack};
            if(ack_sync[1]==request)begin held<=src_data;request<=~request;end
        end
    end
    always @(posedge dst_clk or negedge dst_rst_n)begin
        if(!dst_rst_n)begin req_sync<=0;ack<=0;dst_data<=0;end
        else begin
            req_sync<={req_sync[0],request};
            if(req_sync[1]!=ack)begin dst_data<=held;ack<=req_sync[1];end
        end
    end
endmodule
`default_nettype wire
