`default_nettype none
// Packet-atomic fair arbitration. The selected producer owns the interface
// through its final accepted byte; a producer stall never changes ownership.
module meeting_packet_arbiter(
    input wire clk,rst_n,
    input wire [7:0] a_data,b_data,
    input wire a_valid,b_valid,a_last,b_last,
    input wire [15:0] a_length,b_length,
    output wire a_ready,b_ready,
    output wire [7:0] out_data,
    output wire out_valid,out_last,
    output wire [15:0] out_length,
    input wire out_ready,
    output reg [31:0] a_packets,b_packets
);
    reg owned,owner,last_owner;
    wire choose=owned ? owner : (a_valid && b_valid ? !last_owner : b_valid);
    assign out_data=choose ? b_data : a_data;
    assign out_valid=choose ? b_valid : a_valid;
    assign out_last=choose ? b_last : a_last;
    assign out_length=choose ? b_length : a_length;
    assign a_ready=out_ready && !choose;
    assign b_ready=out_ready && choose;
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin owned<=0;owner<=0;last_owner<=1;a_packets<=0;b_packets<=0;end
        else if(out_valid) begin
            if(!owned) begin owned<=1;owner<=choose;end
            if(out_ready && out_last) begin
                owned<=0;last_owner<=choose;
                if(choose)b_packets<=b_packets+1'b1;else a_packets<=a_packets+1'b1;
            end
        end
    end
endmodule
`default_nettype wire
