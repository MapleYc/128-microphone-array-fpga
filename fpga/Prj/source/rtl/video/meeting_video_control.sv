`default_nettype none
// Preserve the SCCB/reset register ABI. There is no CPU-readable frame RAM.
// 0x34 bit0 starts autonomous video; 0x40..0x7c are coherent diagnostic words.
module meeting_video_control(
    input wire clk,rst_n,req_valid_i,req_write_i,
    input wire [31:0] req_addr_i,req_data_i,input wire [3:0] req_strb_i,
    output wire req_ready_o,output reg [31:0] rsp_data_o,output reg rsp_error_o,
    input wire cam_scl_i,cam_sda_i,
    output wire cam_scl_low_o,cam_sda_low_o,cam_reset_no,cam_pwdn_o,irq_o,
    input wire [511:0] video_status_i,output reg stream_enable_o
);
    reg [3:0] gpio;
    (* async_reg="true" *) reg [1:0] scl_sync,sda_sync;
    wire [11:0] off=req_addr_i[11:0];
    assign req_ready_o=rst_n;
    assign cam_scl_low_o=~gpio[0];assign cam_sda_low_o=~gpio[1];
    assign cam_reset_no=gpio[2];assign cam_pwdn_o=gpio[3];assign irq_o=0;
    always @*begin
        rsp_error_o=0;rsp_data_o=0;
        case(off)
            12'h000:begin rsp_data_o=32'hc5640001;rsp_error_o=req_write_i;end
            12'h004:begin if(req_write_i && req_data_i[0])rsp_error_o=1;end
            12'h008:begin rsp_data_o=0;rsp_error_o=req_write_i;end
            12'h00c:rsp_data_o=0;
            12'h010:rsp_data_o={28'b0,gpio};
            12'h014:begin rsp_data_o={30'b0,sda_sync[1],scl_sync[1]};rsp_error_o=req_write_i;end
            12'h018:begin rsp_data_o=video_status_i[31:0];rsp_error_o=req_write_i;end
            12'h01c:begin rsp_data_o=video_status_i[511:480];rsp_error_o=req_write_i;end
            12'h028,12'h02c:begin rsp_data_o={16'd720,16'd1280};rsp_error_o=req_write_i;end
            12'h030:begin rsp_data_o=32'd921600;rsp_error_o=req_write_i;end
            12'h034:begin rsp_data_o={31'b0,stream_enable_o};if(req_write_i && |req_data_i[31:1])rsp_error_o=1;end
            default:begin
                if(off>=12'h040 && off<12'h080)begin rsp_data_o=video_status_i[(off[5:2])*32+:32];rsp_error_o=req_write_i;end
                else rsp_error_o=1;
            end
        endcase
        if(off[1:0]!=0)rsp_error_o=1;
    end
    always @(posedge clk or negedge rst_n)begin
        if(!rst_n)begin gpio<=4'hb;scl_sync<=3;sda_sync<=3;stream_enable_o<=0;end
        else begin
            scl_sync<={scl_sync[0],cam_scl_i};sda_sync<={sda_sync[0],cam_sda_i};
            if(req_valid_i && req_ready_o && req_write_i && !rsp_error_o && req_strb_i[0])begin
                if(off==12'h010)gpio<=req_data_i[3:0];
                if(off==12'h034)stream_enable_o<=req_data_i[0];
            end
        end
    end
endmodule
`default_nettype wire
