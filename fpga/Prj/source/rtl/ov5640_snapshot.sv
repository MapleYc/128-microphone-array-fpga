`default_nettype none
// One request owns one complete frame. DVP cannot be backpressured.
// Bundled-data CDC: result metadata and RAM stop changing before done_toggle;
// CPU does not read RAM during capture and no new request occurs while busy.
module ov5640_snapshot #(
    parameter integer INPUT_WIDTH=1280, INPUT_HEIGHT=720,
    parameter integer H_STEP=8, V_STEP=8, SWAP_RB=1,
    parameter integer OUTPUT_WIDTH=(INPUT_WIDTH+H_STEP-1)/H_STEP,
    parameter integer OUTPUT_HEIGHT=(INPUT_HEIGHT+V_STEP-1)/V_STEP,
    parameter integer PIXELS=OUTPUT_WIDTH*OUTPUT_HEIGHT,
    parameter integer ADDR_W=PIXELS>1 ? $clog2(PIXELS) : 1
)(
    input wire sys_clk, rst_n,
    input wire start_i,
    output reg busy_o, done_o,
    output reg [2:0] error_o,
    output reg [31:0] frame_id_o, pixel_count_o,
    input wire read_en_i,
    input wire [ADDR_W-1:0] read_addr_i,
    output reg [15:0] read_data_o,
    input wire pclk, vsync_i, href_i,
    input wire [7:0] data_i
);
    reg [1:0] sys_release, cam_release;
    always @(posedge sys_clk or negedge rst_n)
        if (!rst_n) sys_release<=0; else sys_release<={sys_release[0],1'b1};
    always @(posedge pclk or negedge rst_n)
        if (!rst_n) cam_release<=0; else cam_release<={cam_release[0],1'b1};
    wire sys_reset_n=sys_release[1], cam_reset_n=cam_release[1];
    reg request_toggle, complete_toggle;
    (* async_reg="true" *) reg [1:0] request_sync, complete_sync;
    reg request_seen, complete_seen;
    reg [2:0] result_error;
    reg [31:0] result_frame, result_pixels;
    reg [15:0] memory [0:PIXELS-1];
    // Synchronous read; no reset loop over memory, preserving RAM inference.
    always @(posedge sys_clk)
        if (read_en_i && read_addr_i < PIXELS) read_data_o<=memory[read_addr_i];
    always @(posedge sys_clk or negedge sys_reset_n) begin
        if (!sys_reset_n) begin
            request_toggle<=0; complete_sync<=0; complete_seen<=0;
            busy_o<=0; done_o<=0; error_o<=0; frame_id_o<=0; pixel_count_o<=0;
        end else begin
            complete_sync<={complete_sync[0],complete_toggle};
            done_o<=0;
            if (start_i && !busy_o) begin
                request_toggle<=~request_toggle;
                busy_o<=1;
            end
            if (complete_sync[1]!=complete_seen) begin
                complete_seen<=complete_sync[1];
                busy_o<=0; done_o<=1;
                error_o<=result_error;
                frame_id_o<=result_frame; pixel_count_o<=result_pixels;
            end
        end
    end

    reg armed, active, previous_vsync, previous_href, byte_phase;
    reg [7:0] first_byte;
    reg [15:0] line_pixels, lines;
    reg [31:0] output_count, sensor_frame;
    reg bad_line;
    wire frame_end=vsync_i && !previous_vsync;
    wire frame_start=!vsync_i && previous_vsync;
    wire [15:0] packed_pixel={first_byte,data_i};
    wire [15:0] rgb_pixel=SWAP_RB ? {packed_pixel[4:0],packed_pixel[10:5],packed_pixel[15:11]} : packed_pixel;
    wire select_pixel=(line_pixels % H_STEP)==0 && (lines % V_STEP)==0;
    wire final_line_bad=previous_href && (byte_phase || line_pixels!=INPUT_WIDTH);
    wire [16:0] lines_at_end={1'b0,lines}+previous_href;
    always @(posedge pclk or negedge cam_reset_n) begin
        if (!cam_reset_n) begin
            request_sync<=0; request_seen<=0; complete_toggle<=0;
            result_error<=0; result_frame<=0; result_pixels<=0;
            armed<=0; active<=0; previous_vsync<=0; previous_href<=0;
            byte_phase<=0; first_byte<=0; line_pixels<=0; lines<=0;
            output_count<=0; sensor_frame<=0; bad_line<=0;
        end else begin
            request_sync<={request_sync[0],request_toggle};
            previous_vsync<=vsync_i; previous_href<=href_i;
            if (request_sync[1]!=request_seen) begin
                request_seen<=request_sync[1]; armed<=1;
            end
            if (frame_end) begin
                sensor_frame<=sensor_frame+1'b1;
                if (active) begin
                    active<=0;
                    result_error<={output_count!=PIXELS, lines_at_end!=INPUT_HEIGHT, bad_line || final_line_bad};
                    result_frame<=sensor_frame; result_pixels<=output_count;
                    complete_toggle<=~complete_toggle;
                end
            end else if (frame_start) begin
                if (armed) begin
                    armed<=0; active<=1; byte_phase<=0;
                    line_pixels<=0; lines<=0; output_count<=0; bad_line<=0;
                end
            end else if (active && !vsync_i) begin
                if (href_i) begin
                    if (!byte_phase) first_byte<=data_i;
                    else begin
                        if (line_pixels<INPUT_WIDTH && lines<INPUT_HEIGHT && select_pixel && output_count<PIXELS) begin
                            memory[output_count[ADDR_W-1:0]]<=rgb_pixel;
                            output_count<=output_count+1'b1;
                        end
                        if (line_pixels<16'hffff) line_pixels<=line_pixels+1'b1;
                        else bad_line<=1;
                        if (line_pixels>=INPUT_WIDTH || lines>=INPUT_HEIGHT) bad_line<=1;
                    end
                    byte_phase<=~byte_phase;
                end else if (previous_href) begin
                    if (byte_phase || line_pixels!=INPUT_WIDTH) bad_line<=1;
                    byte_phase<=0; line_pixels<=0;
                    if (lines<16'hffff) lines<=lines+1'b1; else bad_line<=1;
                end
            end
        end
    end
`ifndef SYNTHESIS
    initial begin
        if (INPUT_WIDTH<1 || INPUT_WIDTH>65534 || INPUT_HEIGHT<1 || INPUT_HEIGHT>65534 || H_STEP<1 || V_STEP<1)
            $fatal(1,"Invalid snapshot geometry");
        if (OUTPUT_WIDTH!=(INPUT_WIDTH+H_STEP-1)/H_STEP || OUTPUT_HEIGHT!=(INPUT_HEIGHT+V_STEP-1)/V_STEP)
            $fatal(1,"Output geometry must match stride sampling");
    end
`endif
endmodule
`default_nettype wire
