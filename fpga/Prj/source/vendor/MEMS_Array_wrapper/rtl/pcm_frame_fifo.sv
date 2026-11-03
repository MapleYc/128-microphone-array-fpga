`timescale 1ns/1ps
// Whole-frame queue. On full, drop the newest whole frame and preserve alignment.
module pcm_frame_fifo #(
    parameter integer CHANNELS=128,
    parameter integer DEPTH=4
) (
    input wire clk, rst_n,
    input wire [CHANNELS*24-1:0] frame_i,
    input wire [63:0] frame_id_i,
    input wire valid_i,
    output wire signed [23:0] pcm_data,
    output wire [6:0] pcm_channel,
    output wire [63:0] pcm_frame_id,
    output wire pcm_valid, pcm_first, pcm_last,
    input wire pcm_ready,
    output reg overflow_o,
    output reg [31:0] dropped_frames_o
);
    localparam integer AW=(DEPTH<2)?1:$clog2(DEPTH);
    reg [CHANNELS*24-1:0] frames [0:DEPTH-1];
    reg [63:0] ids [0:DEPTH-1];
    reg [AW-1:0] rd,wr;
    reg [$clog2(DEPTH+1)-1:0] count;
    reg [6:0] channel;
    assign pcm_valid=(count!=0);
    assign pcm_channel=channel;
    assign pcm_data=pcm_valid ? frames[rd][channel*24 +: 24] : 24'sd0;
    assign pcm_frame_id=pcm_valid ? ids[rd] : 64'd0;
    assign pcm_first=pcm_valid && channel==0;
    assign pcm_last=pcm_valid && channel==CHANNELS-1;
    wire pop_frame=pcm_valid && pcm_ready && pcm_last;
    wire push_frame=valid_i && ((count<DEPTH) || pop_frame);
    // Preserve the single-write-port RAM template. An explicit limit splits
    // the wide memory write-enable load without turning RAM into registers.
    wire write_enable /* synthesis syn_keep=1 syn_maxfan=256 */;
    assign write_enable=rst_n && push_frame;
    always @(posedge clk) if(write_enable) begin
        frames[wr]<=frame_i; ids[wr]<=frame_id_i;
    end
    always @(posedge clk) begin
        if(!rst_n) begin
            rd<=0; wr<=0; count<=0; channel<=0;
            overflow_o<=0; dropped_frames_o<=0;
        end else begin
            if(pcm_valid && pcm_ready) begin
                if(pcm_last) begin channel<=0; rd<=(rd==DEPTH-1)?0:rd+1'b1; end
                else channel<=channel+1'b1;
            end
            if(push_frame) begin
                wr<=(wr==DEPTH-1)?0:wr+1'b1;
            end
            case({push_frame,pop_frame})
                2'b10: count<=count+1'b1;
                2'b01: count<=count-1'b1;
                default: ;
            endcase
            if(valid_i && !push_frame) begin
                overflow_o<=1;
                if(dropped_frames_o!=32'hffffffff) dropped_frames_o<=dropped_frames_o+1'b1;
            end
        end
    end
    // synthesis translate_off
    initial if(CHANNELS<1 || CHANNELS>128 || DEPTH<1) $fatal(1,"Invalid PCM FIFO parameters");
    // synthesis translate_on
endmodule
