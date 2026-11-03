`timescale 1ns/1ps
// Frame-atomic, fixed parity gain; Q2.30 input and output retain FIR headroom.
// Positive signed Q2.22 gains only. Configuration is retained across disable
// and bypass. A pending request commits at the next accepted frame boundary.
module pcm_parity_compensation #(
    parameter integer CHANNELS=128,
    parameter [23:0] DEFAULT_EVEN_GAIN_Q22=24'd4194304,
    parameter [23:0] DEFAULT_ODD_GAIN_Q22=24'd4194304,
    // Zero disables startup DC estimation and preserves portable defaults.
    parameter integer DC_ESTIMATE_LOG2=0,
    parameter integer DC_SKIP_FRAMES=96,
    parameter signed [31:0] DC_MAX_OFFSET_Q30=32'sd33554432,
    parameter signed [31:0] DC_STABILITY_Q30=32'sd536871
) (
    input wire clk, rst_n, enable,
    input wire [CHANNELS*32-1:0] data_i,
    input wire valid_i,
    input wire cfg_valid,
    input wire [23:0] even_gain_q22, odd_gain_q22,
    input wire bypass,
    output wire cfg_ready,
    output reg [23:0] active_even_gain_q22, active_odd_gain_q22,
    output reg cfg_error_o,
    output reg [CHANNELS*32-1:0] data_o,
    output reg valid_o, clip_o, overrun_o,
    output wire dc_ready_o,
    output wire [CHANNELS-1:0] dc_rejected_o
);
    localparam integer INDEX_WIDTH=(CHANNELS<2)?1:$clog2(CHANNELS);
    localparam integer GROUPS=(CHANNELS+15)/16;
    reg [CHANNELS*32-1:0] input_hold;
    reg [INDEX_WIDTH-1:0] index, group_tag, read_tag, product_tag;
    reg busy, group_valid, read_valid;
    reg product_valid /* synthesis syn_maxfan=256 */;
    reg signed [23:0] group_gain;
    wire [GROUPS*32-1:0] group_samples;
    // Zero extension also covers CHANNELS<16; the final partial group is padded.
    wire [3:0] group_lane=index;
    wire [2:0] read_group=group_tag>>4;
    // Do not saturate x-DC before multiplying: gains below unity can bring
    // an otherwise out-of-32-bit-range difference back into valid range.
    reg signed [32:0] sample_read;
    reg signed [23:0] gain_read;
    reg pending_valid;
    reg [23:0] pending_even, pending_odd;
    reg [23:0] frame_even, frame_odd;
    reg signed [56:0] product;
    localparam integer DC_ENABLED=(DC_ESTIMATE_LOG2!=0);
    localparam integer DC_LOG2=(DC_ESTIMATE_LOG2<2)?2:DC_ESTIMATE_LOG2;
    localparam integer DC_ACC_WIDTH=32+DC_LOG2;
    localparam integer DC_STATE_WIDTH=DC_ACC_WIDTH+65;
    localparam integer DC_SKIP_WIDTH=(DC_SKIP_FRAMES<1)?1:$clog2(DC_SKIP_FRAMES+1);
    localparam integer DC_WINDOW=(1<<DC_LOG2);
    reg [DC_SKIP_WIDTH-1:0] dc_skip_count;
    reg [DC_LOG2-1:0] dc_frame_count;
    reg dc_complete,dc_window_fault;
    reg [CHANNELS-1:0] dc_rejected;
    reg frame_bypass,frame_dc_apply,frame_dc_train;
    reg frame_dc_first,frame_dc_half,frame_dc_last;
    // Estimation runs beside the audio pipe. Split sum, mean, and qualification
    // across three edges without moving any audio valid/tag stage.
    reg dc_s_valid,dc_m_valid;
    reg signed [DC_ACC_WIDTH-1:0] dc_sum_s,dc_sum_m;
    reg signed [31:0] dc_half_s,dc_half_m,dc_mean_m,dc_new_half_m;
    reg [INDEX_WIDTH-1:0] dc_tag_s,dc_tag_m;
    reg dc_rail_s,dc_rail_m,dc_first_s,dc_first_m;
    reg dc_half_end_s,dc_half_end_m,dc_last_s,dc_last_m;
    wire [GROUPS*DC_STATE_WIDTH-1:0] group_dc_state;
    wire [DC_STATE_WIDTH-1:0] selected_dc_state=
        group_dc_state[read_group*DC_STATE_WIDTH +: DC_STATE_WIDTH];
    wire signed [31:0] raw_sample=group_samples[read_group*32 +: 32];
    wire signed [31:0] frozen_dc=selected_dc_state[31:0];
    wire signed [31:0] previous_half=selected_dc_state[63:32];
    wire signed [DC_ACC_WIDTH-1:0] previous_sum=
        frame_dc_first ? {DC_ACC_WIDTH{1'b0}} : selected_dc_state[64 +: DC_ACC_WIDTH];
    wire signed [DC_ACC_WIDTH-1:0] dc_sum=
        previous_sum + {{DC_LOG2{raw_sample[31]}},raw_sample};
    wire raw_rail=(raw_sample>=32'sd1073741824 || raw_sample<=-32'sd1073741824);
    wire dc_rail=raw_rail || (!frame_dc_first && selected_dc_state[DC_STATE_WIDTH-1]);
    // Rounding is nearest with signed ties away from zero. The full-window
    // average fits signed32; the extra bit makes comparisons unambiguous.
    wire signed [31:0] dc_quotient=dc_sum_s[DC_ACC_WIDTH-1:DC_LOG2];
    wire dc_increment=dc_sum_s[DC_LOG2-1] && (!dc_sum_s[DC_ACC_WIDTH-1] || |dc_sum_s[DC_LOG2-2:0]);
    wire signed [32:0] dc_mean={dc_quotient[31],dc_quotient}+$signed({32'b0,dc_increment});
    wire signed [32:0] half_quotient=dc_sum_s[DC_ACC_WIDTH-1:DC_LOG2-1];
    wire half_tail_nonzero;
    generate if(DC_LOG2>2)begin:g_half_tail
        assign half_tail_nonzero=|dc_sum_s[DC_LOG2-3:0];
    end else begin:g_half_tie
        assign half_tail_nonzero=1'b0;
    end endgenerate
    wire half_increment=dc_sum_s[DC_LOG2-2] &&
        (!dc_sum_s[DC_ACC_WIDTH-1] || half_tail_nonzero);
    wire signed [33:0] half_mean={half_quotient[32],half_quotient}+$signed({33'b0,half_increment});
    wire signed [33:0] half_deviation=
        {{2{dc_mean_m[31]}},dc_mean_m} - {{2{dc_half_m[31]}},dc_half_m};
    wire dc_reject=dc_rail_m || dc_window_fault ||
        $signed({dc_mean_m[31],dc_mean_m})>$signed({DC_MAX_OFFSET_Q30[31],DC_MAX_OFFSET_Q30}) ||
        $signed({dc_mean_m[31],dc_mean_m})<-$signed({DC_MAX_OFFSET_Q30[31],DC_MAX_OFFSET_Q30}) ||
        half_deviation>$signed({{2{DC_STABILITY_Q30[31]}},DC_STABILITY_Q30}) ||
        half_deviation<-$signed({{2{DC_STABILITY_Q30[31]}},DC_STABILITY_Q30});
    wire [31:0] next_half=dc_half_end_m ? dc_new_half_m : dc_first_m ? 32'b0 : dc_half_m;
    wire [31:0] next_offset=(dc_last_m && !dc_reject) ? dc_mean_m : 32'b0;
    wire [DC_STATE_WIDTH-1:0] next_dc_state={dc_rail_m,dc_sum_m,next_half,next_offset};
    wire [3:0] dc_write_lane=dc_tag_m;
    wire [2:0] dc_write_group=dc_tag_m>>4;
    wire signed [31:0] applied_dc=(frame_dc_apply && !frame_bypass && !dc_window_fault) ? frozen_dc : 32'sd0;
    wire signed [32:0] corrected_sample=
        {raw_sample[31],raw_sample} - {applied_dc[31],applied_dc};
    wire clear_state /* synthesis syn_keep=1 syn_maxfan=256 */;
    wire capture /* synthesis syn_keep=1 syn_maxfan=256 */;
    assign clear_state=!rst_n || !enable;
    assign capture=enable && valid_i && !busy && !group_valid && !read_valid && !product_valid;
    wire cfg_accept=cfg_valid && cfg_ready;
    wire cfg_legal=!even_gain_q22[23] && !odd_gain_q22[23];
    wire [23:0] boundary_even=(cfg_accept && cfg_legal)?even_gain_q22:
                              pending_valid?pending_even:active_even_gain_q22;
    wire [23:0] boundary_odd=(cfg_accept && cfg_legal)?odd_gain_q22:
                             pending_valid?pending_odd:active_odd_gain_q22;
    wire signed [34:0] quotient=product[56:22];
    wire increment=product[21] && (!product[56] || |product[20:0]);
    wire signed [35:0] rounded={quotient[34],quotient} + $signed({35'b0,increment});
    assign cfg_ready=!pending_valid;
    assign dc_ready_o=!DC_ENABLED || dc_complete;
    assign dc_rejected_o=dc_rejected | {CHANNELS{DC_ENABLED && dc_complete && dc_window_fault}};

    // Registered 16-way banks followed by an at-most-eight-way mux replace
    // the single 128-way mux. Preserve just these intended pipeline registers.
    genvar bank,lane;
    generate for(bank=0;bank<GROUPS;bank=bank+1)begin:g_read_bank
        wire [511:0] bank_input;
        (* syn_preserve=1 *) reg [31:0] bank_sample;
        // Parallel counter replica: same cycle as index, with no added stage.
        // syn_preserve protects the intentionally equal local counter state.
        (* syn_preserve=1 *) reg [3:0] local_lane;
        always @(posedge clk)begin
            if(clear_state)local_lane<=0;
            else if(capture)local_lane<=0;
            else if(busy && index!=CHANNELS-1)local_lane<=local_lane+1'b1;
        end
        for(lane=0;lane<16;lane=lane+1)begin:g_lane
            if(bank*16+lane<CHANNELS)
                assign bank_input[lane*32 +: 32]=input_hold[(bank*16+lane)*32 +: 32];
            else assign bank_input[lane*32 +: 32]=32'b0;
        end
        always @(posedge clk)begin
            if(clear_state)bank_sample<=0;
            // Idle refreshes are masked by group_valid.  Reading every clock
            // removes the shared busy CE load from these bank registers.
            else bank_sample<=bank_input[local_lane*32 +: 32];
        end
        assign group_samples[bank*32 +: 32]=bank_sample;
        if(DC_ENABLED)begin:g_dc
            // Packed 16-row synchronous state banks share the already local
            // lane address. No full-array reset or 128-input state mux.
            // Layout: rail, exact total sum, rounded first-half mean, DC.
            reg [DC_STATE_WIDTH-1:0] state_memory[0:15];
            reg [DC_STATE_WIDTH-1:0] state_read;
            always @(posedge clk)begin
                if(clear_state)state_read<=0;
                else state_read<=state_memory[local_lane];
                if(!clear_state && dc_m_valid && dc_write_group==bank)
                    state_memory[dc_write_lane]<=next_dc_state;
            end
            assign group_dc_state[bank*DC_STATE_WIDTH +: DC_STATE_WIDTH]=state_read;
        end else begin:g_no_dc
            assign group_dc_state[bank*DC_STATE_WIDTH +: DC_STATE_WIDTH]=0;
        end
    end endgenerate

    always @(posedge clk)begin
        if(clear_state)begin
            dc_s_valid<=0;dc_m_valid<=0;dc_sum_s<=0;dc_sum_m<=0;
            dc_half_s<=0;dc_half_m<=0;dc_mean_m<=0;dc_new_half_m<=0;
            dc_tag_s<=0;dc_tag_m<=0;dc_rail_s<=0;dc_rail_m<=0;
            dc_first_s<=0;dc_first_m<=0;dc_half_end_s<=0;dc_half_end_m<=0;dc_last_s<=0;dc_last_m<=0;
        end else begin
            dc_s_valid<=DC_ENABLED && group_valid && frame_dc_train;
            dc_m_valid<=dc_s_valid;
            if(DC_ENABLED && group_valid && frame_dc_train)begin
                dc_sum_s<=dc_sum;dc_half_s<=previous_half;dc_tag_s<=group_tag;dc_rail_s<=dc_rail;
                dc_first_s<=frame_dc_first;dc_half_end_s<=frame_dc_half;dc_last_s<=frame_dc_last;
            end
            if(dc_s_valid)begin
                dc_sum_m<=dc_sum_s;dc_half_m<=dc_half_s;dc_tag_m<=dc_tag_s;dc_rail_m<=dc_rail_s;
                dc_mean_m<=dc_mean[31:0];dc_new_half_m<=half_mean[31:0];
                dc_first_m<=dc_first_s;dc_half_end_m<=dc_half_end_s;dc_last_m<=dc_last_s;
            end
        end
    end

    always @(posedge clk) begin
        if(!rst_n) begin
            active_even_gain_q22<=DEFAULT_EVEN_GAIN_Q22;
            active_odd_gain_q22<=DEFAULT_ODD_GAIN_Q22;
            pending_valid<=0;pending_even<=0;pending_odd<=0;cfg_error_o<=0;
        end else begin
            cfg_error_o<=cfg_accept && !cfg_legal;
            if(!enable || capture) begin
                if(pending_valid || (cfg_accept && cfg_legal)) begin
                    active_even_gain_q22<=boundary_even;
                    active_odd_gain_q22<=boundary_odd;
                    pending_valid<=0;
                end
            end else if(cfg_accept && cfg_legal) begin
                pending_even<=even_gain_q22;pending_odd<=odd_gain_q22;pending_valid<=1;
            end
        end
    end

    always @(posedge clk) begin
        if(clear_state) begin
            input_hold<=0;index<=0;group_tag<=0;read_tag<=0;product_tag<=0;
            busy<=0;group_valid<=0;read_valid<=0;product_valid<=0;
            group_gain<=0;sample_read<=0;gain_read<=0;
            frame_even<=24'd4194304;frame_odd<=24'd4194304;product<=0;
            data_o<=0;valid_o<=0;clip_o<=0;overrun_o<=0;
            dc_skip_count<=0;dc_frame_count<=0;dc_complete<=0;dc_window_fault<=0;dc_rejected<=0;
            frame_bypass<=0;frame_dc_apply<=0;frame_dc_train<=0;
            frame_dc_first<=0;frame_dc_half<=0;frame_dc_last<=0;
        end else begin
            valid_o<=0;group_valid<=busy;read_valid<=group_valid;product_valid<=read_valid;
            if(dc_m_valid && dc_last_m)begin
                dc_rejected[dc_tag_m]<=dc_reject;
                if(dc_tag_m==CHANNELS-1)dc_complete<=1;
            end
            if(valid_i && (busy || group_valid || read_valid || product_valid))begin
                overrun_o<=1;
                if(DC_ENABLED && !dc_complete && !frame_bypass)dc_window_fault<=1;
            end
            if(capture) begin
                input_hold<=data_i;busy<=1;index<=0;
                frame_even<=bypass?24'd4194304:boundary_even;
                frame_odd<=bypass?24'd4194304:boundary_odd;
                frame_bypass<=bypass;
                frame_dc_apply<=DC_ENABLED && dc_complete && !bypass;
                frame_dc_train<=0;frame_dc_first<=0;frame_dc_half<=0;frame_dc_last<=0;
                if(DC_ENABLED && !dc_complete && !bypass)begin
                    if(dc_skip_count<DC_SKIP_FRAMES)dc_skip_count<=dc_skip_count+1'b1;
                    else begin
                        frame_dc_train<=1;
                        frame_dc_first<=(dc_frame_count==0);
                        frame_dc_half<=(dc_frame_count==DC_WINDOW/2-1);
                        frame_dc_last<=(dc_frame_count==DC_WINDOW-1);
                        if(dc_frame_count!=DC_WINDOW-1)dc_frame_count<=dc_frame_count+1'b1;
                    end
                end
            end
            if(busy) begin
                group_gain<=index[0]?frame_odd:frame_even;group_tag<=index;
                if(index==CHANNELS-1) busy<=0;
                else index<=index+1'b1;
            end
            if(group_valid)begin
                sample_read<=corrected_sample;
                gain_read<=group_gain;read_tag<=group_tag;
            end
            if(read_valid)begin product<=sample_read*gain_read;product_tag<=read_tag;end
            if(product_valid) begin
                if(rounded>36'sd2147483647) begin
                    data_o[product_tag*32 +: 32]<=32'h7fffffff;clip_o<=1;
                end else if(rounded < -36'sd2147483648) begin
                    data_o[product_tag*32 +: 32]<=32'h80000000;clip_o<=1;
                end else data_o[product_tag*32 +: 32]<=rounded[31:0];
                if(product_tag==CHANNELS-1)begin
                    valid_o<=1;
                end
            end
        end
    end
    // synthesis translate_off
    initial begin
        if(CHANNELS<1 || CHANNELS>128 || DEFAULT_EVEN_GAIN_Q22[23] || DEFAULT_ODD_GAIN_Q22[23])
            $fatal(1,"Invalid parity compensation parameters");
        if((DC_ESTIMATE_LOG2!=0 && (DC_ESTIMATE_LOG2<2 || DC_ESTIMATE_LOG2>20)) ||
           DC_SKIP_FRAMES<0 || DC_MAX_OFFSET_Q30[31] || DC_STABILITY_Q30[31])
            $fatal(1,"Invalid startup DC estimation parameters");
    end
    // synthesis translate_on
endmodule
