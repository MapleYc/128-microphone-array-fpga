`default_nettype none
// 128-channel fractional-delay sum. Delay Q8, common causal delay 64 frames.
// Two copies of synchronous ring RAM provide two interpolation taps per input.
// Shadow delay table commits atomically at channel zero; configuration never
// mixes directions within one frame. Raw PCM canonical order is mandatory.
module meeting_beamformer #(
    // Canonical geometry indices. Keep all 128 stream slots and delay entries.
    parameter [127:0] VALID_CHANNEL_MASK={128{1'b1}}
)(
    input wire clk,rst_n,run_i,
    input wire pcm_fire_i,input wire [6:0] pcm_channel_i,
    input wire [63:0] pcm_frame_i,input wire signed [23:0] pcm_data_i,
    input wire delay_write_i,input wire [6:0] delay_index_i,
    input wire [14:0] delay_q8_i,input wire commit_i,
    output reg pending_o,output reg [31:0] epoch_o,
    output reg out_valid_o,output reg signed [15:0] out_pcm_o,
    output reg [63:0] out_frame_o,output reg fault_o,
    output reg [31:0] out_epoch_o
);
    reg signed [15:0] ring_a[0:16383],ring_b[0:16383];
    reg [14:0] delays0[0:127],delays1[0:127];
    reg active_bank,collecting,frame_good;
    reg [6:0] next_channel,history_row;
    reg [7:0] history_frames;
    reg [63:0] held_frame,last_frame;
    reg have_frame;
    wire first=pcm_channel_i==0;
    wire gap=first && have_frame && pcm_frame_i!=last_frame+64'd1;
    wire selected_bank=(first && pending_o) ? !active_bank : active_bank;
    wire [14:0] delay_value=selected_bank ? delays1[pcm_channel_i] : delays0[pcm_channel_i];
    wire [6:0] row=gap ? 7'd0 : history_row;
    wire [6:0] ra=row-delay_value[14:8],rb=ra-1'b1;
    wire valid_word=pcm_fire_i && run_i && (first || (collecting && pcm_channel_i==next_channel && pcm_frame_i==held_frame));
    wire [13:0] wa={row,pcm_channel_i};
    reg signed [15:0] a_q,b_q;
    reg [7:0] fraction_q;
    reg vq,fq,lq,gq,enabled_q;
    reg [63:0] frame_q;
    reg signed [39:0] sum;
    wire signed [16:0] ca=$signed({1'b0,(9'd256-{1'b0,fraction_q})});
    wire signed [16:0] cb=$signed({9'd0,fraction_q});
    wire signed [32:0] weighted=a_q*ca+b_q*cb;
    wire signed [39:0] extended=enabled_q ? {{7{weighted[32]}},weighted} : 40'sd0;
    wire signed [39:0] total=fq ? extended : sum+extended;
    wire signed [40:0] mag=total<0 ? -$signed({total[39],total}) : $signed({total[39],total});
    function integer count_channels(input [127:0] mask);
        integer j;
        begin count_channels=0;for(j=0;j<128;j=j+1)count_channels=count_channels+mask[j];end
    endfunction
    localparam integer ACTIVE_CHANNELS=count_channels(VALID_CHANNEL_MASK);
    localparam integer DIVISOR=(ACTIVE_CHANNELS>0 ? ACTIVE_CHANNELS : 1)*256;
    // |sum| <= 128*32768*256; 32 unsigned bits cover rounding as well.
    wire [31:0] numerator={1'b0,mag[30:0]}+DIVISOR/2;
    // At most 32768 after normalization, so 16 restoring steps suffice.
    // One 17-bit subtract per clock avoids a long combinational divider.
    reg normalizing,negative_q;
    reg [3:0] divide_step;
    reg [16:0] remainder_q;
    reg [15:0] quotient_q;
    reg [63:0] normalized_frame;
    reg [31:0] normalized_epoch;
    wire [16:0] trial={remainder_q[15:0],quotient_q[15]};
    wire subtract=trial>=DIVISOR;
    wire [15:0] next_quotient={quotient_q[14:0],subtract};
    wire signed [16:0] sample_out=negative_q ? -$signed({1'b0,next_quotient}) : $signed({1'b0,next_quotient});
    integer i;
    always @(posedge clk) begin
        if(pcm_fire_i && run_i) begin
            ring_a[wa]<=pcm_data_i[23:8];ring_b[wa]<=pcm_data_i[23:8];
            // Delay zero must forward the sample being written (old RAM data
            // on simultaneous read/write is vendor dependent).
            a_q<=delay_value[14:8]==0 ? pcm_data_i[23:8] : ring_a[{ra,pcm_channel_i}];
            b_q<=ring_b[{rb,pcm_channel_i}];
        end
    end
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            for(i=0;i<128;i=i+1)begin delays0[i]<=15'd16384;delays1[i]<=15'd16384;end
            active_bank<=0;pending_o<=0;epoch_o<=0;collecting<=0;frame_good<=0;
            next_channel<=0;history_row<=0;history_frames<=0;held_frame<=0;last_frame<=0;have_frame<=0;
            vq<=0;fq<=0;lq<=0;gq<=0;enabled_q<=0;frame_q<=0;fraction_q<=0;sum<=0;
            out_valid_o<=0;out_pcm_o<=0;out_frame_o<=0;fault_o<=0;
            out_epoch_o<=0;normalizing<=0;negative_q<=0;divide_step<=0;
            remainder_q<=0;quotient_q<=0;normalized_frame<=0;normalized_epoch<=0;
        end else begin
            out_valid_o<=0;fault_o<=0;vq<=valid_word;
            if(delay_write_i && !pending_o)begin
                if(active_bank)delays0[delay_index_i]<=delay_q8_i;else delays1[delay_index_i]<=delay_q8_i;
            end
            if(commit_i && !pending_o)pending_o<=1;
            if(!run_i)begin
                collecting<=0;history_row<=0;history_frames<=0;have_frame<=0;vq<=0;frame_good<=0;
                normalizing<=0;
            end else begin
                if(normalizing)begin
                    remainder_q<=subtract ? trial-DIVISOR : trial;
                    quotient_q<=next_quotient;
                    if(divide_step==15)begin
                        normalizing<=0;out_valid_o<=1;out_frame_o<=normalized_frame;out_epoch_o<=normalized_epoch;
                        out_pcm_o<=sample_out>32767 ? 16'sh7fff : sample_out< -32768 ? 16'sh8000 : sample_out[15:0];
                    end else divide_step<=divide_step+1'b1;
                end
                if(pcm_fire_i)begin
                    enabled_q<=VALID_CHANNEL_MASK[pcm_channel_i];
                    fraction_q<=delay_value[7:0];fq<=first;lq<=pcm_channel_i==127;frame_q<=pcm_frame_i;
                    gq<=first ? (!gap && history_frames>=128 && pcm_frame_i>=64) : frame_good;
                    if(first)begin
                        if(collecting)fault_o<=1;
                        collecting<=1;next_channel<=1;held_frame<=pcm_frame_i;
                        frame_good<=!gap && history_frames>=128 && pcm_frame_i>=64;
                        if(gap)begin history_row<=0;history_frames<=0;fault_o<=1;end
                        if(pending_o)begin active_bank<=!active_bank;pending_o<=0;epoch_o<=epoch_o+1'b1;end
                    end else if(!valid_word)begin collecting<=0;frame_good<=0;fault_o<=1;end
                    else if(pcm_channel_i==127)begin
                        collecting<=0;history_row<=row+1'b1;
                        if(history_frames<128)history_frames<=history_frames+1'b1;
                        have_frame<=1;last_frame<=pcm_frame_i;
                    end else next_channel<=next_channel+1'b1;
                end
                if(vq)begin
                    sum<=total;
                    if(lq && gq && ACTIVE_CHANNELS!=0)begin
                        normalizing<=1;divide_step<=0;negative_q<=total<0;
                        remainder_q<={1'b0,numerator[31:16]};quotient_q<=numerator[15:0];
                        normalized_frame<=frame_q-64'd64;normalized_epoch<=epoch_o;
                    end
                end
            end
        end
    end
endmodule
`default_nettype wire
