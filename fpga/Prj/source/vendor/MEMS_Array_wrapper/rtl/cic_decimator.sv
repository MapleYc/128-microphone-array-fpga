`timescale 1ns/1ps
// Five registered integrators, modulo 2^32, /8, then five pipelined combs.
// Eight lanes each service 16 channels in the 40-cycle PDM period.
// Equal four-PDM-period pipeline delay is intentional and included in both RMs.
module cic_decimator #(
    parameter integer CHANNELS = 128,
    parameter integer LANES = 8
) (
    input wire clk, rst_n,
    input wire [CHANNELS-1:0] bits_i,
    input wire valid_i,
    output wire [CHANNELS*32-1:0] data_o,
    output reg valid_o,
    output reg overrun_o
);
    localparam integer GROUPS = CHANNELS/LANES;
    localparam integer GW = (GROUPS < 2) ? 1 : $clog2(GROUPS);
    reg [CHANNELS-1:0] bits_hold;
    reg running, decimate;
    reg [GW-1:0] group_index;
    reg [2:0] phase;
    reg [4:0] cv /* synthesis syn_maxfan=256 */;
    wire clear_state /* synthesis syn_keep=1 syn_maxfan=256 */;
    assign clear_state = !rst_n;
    reg [GW-1:0] tag [0:4];
    integer j;
    always @(posedge clk) begin
        if (clear_state) begin
            bits_hold <= 0; running <= 0; decimate <= 0;
            group_index <= 0; phase <= 0; cv <= 0;
            valid_o <= 0; overrun_o <= 0;
            for (j=0;j<5;j=j+1) tag[j] <= 0;
        end else begin
            valid_o <= 0;
            cv[0] <= running && decimate;
            tag[0] <= group_index;
            for (j=1;j<5;j=j+1) begin cv[j]<=cv[j-1]; tag[j]<=tag[j-1]; end
            if (cv[4] && tag[4] == GROUPS-1) valid_o <= 1;
            if (running) begin
                if (group_index == GROUPS-1) running <= 0;
                else group_index <= group_index+1'b1;
            end
            if (valid_i) begin
                if (running) overrun_o <= 1;
                else begin
                    bits_hold <= bits_i;
                    group_index <= 0; running <= 1;
                    decimate <= (phase == 7);
                    phase <= phase+1'b1;
                end
            end
        end
    end
    // Memories retain arbitrary physical contents across reset. A shared row
    // validity bit makes each first read logically zero without resetting RAM.
    // All lanes update the same row; there are 6*GROUPS validity bits in total.
    reg [GROUPS-1:0] integrator_valid;
    reg [GROUPS-1:0] comb_valid [0:4];
    integer v;
    always @(posedge clk) begin
        if (clear_state) begin
            integrator_valid <= 0;
            for (v=0;v<5;v=v+1) comb_valid[v] <= 0;
        end else begin
            if (running) integrator_valid[group_index] <= 1'b1;
            if (running && decimate) comb_valid[0][group_index] <= 1'b1;
            for (v=1;v<5;v=v+1)
                if (cv[v-1]) comb_valid[v][tag[v-1]] <= 1'b1;
        end
    end
    genvar l, s, g;
    generate for (l=0;l<LANES;l=l+1) begin: lane
        wire [31:0] integral [0:4];
        wire [31:0] next_last = integral[4]+integral[3];
        wire [31:0] bipolar = bits_hold[group_index*LANES+l] ? 32'd1 : 32'hffffffff;
        reg [31:0] comb_value [0:4];
        reg [GROUPS*32-1:0] result;
        always @(posedge clk) begin
            if (clear_state) begin
                result <= 0;
            end else begin
                if (cv[4]) result[tag[4]*32 +: 32] <= comb_value[4] << 15;
            end
        end
        for (s=0;s<5;s=s+1) begin: integrator_stage
            reg [31:0] history [0:GROUPS-1];
            wire [31:0] stage_input;
            assign integral[s]=integrator_valid[group_index] ? history[group_index] : 32'd0;
            if(s==0) assign stage_input=bipolar;
            else assign stage_input=integral[s-1];
            always @(posedge clk) begin
                if(rst_n && running) history[group_index]<=integral[s]+stage_input;
            end
        end
        for (s=0;s<5;s=s+1) begin: comb_stage
            reg [31:0] history [0:GROUPS-1];
            wire [GW-1:0] read_address;
            if(s==0) assign read_address=group_index;
            else assign read_address=tag[s-1];
            wire [31:0] old_history=comb_valid[s][read_address] ? history[read_address] : 32'd0;
            if(s==0) begin: first_stage
                always @(posedge clk) begin
                    if(clear_state) begin
                        comb_value[s]<=0;
                    end else if(running && decimate) begin
                        comb_value[s]<=next_last-old_history;
                    end
                end
                always @(posedge clk)
                    if(rst_n && running && decimate) history[group_index]<=next_last;
            end else begin: next_stage
                always @(posedge clk) begin
                    if(!rst_n) begin
                        comb_value[s]<=0;
                    end else if(cv[s-1]) begin
                        comb_value[s]<=comb_value[s-1]-old_history;
                    end
                end
                always @(posedge clk)
                    if(rst_n && cv[s-1]) history[tag[s-1]]<=comb_value[s-1];
            end
        end
        for(g=0;g<GROUPS;g=g+1) begin: flatten
            assign data_o[(g*LANES+l)*32 +: 32] = result[g*32 +: 32];
        end
    end endgenerate
    // synthesis translate_off
    initial if (CHANNELS < 1 || LANES < 1 || CHANNELS%LANES)
        $fatal(1,"Invalid CIC channel/lane count");
    // synthesis translate_on
endmodule
