`timescale 1ns/1ps
// Configuration is writable only while acquisition is disabled.
// y = sat24(round_away((x_Q30 - offset_Q30) * gain_Q22 / 2^29)).
module pcm_calibration #(
    parameter integer CHANNELS=128
) (
    input wire clk, rst_n, enable,
    input wire [CHANNELS*32-1:0] data_i,
    input wire valid_i,
    input wire cfg_valid,
    input wire [6:0] cfg_channel,
    input wire signed [23:0] cfg_gain,
    input wire signed [31:0] cfg_offset,
    output wire cfg_ready,
    output reg [CHANNELS*24-1:0] data_o,
    output reg valid_o,
    output reg clip_o, overrun_o
);
    reg signed [23:0] gain [0:CHANNELS-1];
    reg signed [31:0] offset [0:CHANNELS-1];
    reg [CHANNELS*32-1:0] input_hold;
    reg busy;
    reg [6:0] index, tag1, tag2;
    reg v1;
    reg v2 /* synthesis syn_maxfan=256 */;
    // Wide input/output registers need explicitly bounded control fanout;
    // the PDS global fanout guide alone does not split CE/reset networks.
    wire clear_state /* synthesis syn_keep=1 syn_maxfan=256 */;
    wire capture_input /* synthesis syn_keep=1 syn_maxfan=256 */;
    assign clear_state = !rst_n || !enable;
    assign capture_input = valid_i && !(busy || v1 || v2);
    reg signed [32:0] difference;
    reg signed [23:0] gain_read;
    reg signed [56:0] product;
    // Arithmetic quotient plus one-bit correction is exactly equivalent to
    // signed magnitude rounding, including negative ties away from zero.
    // Avoid cascaded 58-bit negation/addition on the output timing path.
    wire signed [27:0] product_quotient=product[56:29];
    wire round_increment=product[28] && (!product[56] || |product[27:0]);
    wire signed [28:0] rounded_product=
         {product_quotient[27],product_quotient} + $signed({28'b0,round_increment});
    assign cfg_ready = !enable;
    integer c;
    always @(posedge clk) begin
        if(!rst_n) begin
            for(c=0;c<CHANNELS;c=c+1) begin gain[c]<=24'sd4194304; offset[c]<=0; end
        end else if(cfg_valid && cfg_ready && cfg_channel<CHANNELS) begin
            gain[cfg_channel]<=cfg_gain; offset[cfg_channel]<=cfg_offset;
        end
    end
    always @(posedge clk) begin
        if(clear_state) begin
            input_hold<=0; busy<=0; index<=0; tag1<=0; tag2<=0;
            v1<=0; v2<=0; difference<=0; gain_read<=0; product<=0;
            data_o<=0; valid_o<=0; clip_o<=0; overrun_o<=0;
        end else begin
            valid_o<=0; v1<=busy; v2<=v1;
            if(valid_i) begin
                if(busy || v1 || v2) overrun_o<=1;
                else begin busy<=1; index<=0; end
            end
            if(capture_input) input_hold<=data_i;
            if(busy) begin
                difference <= {input_hold[index*32+31],input_hold[index*32 +: 32]} -
                              {offset[index][31],offset[index]};
                gain_read<=gain[index]; tag1<=index;
                if(index==CHANNELS-1) busy<=0;
                else index<=index+1'b1;
            end
            if(v1) begin product<=difference*gain_read; tag2<=tag1; end
            if(v2) begin
                if(rounded_product>29'sd8388607) begin
                    data_o[tag2*24 +: 24]<=24'h7fffff; clip_o<=1;
                end else if(rounded_product < -29'sd8388608) begin
                    data_o[tag2*24 +: 24]<=24'h800000; clip_o<=1;
                end else data_o[tag2*24 +: 24]<=rounded_product[23:0];
                if(tag2==CHANNELS-1) valid_o<=1;
            end
        end
    end
endmodule
