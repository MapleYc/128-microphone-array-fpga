`default_nettype none
// CPU-clock MMIO plus PCLK-domain snapshot storage. Camera reset/PWDN/SCCB
// pins are software controlled; power, XCLK and physical pad assignments are external.
module array128_camera_mmio #(
    parameter [31:0] BASE_ADDR=32'h20007000,
    parameter integer INPUT_WIDTH=1280, INPUT_HEIGHT=720, H_STEP=8, V_STEP=8, SWAP_RB=1,
    parameter integer OUT_W=(INPUT_WIDTH+H_STEP-1)/H_STEP,
    parameter integer OUT_H=(INPUT_HEIGHT+V_STEP-1)/V_STEP,
    parameter integer PIXELS=OUT_W*OUT_H,
    parameter integer ADDR_W=PIXELS>1 ? $clog2(PIXELS) : 1
)(
    input wire clk, rst_n,
    input wire req_valid_i, req_write_i,
    input wire [31:0] req_addr_i, req_data_i,
    input wire [3:0] req_strb_i,
    output wire req_ready_o,
    output reg [31:0] rsp_data_o,
    output reg rsp_error_o,
    input wire cam_pclk_i, cam_vsync_i, cam_href_i,
    input wire [7:0] cam_data_i,
    input wire cam_scl_i, cam_sda_i,
    output wire cam_scl_low_o, cam_sda_low_o, cam_reset_no, cam_pwdn_o,
    output wire irq_o
);
    wire [31:0] offset=req_addr_i-BASE_ADDR;
    reg [3:0] gpio;
    (* async_reg="true" *) reg [1:0] scl_sync,sda_sync;
    always @(posedge clk or negedge rst_n)
        if(!rst_n)begin scl_sync<=3;sda_sync<=3;end
        else begin scl_sync<={scl_sync[0],cam_scl_i};sda_sync<={sda_sync[0],cam_sda_i};end
    reg [1:0] interface_release;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) interface_release<=0; else interface_release<={interface_release[0],1'b1};
    reg irq_enable, frame_ready, error_pending, read_pending;
    reg [2:0] last_error;
    reg [31:0] read_index, completed_frame, completed_pixels;
    wire busy, done;
    wire busy_status=busy || done;
    wire [2:0] capture_error;
    wire [31:0] capture_frame, capture_pixels;
    wire [15:0] memory_data;
    wire valid_data_read=!req_write_i && offset==32'h024 && frame_ready && !busy_status;
    assign req_ready_o=interface_release[1] && (!valid_data_read || read_pending);
    wire accepted=req_valid_i && req_ready_o && !rsp_error_o;
    wire write_fire=accepted && req_write_i;
    wire command=write_fire && offset==32'h004 && req_strb_i[0];
    wire start=command && req_data_i[0];
    wire release_frame=command && req_data_i[1];
    wire clear_error=command && req_data_i[2];
    wire memory_read=req_valid_i && valid_data_read && !read_pending && !rsp_error_o;
    assign cam_scl_low_o=~gpio[0];
    assign cam_sda_low_o=~gpio[1];
    assign cam_reset_no=gpio[2];
    assign cam_pwdn_o=gpio[3];
    assign irq_o=rst_n && irq_enable && (frame_ready || error_pending);
    always @* begin
        rsp_data_o=0; rsp_error_o=0;
        case(offset)
            32'h000: begin rsp_data_o=32'hc5640001; rsp_error_o=req_write_i; end
            32'h004: begin
                if (req_write_i && req_strb_i[0])
                    rsp_error_o=|req_data_i[31:3] || (req_data_i[0] && (busy_status || req_data_i[1])) || (req_data_i[1] && busy_status);
            end
            32'h008: begin rsp_data_o={29'b0,error_pending,frame_ready,busy_status}; rsp_error_o=req_write_i; end
            32'h00c: rsp_data_o={31'b0,irq_enable};
            32'h010: rsp_data_o={28'b0,gpio};
            32'h014: begin rsp_data_o={30'b0,sda_sync[1],scl_sync[1]}; rsp_error_o=req_write_i; end
            32'h018: begin rsp_data_o=completed_frame; rsp_error_o=req_write_i; end
            32'h01c: begin rsp_data_o={29'b0,last_error}; rsp_error_o=req_write_i; end
            32'h020: begin
                rsp_data_o=read_index;
                if (req_write_i && (req_strb_i!=4'hf || req_data_i>=PIXELS)) rsp_error_o=1;
            end
            32'h024: begin rsp_data_o={16'b0,memory_data}; rsp_error_o=req_write_i || !frame_ready || busy_status; end
            32'h028: begin rsp_data_o=(INPUT_HEIGHT<<16)|INPUT_WIDTH; rsp_error_o=req_write_i; end
            32'h02c: begin rsp_data_o=(OUT_H<<16)|OUT_W; rsp_error_o=req_write_i; end
            32'h030: begin rsp_data_o=completed_pixels; rsp_error_o=req_write_i; end
            default: rsp_error_o=1;
        endcase
        if (req_addr_i[1:0]!=0) begin rsp_error_o=1; rsp_data_o=0; end
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            gpio<=4'hb; irq_enable<=0; frame_ready<=0; error_pending<=0;
            read_pending<=0; read_index<=0; completed_frame<=0; completed_pixels<=0; last_error<=0;
        end else begin
            if (memory_read) read_pending<=1;
            if (accepted && valid_data_read) begin
                read_pending<=0;
                if (read_index==PIXELS-1) read_index<=0; else read_index<=read_index+1'b1;
            end
            if (write_fire && offset==32'h010 && req_strb_i[0]) gpio<=req_data_i[3:0];
            if (write_fire && offset==32'h00c && req_strb_i[0]) irq_enable<=req_data_i[0];
            if (write_fire && offset==32'h020) read_index<=req_data_i;
            if (clear_error) begin error_pending<=0; last_error<=0; end
            if (done) begin
                frame_ready<=capture_error==0;
                completed_frame<=capture_frame; completed_pixels<=capture_pixels;
                if (capture_error!=0) begin error_pending<=1; last_error<=capture_error; end
            end
            if (start || release_frame) begin frame_ready<=0; read_index<=0; end
        end
    end
    ov5640_snapshot #(.INPUT_WIDTH(INPUT_WIDTH),.INPUT_HEIGHT(INPUT_HEIGHT),.H_STEP(H_STEP),.V_STEP(V_STEP),.SWAP_RB(SWAP_RB)) u_capture(
        .sys_clk(clk),.rst_n(rst_n),.start_i(start),.busy_o(busy),.done_o(done),
        .error_o(capture_error),.frame_id_o(capture_frame),.pixel_count_o(capture_pixels),
        .read_en_i(memory_read),.read_addr_i(read_index[ADDR_W-1:0]),.read_data_o(memory_data),
        .pclk(cam_pclk_i),.vsync_i(cam_vsync_i),.href_i(cam_href_i),.data_i(cam_data_i));
endmodule
`default_nettype wire
