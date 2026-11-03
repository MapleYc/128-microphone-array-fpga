`timescale 1ns/1ps
// Banked history RAM, one read + one write per lane per clock.
// Input vectors are written over GROUPS clocks while an independent MAC engine runs.
// DEPTH includes overwrite margin for samples arriving during a filter job.
module fir_decimator #(
    parameter integer CHANNELS = 128,
    parameter integer LANES = 16,
    parameter integer TAPS = 63,
    parameter integer DECIMATION = 4,
    parameter integer DEPTH = 128,
    parameter integer COEFF_SET = 1
) (
    input wire clk, rst_n,
    input wire [CHANNELS*32-1:0] data_i,
    input wire valid_i,
    output wire [CHANNELS*32-1:0] data_o,
    output reg valid_o,
    output reg overrun_o,
    output wire clip_o
);
    localparam integer GROUPS = CHANNELS/LANES;
    localparam integer GW = (GROUPS < 2) ? 1 : $clog2(GROUPS);
    localparam integer AW = $clog2(DEPTH);
    localparam integer TW = $clog2(TAPS+1);
    localparam integer DW = (DECIMATION < 2) ? 1 : $clog2(DECIMATION);
    reg [CHANNELS*32-1:0] input_hold;
    reg writing /* synthesis syn_maxfan=256 */;
    reg busy;
    reg [GW-1:0] write_group, compute_group;
    reg [AW-1:0] write_pointer, base_pointer;
    reg [DW-1:0] decimation_phase;
    reg [TW-1:0] history_count, job_history, issue_count;
    // Register each complete write transaction before the RAM port.  The
    // lane data mux can now end at local fabric registers rather than a
    // distant fixed RAM site; address, enable and last use the same latency.
    reg write_commit_valid /* synthesis syn_maxfan=256 */;
    reg write_commit_last;
    reg [GW+AW-1:0] write_commit_address /* synthesis syn_maxfan=256 */;
    reg read_valid /* synthesis syn_maxfan=256 */;
    reg partial_valid /* synthesis syn_maxfan=256 */;
    reg middle_valid /* synthesis syn_maxfan=256 */;
    reg product_valid /* synthesis syn_maxfan=256 */;
    reg read_last, partial_last, middle_last, product_last;
    // PDS's global fanout_guide excludes control pins. Explicit limits let
    // ADS duplicate the drivers without adding a pipeline cycle.
    reg final_valid /* synthesis syn_maxfan=256 */;
    wire clear_state /* synthesis syn_keep=1 syn_maxfan=256 */;
    wire capture_input /* synthesis syn_keep=1 syn_maxfan=256 */;
    wire finish_product /* synthesis syn_keep=1 syn_maxfan=256 */;
    assign clear_state = !rst_n;
    assign capture_input = valid_i && !writing;
    assign finish_product = product_valid && product_last;
    reg [GW-1:0] final_group;
    reg signed [31:0] read_coefficient;
    wire issue = busy && (issue_count < TAPS);
    wire [AW-1:0] read_pointer = base_pointer - issue_count;
    wire [LANES-1:0] lane_clip;
    assign clip_o = |lane_clip;

    function automatic signed [31:0] coefficient(input integer address);
        begin
            if(COEFF_SET==1) begin
                case(address)
`include "fir1_coeff.svh"
                    default: coefficient=0;
                endcase
            end else begin
                case(address)
