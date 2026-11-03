`default_nettype none
// CPU-clock diagnostic snapshot, a non-blocking tap on accepted canonical PCM.
// It does not replace a continuous window buffer or DMA for localization.
module array128_pcm_snapshot_mmio #(
    parameter [31:0] BASE_ADDR=32'h20006000
)(
    input wire clk,rst_n,run_i,
    input wire req_valid_i,req_write_i,
    input wire [31:0] req_addr_i,req_data_i,
    input wire [3:0] req_strb_i,
    output wire req_ready_o,
    output reg [31:0] rsp_data_o,
    output reg rsp_error_o,
    input wire pcm_fire_i,pcm_first_i,pcm_last_i,
    input wire [6:0] pcm_channel_i,
    input wire [63:0] pcm_frame_i,
    input wire signed [23:0] pcm_data_i,
    input wire diag_valid_i,
    input wire [31:0] dropped_frames_i,published_frames_i,
    input wire [4:0] frontend_status_i,
    input wire protocol_error_i,
    output wire irq_o
);
    reg pending,collecting,frame_ready,error_pending,irq_enable,read_pending,diag_seen,previous_run;
    reg [6:0] index;
    reg [7:0] expected_channel;
    reg [63:0] held_frame,frame_id;
    reg [31:0] epoch,snapshot_epoch;
    reg [31:0] diagnostic_drops;
    reg [23:0] memory [0:127];
    reg [23:0] memory_data;
    wire [31:0] offset=req_addr_i-BASE_ADDR;
    wire data_read=!req_write_i && offset==32'h05c && frame_ready;
    assign req_ready_o=rst_n && (!data_read || read_pending);
    wire accepted=req_valid_i && req_ready_o && !rsp_error_o;
    wire write_fire=accepted && req_write_i;
    wire command=write_fire && offset==32'h044 && req_strb_i[0];
    wire trigger=command && req_data_i[0];
    wire release_frame=command && req_data_i[1];
    wire clear_error=command && req_data_i[2];
    wire fetch=req_valid_i && data_read && !read_pending && !rsp_error_o;
    wire take_first=pending && !collecting && pcm_first_i && pcm_channel_i==0 && !pcm_last_i;
    wire take_next=pending && collecting && pcm_channel_i==expected_channel[6:0] && pcm_frame_i==held_frame &&
                   !pcm_first_i && pcm_last_i==(expected_channel==127);
    wire take=pcm_fire_i && (take_first || take_next);
    assign irq_o=rst_n && irq_enable && (frame_ready || error_pending);
    always @(posedge clk) begin
        if(run_i && take) memory[pcm_channel_i]<=pcm_data_i;
        if(fetch) memory_data<=memory[index];
    end
    always @* begin
        rsp_error_o=0;rsp_data_o=0;
        case(offset)
            32'h040:begin rsp_data_o=32'hf1280001;rsp_error_o=req_write_i;end
            32'h044:if(req_write_i && req_strb_i[0])
                rsp_error_o=|req_data_i[31:3] || (req_data_i[0] && (!run_i || pending || req_data_i[1])) || (req_data_i[1] && pending);
            32'h048:begin rsp_data_o={29'b0,error_pending,frame_ready,pending};rsp_error_o=req_write_i;end
            32'h04c:rsp_data_o={31'b0,irq_enable};
            32'h050:begin rsp_data_o=frame_id[31:0];rsp_error_o=req_write_i;end
            32'h054:begin rsp_data_o=frame_id[63:32];rsp_error_o=req_write_i;end
            32'h058:begin rsp_data_o={25'b0,index};if(req_write_i && (req_strb_i!=15 || req_data_i>=128)) rsp_error_o=1;end
            32'h05c:begin rsp_data_o={{8{memory_data[23]}},memory_data};rsp_error_o=req_write_i || !frame_ready;end
            32'h060:begin rsp_data_o=snapshot_epoch;rsp_error_o=req_write_i;end
            32'h064:begin rsp_data_o={diag_seen,25'b0,protocol_error_i,frontend_status_i};rsp_error_o=req_write_i;end
            32'h068:begin rsp_data_o=diagnostic_drops;rsp_error_o=req_write_i;end
            32'h06c:begin rsp_data_o=published_frames_i;rsp_error_o=req_write_i;end
            32'h070:begin rsp_data_o=32'd48000;rsp_error_o=req_write_i;end
            32'h074:begin rsp_data_o=32'd128;rsp_error_o=req_write_i;end
            32'h078:begin rsp_data_o=32'd122880000;rsp_error_o=req_write_i;end
            32'h07c:begin rsp_data_o=32'h00170018;rsp_error_o=req_write_i;end // signed Q1.23, 24 bits
            default:rsp_error_o=1;
        endcase
        if(req_addr_i[1:0]!=0) begin rsp_error_o=1;rsp_data_o=0;end
    end
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            pending<=0;collecting<=0;frame_ready<=0;error_pending<=0;irq_enable<=0;read_pending<=0;
            diag_seen<=0;previous_run<=0;index<=0;expected_channel<=0;held_frame<=0;frame_id<=0;epoch<=0;snapshot_epoch<=0;diagnostic_drops<=0;
        end else begin
            previous_run<=run_i;
            if(run_i && !previous_run) epoch<=epoch+1'b1;
            if(!run_i) diag_seen<=0;else if(diag_valid_i) diag_seen<=1;
            // Reading status latches the matching source-domain drop count.
            if(accepted && !req_write_i && offset==32'h064) diagnostic_drops<=dropped_frames_i;
            if(fetch) read_pending<=1;
            if(accepted && data_read) begin read_pending<=0;index<=index+1'b1;end
            if(write_fire && offset==32'h058) index<=req_data_i[6:0];
            if(write_fire && offset==32'h04c && req_strb_i[0]) irq_enable<=req_data_i[0];
            if(clear_error) error_pending<=0;
            if(release_frame) frame_ready<=0;
            if(trigger) begin pending<=1;collecting<=0;frame_ready<=0;index<=0;end
            if(pending && pcm_fire_i && run_i) begin
                if(take_first) begin collecting<=1;expected_channel<=1;held_frame<=pcm_frame_i;end
                else if(collecting) begin
                    if(!take_next) begin pending<=0;collecting<=0;error_pending<=1;end
                    else if(pcm_last_i) begin
                        pending<=0;collecting<=0;frame_ready<=1;frame_id<=held_frame;snapshot_epoch<=epoch;
                    end else expected_channel<=expected_channel+1'b1;
                end
            end
            if(!run_i && pending) begin pending<=0;collecting<=0;error_pending<=1;end
        end
    end
endmodule
`default_nettype wire
