`default_nettype none
// DVP RGB565 -> framed 256-bit words. Never backpressures the sensor.
// Tokens: 01 START {frame_id[31:0], source_frame[95:32]}, 00 DATA,
// 10 END {pixel_count[31:0], good[32]}. Any malformed/overflow frame is rejected.
module meeting_video_capture #(parameter integer WIDTH=1280,HEIGHT=720)(
    input wire pclk,rst_n,enable,vsync,href,input wire [7:0] data,
    input wire [63:0] source_frame,
    output reg [257:0] out_data,output reg out_valid,input wire out_ready,
    output reg [31:0] sensor_frames,bad_frames,overflow_frames
);
    // Register the entire source-synchronous bus together before the word
    // assembler. HREF must not drive a 258-bit token enable from the pad.
    reg vsync_q,href_q;
    reg [7:0] data_q;
    always @(posedge pclk or negedge rst_n) begin
        if(!rst_n)begin vsync_q<=0;href_q<=0;data_q<=0;end
        else begin vsync_q<=vsync;href_q<=href;data_q<=data;end
    end
    reg pv,ph,active,started,bad,overflow,phase,end_pending;
    reg [7:0] first_byte;
    reg [15:0] x,y;
    reg [31:0] pixels;
    reg [3:0] slot;
    reg [255:0] packed_word;
    reg [32:0] ending;
    wire available=!out_valid || out_ready;
    wire final_line_bad=ph && (phase || x!=WIDTH);
    wire [16:0] final_lines={1'b0,y}+ph;
    always @(posedge pclk or negedge rst_n) begin
        if(!rst_n)begin
            pv<=0;ph<=0;active<=0;started<=0;bad<=0;overflow<=0;phase<=0;
            end_pending<=0;first_byte<=0;x<=0;y<=0;pixels<=0;slot<=0;packed_word<=0;
            ending<=0;out_data<=0;out_valid<=0;sensor_frames<=0;bad_frames<=0;overflow_frames<=0;
        end else begin
            pv<=vsync_q;ph<=href_q;
            if(out_ready)out_valid<=0;
            if(end_pending && available)begin
                out_data<={2'b10,223'b0,ending};out_valid<=1;end_pending<=0;
            end
            if(vsync_q && !pv)begin
                sensor_frames<=sensor_frames+1'b1;
                active<=0;
                if(active && started)begin
                    ending<={!(bad || final_line_bad || final_lines!=HEIGHT || pixels!=WIDTH*HEIGHT || slot!=0),pixels};
                    end_pending<=1;
                    if(bad || final_line_bad || final_lines!=HEIGHT || pixels!=WIDTH*HEIGHT || slot!=0)bad_frames<=bad_frames+1'b1;
                    if(overflow)overflow_frames<=overflow_frames+1'b1;
                end
            end else if(!vsync_q && pv)begin
                active<=0;started<=0;bad<=0;overflow<=0;phase<=0;x<=0;y<=0;pixels<=0;slot<=0;
                if(enable && !end_pending && available)begin
                    active<=1;started<=1;out_data<={2'b01,160'b0,source_frame,sensor_frames};out_valid<=1;
                end
            end else if(active && !vsync_q)begin
                if(href_q)begin
                    if(!phase)first_byte<=data_q;
                    else begin
                        if(x<WIDTH && y<HEIGHT)begin
                            pixels<=pixels+1'b1;
                            // First camera byte is RGB565[15:8]; token byte0 is LSB.
                            packed_word<={first_byte,data_q,packed_word[255:16]};
                            slot<=slot+1'b1;
                            if(slot==15 && !bad)begin
                                if(available)begin out_data<={2'b00,first_byte,data_q,packed_word[255:16]};out_valid<=1;end
                                else begin bad<=1;overflow<=1;end
                            end
                        end else bad<=1;
                        if(x!=65535)x<=x+1'b1;else bad<=1;
                    end
                    phase<=~phase;
                end else if(ph)begin
                    if(phase || x!=WIDTH)bad<=1;
                    phase<=0;x<=0;
                    if(y!=65535)y<=y+1'b1;else bad<=1;
                end
            end
        end
    end
`ifndef SYNTHESIS
    initial if(WIDTH<16 || WIDTH%16 || HEIGHT<1)$fatal(1,"Capture needs width multiple of 16");
`endif
endmodule
`default_nettype wire
