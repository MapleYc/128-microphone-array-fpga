`default_nettype none
// V128 version1, fixed32-byte LE header + 1024 bytes RGB565 (final chunk short).
// Format1=RGB565_LE. Timestamp is the source PCM frame near DVP frame start.
// Link loss drains abandoned data through the next START. No partial-frame reuse.
module meeting_video_packetizer #(parameter integer WIDTH=1280,HEIGHT=720,CHUNK=1024)(
    input wire clk,rst_n,enable,
    input wire [257:0] in_data,input wire in_valid,output wire in_ready,
    output wire [7:0] payload_data,output wire payload_valid,input wire payload_ready,
    output wire payload_last,output wire [15:0] payload_length,
    output reg [31:0] frames,packets,aborted
);
    localparam WAIT=0,HEADER=1,PIXELS=2;
    localparam integer FRAME_BYTES=WIDTH*HEIGHT*2;
    localparam integer PACKET_COUNT=(FRAME_BYTES+CHUNK-1)/CHUNK;
    localparam integer LAST_BYTES=FRAME_BYTES-(PACKET_COUNT-1)*CHUNK;
    localparam [15:0] FRAME_W=WIDTH,FRAME_H=HEIGHT;
    reg [1:0] state;
    reg [31:0] fid,offset;
    reg [31:0] chunk_index;
    reg last_packet;
    reg [63:0] source_frame;
    reg [10:0] position;
    reg [4:0] byte_index;
    reg [255:0] word_data;
    reg have_word;
    // Decide the last chunk at a packet boundary. Do not put a 32-bit
    // subtract/compare/add chain in every Ethernet byte handshake.
    wire [15:0] count=last_packet ? LAST_BYTES : CHUNK;
    wire [10:0] last_position=last_packet ? LAST_BYTES-1 : CHUNK-1;
    wire [255:0] header={16'b0,count,FRAME_H,FRAME_W,offset,source_frame,fid,16'd32,8'd1,8'd1,32'h38323156};
    assign payload_data=state==HEADER ? header[position[4:0]*8+:8] : word_data[byte_index*8+:8];
    assign payload_valid=enable && (state==HEADER || (state==PIXELS && have_word));
    assign payload_last=state==PIXELS && position==last_position;
    assign payload_length=count+16'd32;
    assign in_ready=!enable || state==WAIT || (state==PIXELS && !have_word);
    always @(posedge clk or negedge rst_n)begin
        if(!rst_n)begin state<=WAIT;fid<=0;offset<=0;chunk_index<=0;last_packet<=0;source_frame<=0;position<=0;byte_index<=0;word_data<=0;have_word<=0;frames<=0;packets<=0;aborted<=0;end
        else if(!enable)begin
            if(state!=WAIT)aborted<=aborted+1'b1;
            state<=WAIT;have_word<=0;position<=0;byte_index<=0;
        end else begin
            if(in_valid && in_ready)begin
                if(state==WAIT && in_data[257:256]==1)begin
                    fid<=in_data[31:0];source_frame<=in_data[95:32];offset<=0;position<=0;byte_index<=0;have_word<=0;state<=HEADER;
                    chunk_index<=0;last_packet<=PACKET_COUNT==1;
                end else if(state==PIXELS)begin
                    if(in_data[257:256]==0)begin word_data<=in_data[255:0];have_word<=1;byte_index<=0;end
                    else begin state<=WAIT;have_word<=0;aborted<=aborted+1'b1;end
                end
            end
            if(payload_valid && payload_ready)begin
                if(state==HEADER)begin
                    if(position==31)begin position<=0;state<=PIXELS;end else position<=position+1'b1;
                end else begin
                    if(byte_index==31)begin have_word<=0;byte_index<=0;end else byte_index<=byte_index+1'b1;
                    if(position==last_position)begin
                        packets<=packets+1'b1;position<=0;
                        if(last_packet)begin state<=WAIT;frames<=frames+1'b1;end
                        else begin
                            offset<=offset+CHUNK;state<=HEADER;
                            chunk_index<=chunk_index+1'b1;
                            last_packet<=chunk_index==PACKET_COUNT-2;
                        end
                    end else position<=position+1'b1;
                end
            end
        end
    end
`ifndef SYNTHESIS
    initial if(CHUNK%32 || CHUNK<32 || CHUNK>1440 || FRAME_BYTES%32)$fatal(1,"Invalid V128 chunk/geometry");
`endif
endmodule
`default_nettype wire
