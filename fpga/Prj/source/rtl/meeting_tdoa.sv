`default_nettype none
// Triggered 256x128 PCM snapshot, then 64 pair correlations. Snapshot capture
// and calculation never backpressure audio. Difference pre-emphasis removes
// steady microphone DC. Report best raw correlation plus its normalization
// energies; CPU checks coherence and fits the geometry with outlier rejection.
module meeting_tdoa #(
    parameter [127:0] VALID_CHANNEL_MASK={128{1'b1}},
    // 8 retains the original path exactly. 4 keeps four more PCM bits for
    // quiet speech; correlation and energy then use different physical units.
    parameter integer PCM_SHIFT=8
)(
    input wire clk,rst_n,run_i,trigger_i,
    input wire pcm_fire_i,input wire [6:0] pcm_channel_i,
    input wire [63:0] pcm_frame_i,input wire signed [23:0] pcm_data_i,
    output wire busy_o,output reg done_o,output reg fault_o,
    output reg [63:0] frame_o,
    input wire [5:0] result_index_i,
    output wire signed [7:0] lag_o,
    output wire [31:0] correlation_o,energy_a_o,energy_b_o,
    output reg [6:0] overflow_pairs_o
);
    localparam integer SAMPLE_BITS=24-PCM_SHIFT;
    localparam integer PRODUCT_BITS=2*SAMPLE_BITS;
    localparam integer SUM_BITS=PRODUCT_BITS+8;
    reg signed [SAMPLE_BITS-1:0] samples[0:32767];
    reg signed [SAMPLE_BITS-1:0] previous[0:127];
    reg [7:0] result_lag[0:63];
    reg [31:0] result_c[0:63],result_a[0:63],result_b[0:63];
    reg [2:0] state;
    reg [7:0] capture_row;
    reg [6:0] next_channel;
    reg collecting;
    reg [63:0] held_frame,last_frame;
    reg [5:0] pair_index;
    wire [6:0] channel_a,channel_b;
    wire [5:0] limit_lag;
    meeting_pair_rom pair_rom(.index(pair_index),.a(channel_a),.b(channel_b),.max_lag(limit_lag));
    reg signed [7:0] lag,best_lag;
    reg [6:0] k;
    reg signed [SAMPLE_BITS-1:0] a_q,b_q;
    reg signed [SUM_BITS-1:0] corr,best_corr;
    reg [SUM_BITS-1:0] energy_a,energy_b,best_ea,best_eb;
    wire signed [SAMPLE_BITS:0] difference=$signed({pcm_data_i[23],pcm_data_i[23:PCM_SHIFT]})-$signed({previous[pcm_channel_i][SAMPLE_BITS-1],previous[pcm_channel_i]});
    wire [7:0] time_a=8'd64+{1'b0,k};
    wire [7:0] time_b=time_a+lag;
    wire signed [PRODUCT_BITS-1:0] product=a_q*b_q;
    wire [PRODUCT_BITS-1:0] square_a=a_q*a_q,square_b=b_q*b_q;
    wire signed [SUM_BITS-1:0] next_corr=corr+{{8{product[PRODUCT_BITS-1]}},product};
    wire [SUM_BITS-1:0] next_ea=energy_a+square_a,next_eb=energy_b+square_b;
    // Reject the entire normalized triple on report overflow. Separate
    // saturation or truncation could manufacture a high coherence score.
    wire report_overflow=(best_ea >> 40)!=0 || (best_eb >> 40)!=0 ||
                         (best_corr>0 && ($unsigned(best_corr) >> 40)!=0);
    wire good_word=pcm_channel_i==next_channel && (!collecting || pcm_frame_i==held_frame);
    wire store=state==1 && pcm_fire_i && run_i && good_word;
    assign busy_o=state!=0;
    assign lag_o=result_lag[result_index_i];
    assign correlation_o=result_c[result_index_i];
    assign energy_a_o=result_a[result_index_i];
    assign energy_b_o=result_b[result_index_i];
    integer i;
    always @(posedge clk)begin
        if(store)samples[{capture_row,pcm_channel_i}]<=difference >>> 1;
        if(state==3)begin a_q<=samples[{time_a,channel_a}];b_q<=samples[{time_b,channel_b}];end
    end
    always @(posedge clk or negedge rst_n)begin
        if(!rst_n)begin
            state<=0;done_o<=0;fault_o<=0;frame_o<=0;capture_row<=0;next_channel<=0;collecting<=0;overflow_pairs_o<=0;
            held_frame<=0;last_frame<=0;pair_index<=0;lag<=0;best_lag<=0;k<=0;corr<=0;energy_a<=0;energy_b<=0;
            best_corr<=0;best_ea<=0;best_eb<=0;
            for(i=0;i<128;i=i+1)previous[i]<=0;
        end else begin
            if(pcm_fire_i && run_i)previous[pcm_channel_i]<=pcm_data_i[23:PCM_SHIFT];
            if(!run_i)begin state<=0;done_o<=0;collecting<=0;fault_o<=0;end
            else case(state)
                0:if(trigger_i)begin state<=1;done_o<=0;fault_o<=0;capture_row<=0;next_channel<=0;collecting<=0;overflow_pairs_o<=0;end
                1:if(pcm_fire_i)begin
                    // Wait for a fresh channel zero after trigger; once started
                    // any missing/repeated channel or source frame aborts.
                    if(capture_row==0 && !collecting && next_channel==0 && pcm_channel_i!=0)begin end
                    else if(!good_word || (pcm_channel_i==0 && capture_row!=0 && pcm_frame_i!=last_frame+64'd1))begin
                        state<=0;fault_o<=1;collecting<=0;
                    end else begin
                        if(pcm_channel_i==0)begin collecting<=1;held_frame<=pcm_frame_i;end
                        if(pcm_channel_i==127)begin
                            last_frame<=pcm_frame_i;collecting<=0;next_channel<=0;
                            if(capture_row==255)begin state<=2;pair_index<=0;frame_o<=pcm_frame_i-64'd128;end
                            else capture_row<=capture_row+1'b1;
                        end else next_channel<=next_channel+1'b1;
                    end
                end
                2:begin lag<=-$signed({2'b0,limit_lag});best_corr<={1'b1,{(SUM_BITS-1){1'b0}}};best_lag<=0;
                    best_ea<=0;best_eb<=0;corr<=0;energy_a<=0;energy_b<=0;k<=0;
                    // Disabled pairs publish zero energy/coherence. Firmware
                    // already rejects these; geometry/result indices do not move.
                    state<=VALID_CHANNEL_MASK[channel_a] && VALID_CHANNEL_MASK[channel_b] ? 3 : 5;end
                3:state<=4;
                4:begin
                    corr<=next_corr;energy_a<=next_ea;energy_b<=next_eb;
                    if(k==127)begin
                        if(next_corr>best_corr)begin best_corr<=next_corr;best_lag<=lag;best_ea<=next_ea;best_eb<=next_eb;end
                        if(lag==$signed({2'b0,limit_lag}))state<=5;
                        else begin lag<=lag+1'b1;k<=0;corr<=0;energy_a<=0;energy_b<=0;state<=3;end
                    end else begin k<=k+1'b1;state<=3;end
                end
                5:begin
                    result_lag[pair_index]<=best_lag;
                    if(report_overflow)begin
                        result_c[pair_index]<=0;result_a[pair_index]<=0;result_b[pair_index]<=0;
                        overflow_pairs_o<=overflow_pairs_o+1'b1;
                    end else begin
                        result_c[pair_index]<=best_corr>0 ? best_corr[39:8] : 0;
                        result_a[pair_index]<=best_ea[39:8];result_b[pair_index]<=best_eb[39:8];
                    end
                    if(pair_index==63)begin state<=0;done_o<=1;end
                    else begin pair_index<=pair_index+1'b1;state<=2;end
                end
                default:begin state<=0;fault_o<=1;end
            endcase
        end
    end
endmodule
`default_nettype wire
