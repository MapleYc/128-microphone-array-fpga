`default_nettype none
// Token FIFO crosses clocks; a complete raw frame is then collected into a
// small destination RAM indexed by geometry channel. Only complete frames
// are published in CH0..CH127 order. Both FIFO resets must assert together.
module array128_pcm_cdc_reorder #(
    parameter integer FIFO_ADDRESS_BITS=9
)(
    input wire src_clk,src_rst_n,dst_clk,dst_rst_n,
    input wire src_valid_i,src_first_i,src_last_i,
    input wire [6:0] src_channel_i,
    input wire [63:0] src_frame_i,
    input wire signed [23:0] src_data_i,
    output wire src_ready_o,
    output wire dst_valid_o,dst_first_o,dst_last_o,
    output reg [6:0] dst_channel_o,
    output reg [63:0] dst_frame_o,
    output reg signed [23:0] dst_data_o,
    input wire dst_ready_i,
    output reg protocol_error_o,
    output reg [31:0] published_frames_o
);
    wire [96:0] token;
    wire token_valid,token_ready;
    async_sample_fifo #(.WIDTH(97),.ADDRESS_BITS(FIFO_ADDRESS_BITS)) u_tokens(
        .wr_clk(src_clk),.wr_rst_n(src_rst_n),.wr_data({src_frame_i,src_channel_i,src_first_i,src_last_i,src_data_i}),
        .wr_valid(src_valid_i),.wr_ready(src_ready_o),
        .rd_clk(dst_clk),.rd_rst_n(dst_rst_n),.rd_data(token),.rd_valid(token_valid),.rd_ready(token_ready));
    wire [63:0] frame=token[96:33];
    wire [6:0] raw_channel=token[32:26];
    wire first=token[25],last=token[24];
    function automatic [6:0] geometry_channel(input [6:0] raw);
        reg [6:0] swapped;
        begin
            swapped=raw^7'd1;
            if(raw<64) geometry_channel=swapped;
            else if(raw<96) geometry_channel=swapped+7'd32;
            else geometry_channel=swapped-7'd32;
        end
    endfunction
    reg emitting,valid;
    reg [7:0] expected_raw,issue_index;
    reg [63:0] collected_frame;
    reg [23:0] memory [0:127];
    assign token_ready=dst_rst_n && !emitting;
    assign dst_valid_o=dst_rst_n && valid;
    assign dst_first_o=dst_valid_o && dst_channel_o==0;
    assign dst_last_o=dst_valid_o && dst_channel_o==127;
    wire legal=raw_channel==expected_raw[6:0] && first==(expected_raw==0) &&
               last==(expected_raw==127) && (expected_raw==0 || frame==collected_frame);
    always @(posedge dst_clk)
        if (token_valid && token_ready && legal) memory[geometry_channel(raw_channel)]<=token[23:0];
    // Synchronous memory read and output staging with held ready/valid.
    always @(posedge dst_clk)
        if (dst_rst_n && emitting && (!valid || dst_ready_i) && issue_index<128)
            dst_data_o<=memory[issue_index[6:0]];
    always @(posedge dst_clk or negedge dst_rst_n) begin
        if(!dst_rst_n) begin
            emitting<=0;valid<=0;expected_raw<=0;issue_index<=0;
            collected_frame<=0;dst_channel_o<=0;dst_frame_o<=0;
            protocol_error_o<=0;published_frames_o<=0;
        end else begin
            if(token_valid && token_ready) begin
                if(!legal) begin expected_raw<=0;protocol_error_o<=1;end
                else begin
                    if(expected_raw==0) collected_frame<=frame;
                    if(expected_raw==127) begin
                        expected_raw<=0;emitting<=1;issue_index<=0;
                        published_frames_o<=published_frames_o+1'b1;
                    end else expected_raw<=expected_raw+1'b1;
                end
            end
            if(emitting && (!valid || dst_ready_i)) begin
                if(issue_index<128) begin
                    valid<=1;dst_channel_o<=issue_index[6:0];dst_frame_o<=collected_frame;
                    issue_index<=issue_index+1'b1;
                end else begin valid<=0;emitting<=0;end
            end
        end
    end
endmodule
`default_nettype wire