`include "fir2_coeff.svh"
                    default: coefficient=0;
                endcase
            end
        end
    endfunction

    function automatic signed [71:0] rounded(input signed [71:0] value);
        reg signed [41:0] quotient, rounded_quotient;
        reg increment;
        begin
            // Arithmetic floor plus one implements nearest, ties away from 0:
            // positive ties increment; negative ties retain the floor.
            // This avoids a 72-bit abs/add/negate chain on the final MAC path.
            quotient=$signed(value[71:30]);
            increment=value[29] && (!value[71] || (|value[28:0]));
            rounded_quotient=quotient+(increment ? 42'sd1 : 42'sd0);
            rounded={{30{rounded_quotient[41]}},rounded_quotient};
        end
    endfunction

    always @(posedge clk) begin
        if(clear_state) begin
            input_hold<=0; writing<=0; busy<=0;
            write_group<=0; compute_group<=0;
            write_pointer<=0; base_pointer<=0; decimation_phase<=0;
            history_count<=0; job_history<=0; issue_count<=0;
            write_commit_valid<=0; write_commit_last<=0; write_commit_address<=0;
            read_valid<=0; partial_valid<=0; middle_valid<=0; product_valid<=0;
            read_last<=0; partial_last<=0; middle_last<=0; product_last<=0;
            final_valid<=0; final_group<=0;
            read_coefficient<=0; valid_o<=0; overrun_o<=0;
        end else begin
            valid_o<=0;
            write_commit_valid<=writing;
            read_valid<=issue;
            partial_valid<=read_valid; partial_last<=read_last;
            middle_valid<=partial_valid; middle_last<=partial_last;
            product_valid<=middle_valid; product_last<=middle_last;
            final_valid<=finish_product;
            if(finish_product) final_group<=compute_group;
            if(final_valid && final_group==GROUPS-1) valid_o<=1;
            if(issue) begin
                read_coefficient<=coefficient(issue_count);
                read_last<=(issue_count==TAPS-1);
                issue_count<=issue_count+1'b1;
            end
            if(finish_product) begin
                issue_count<=0;
                if(compute_group==GROUPS-1) busy<=0;
                else compute_group<=compute_group+1'b1;
            end
            if(valid_i) begin
                if(writing) overrun_o<=1;
                else begin
                    writing<=1; write_group<=0;
                    if(history_count<TAPS) history_count<=history_count+1'b1;
                end
            end
            if(capture_input) input_hold<=data_i;
            if(writing) begin
                write_commit_address<=write_group*DEPTH+write_pointer;
                write_commit_last<=(write_group==GROUPS-1);
                if(write_group==GROUPS-1) begin
                    writing<=0;
                end else write_group<=write_group+1'b1;
            end
            // Start only after the final RAM write is committed.  In the
            // GROUPS=1 case the first issue is the following edge, so the
            // newest sample is never read on its write edge.
            if(write_commit_valid && write_commit_last) begin
                write_pointer<=write_commit_address[AW-1:0]+1'b1;
                if(decimation_phase==DECIMATION-1) begin
                    decimation_phase<=0;
                    if(busy) overrun_o<=1;
                    else begin
                        busy<=1; compute_group<=0; issue_count<=0;
                        base_pointer<=write_commit_address[AW-1:0]; job_history<=history_count;
                    end
                end else decimation_phase<=decimation_phase+1'b1;
            end
        end
    end
    genvar l,g;
    generate for(l=0;l<LANES;l=l+1) begin: lane
        // No RAM reset: job_history masks uninitialised locations after restart.
        reg signed [31:0] history [0:GROUPS*DEPTH-1];
        (* syn_preserve=1 *) reg [GW-1:0] local_write_group;
        always @(posedge clk) begin
            if(clear_state) local_write_group<=0;
            else if(capture_input) local_write_group<=0;
            else if(writing && write_group!=GROUPS-1) local_write_group<=local_write_group+1'b1;
        end
        reg signed [31:0] write_data /* synthesis syn_preserve=1 syn_keep=1 */;
        reg signed [31:0] read_sample;
        reg [31:0] product_ll;
        reg signed [32:0] product_lh, product_hl;
        reg signed [31:0] product_hh, high_product;
        reg [15:0] low_product;
        reg signed [33:0] middle_product;
        reg signed [63:0] product;
        reg signed [71:0] accumulator, final_sum;
        wire signed [71:0] sum = accumulator + {{8{product[63]}},product};
        // a*b = ll + ((lh+hl)<<16) + (hh<<32).  Carry-save compression
        // adds the three middle operands with only one 34-bit carry chain.
        wire [33:0] middle_a = {product_lh[32],product_lh};
        wire [33:0] middle_b = {product_hl[32],product_hl};
        wire [33:0] middle_c = {18'b0,product_ll[31:16]};
        wire [33:0] middle_bits = middle_a ^ middle_b ^ middle_c;
        wire [33:0] middle_carry = ((middle_a & middle_b) | (middle_a & middle_c) | (middle_b & middle_c)) << 1;
        wire signed [31:0] upper_product = high_product + {{14{middle_product[33]}},middle_product[33:16]};
        reg [GROUPS*32-1:0] result;
        // Decode the upper increment in parallel with the low 32-bit add.
        // These predicates preserve the legacy signed 42-bit wrap, including
        // artificial 72-bit positive extremes; no register/enable is changed.
        wire [41:0] round_quotient = final_sum[71:30];
        wire [31:0] round_low = round_quotient[31:0];
        wire [9:0] round_high = round_quotient[41:32];
        wire round_increment = final_sum[29] &&
            (!final_sum[71] || (|final_sum[28:0]));
        wire round_carry32 = round_increment && (&round_low);
        wire round_high_zero = (!round_carry32 && round_high == 10'd0) ||
            (round_carry32 && round_high == 10'd1023);
        wire round_high_ones = (!round_carry32 && round_high == 10'd1023) ||
            (round_carry32 && round_high == 10'd1022);
        wire round_high_sign = round_high[9] ^
            (round_carry32 && (&round_high[8:0]));
        wire round_low_sign = round_low[31] ^
            (round_increment && (&round_low[30:0]));
        wire [31:0] rounded_low = round_low + {31'b0,round_increment};
        wire result_overflow_pos = !round_high_sign &&
            (!round_high_zero || round_low_sign);
        wire result_overflow_neg = round_high_sign &&
            (!round_high_ones || !round_low_sign);
        wire [31:0] saturated_result = result_overflow_pos ? 32'h7fffffff :
            result_overflow_neg ? 32'h80000000 : rounded_low;
        reg clipped;
        assign lane_clip[l]=clipped;
        // Synchronous simple-dual-port RAM template.
        always @(posedge clk) begin
            // A valid commit consumes the old data staged on the preceding
            // writing edge. Idle/reset refreshes do not commit to RAM.
            write_data <= input_hold[(local_write_group*LANES+l)*32 +: 32];
            if(rst_n && write_commit_valid)
                history[write_commit_address] <= write_data;
            if(issue) begin
                if(issue_count<job_history) read_sample<=history[compute_group*DEPTH+read_pointer];
                else read_sample<=0;
            end
        end
        always @(posedge clk) begin
            if(clear_state) begin
                product_ll<=0; product_lh<=0; product_hl<=0; product_hh<=0;
                high_product<=0; low_product<=0; middle_product<=0;
                product<=0; accumulator<=0; final_sum<=0; clipped<=0;
            end else begin
                // Each multiplier fits one 18x18 hardware multiplier.  In
                // particular, low halves are positive 17-bit signed operands.
                if(read_valid) begin
                    product_ll<=read_sample[15:0]*read_coefficient[15:0];
                    product_lh<=$signed({1'b0,read_sample[15:0]})*$signed(read_coefficient[31:16]);
                    product_hl<=$signed(read_sample[31:16])*$signed({1'b0,read_coefficient[15:0]});
                    product_hh<=$signed(read_sample[31:16])*$signed(read_coefficient[31:16]);
                end
                if(partial_valid) begin
                    middle_product<=$signed(middle_bits)+$signed(middle_carry);
                    high_product<=product_hh; low_product<=product_ll[15:0];
                end
                if(middle_valid) product<={upper_product,middle_product[15:0],low_product};
                if(product_valid) begin
                    if(product_last) begin
                        accumulator<=0;
                    end else accumulator<=sum;
                end
                if(finish_product) final_sum<=sum;
                if(final_valid && (result_overflow_pos || result_overflow_neg))
                    clipped<=1;
            end
        end
        for(g=0;g<GROUPS;g=g+1) begin: flatten
            always @(posedge clk) begin
                if(clear_state) result[g*32 +: 32]<=0;
                else if(final_valid && final_group==g)
                    result[g*32 +: 32]<=saturated_result;
            end
            assign data_o[(g*LANES+l)*32 +: 32]=result[g*32 +: 32];
        end
    end endgenerate
    // synthesis translate_off
    initial begin
        if(CHANNELS<1 || CHANNELS%LANES || DEPTH<TAPS+8 || (DEPTH&(DEPTH-1)) ||
           DECIMATION<1 || !((COEFF_SET==1 && TAPS==63)||(COEFF_SET==2 && TAPS==127)))
            $fatal(1,"Invalid FIR parameters");
    end
    // synthesis translate_on
endmodule
