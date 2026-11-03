`default_nettype none
// Audio-page offsets 0x200 TDOA, 0x300 shadow beam delays, 0x400 beam PCM16k.
module meeting_spatial_mmio #(
    parameter [31:0] BASE_ADDR=32'h20006000,
    parameter integer FIFO_DEPTH=1024,
    parameter [127:0] VALID_CHANNEL_MASK={128{1'b1}}
)(
    input wire clk,rst_n,run_i,
    input wire req_valid_i,req_write_i,
    input wire [31:0] req_addr_i,req_data_i,input wire [3:0] req_strb_i,
    output wire req_ready_o,output reg [31:0] rsp_data_o,output reg rsp_error_o,
    input wire pcm_fire_i,input wire [6:0] pcm_channel_i,input wire [63:0] pcm_frame_i,
    input wire signed [23:0] pcm_data_i
);
    localparam integer PW=$clog2(FIFO_DEPTH),CW=$clog2(FIFO_DEPTH+1);
    function integer count_channels(input [127:0] mask);
        integer j;
        begin count_channels=0;for(j=0;j<128;j=j+1)count_channels=count_channels+mask[j];end
    endfunction
    localparam integer ACTIVE_CHANNELS=count_channels(VALID_CHANNEL_MASK);
    wire [31:0] off=req_addr_i-BASE_ADDR;
    wire busy,done,doa_fault,beam_pending,beam_valid,beam_fault,fir_valid,fir_fault;
    wire [63:0] doa_frame,beam_frame,fir_frame;
    wire [31:0] corr,ea,eb,beam_epoch,beam_output_epoch,fir_epoch;
    wire signed [7:0] lag;
    wire signed [15:0] beam_pcm,fir_pcm;
    reg [5:0] pair_index;
    reg [6:0] delay_index;
    reg [31:0] fault_count,drop_count,produced_count;
    reg [PW-1:0] rp,wp;
    reg [CW-1:0] count;
    reg [111:0] fifo[0:FIFO_DEPTH-1];
    reg [111:0] read_word;
    reg read_pending;
    reg [63:0] popped_frame,current_frame;
    reg [31:0] popped_epoch;
    wire data_read=!req_write_i && off==32'h410 && count!=0 && run_i;
    assign req_ready_o=rst_n && (!data_read || read_pending);
    wire accept=req_valid_i && req_ready_o && !rsp_error_o;
    wire wr=accept && req_write_i;
    wire pop=accept && data_read;
    wire fetch=req_valid_i && data_read && !read_pending && !rsp_error_o;
    wire push=fir_valid && run_i && (count<FIFO_DEPTH || pop);
    meeting_tdoa #(.VALID_CHANNEL_MASK(VALID_CHANNEL_MASK),.PCM_SHIFT(4)) doa(.clk(clk),.rst_n(rst_n),.run_i(run_i),.trigger_i(wr && off==32'h204),
        .pcm_fire_i(pcm_fire_i),.pcm_channel_i(pcm_channel_i),.pcm_frame_i(pcm_frame_i),.pcm_data_i(pcm_data_i),
        .busy_o(busy),.done_o(done),.fault_o(doa_fault),.frame_o(doa_frame),
        .result_index_i(pair_index),.lag_o(lag),.correlation_o(corr),.energy_a_o(ea),.energy_b_o(eb));
    meeting_beamformer #(.VALID_CHANNEL_MASK(VALID_CHANNEL_MASK)) beam(.clk(clk),.rst_n(rst_n),.run_i(run_i),
        .pcm_fire_i(pcm_fire_i),.pcm_channel_i(pcm_channel_i),.pcm_frame_i(pcm_frame_i),.pcm_data_i(pcm_data_i),
        .delay_write_i(wr && off==32'h308),.delay_index_i(delay_index),.delay_q8_i(req_data_i[14:0]),
        .commit_i(wr && off==32'h30c),.pending_o(beam_pending),.epoch_o(beam_epoch),
        .out_valid_o(beam_valid),.out_pcm_o(beam_pcm),.out_frame_o(beam_frame),.fault_o(beam_fault),.out_epoch_o(beam_output_epoch));
    meeting_mono_fir fir(.clk(clk),.rst_n(rst_n),.flush_i(!run_i),
        .in_valid_i(beam_valid),.in_pcm_i(beam_pcm),.in_frame_i(beam_frame),.in_epoch_i(beam_output_epoch),
        .ready_o(),.out_valid_o(fir_valid),.out_pcm_o(fir_pcm),.out_frame_o(fir_frame),.out_epoch_o(fir_epoch),.fault_o(fir_fault));
    always @* begin
        rsp_data_o=0;rsp_error_o=0;
        case(off)
            32'h200:begin rsp_data_o=32'hd1280001;rsp_error_o=req_write_i;end
            32'h204:if(req_write_i)rsp_error_o=req_data_i!=1 || busy || !run_i;
            32'h208:begin rsp_data_o={29'b0,doa_fault,done,busy};rsp_error_o=req_write_i;end
            32'h20c:begin rsp_data_o=pair_index;if(req_write_i)rsp_error_o=req_data_i>=64;end
            32'h210:begin rsp_data_o={{24{lag[7]}},lag};rsp_error_o=req_write_i || !done;end
            32'h214:begin rsp_data_o=corr;rsp_error_o=req_write_i || !done;end
            32'h218:begin rsp_data_o=ea;rsp_error_o=req_write_i || !done;end
            32'h21c:begin rsp_data_o=eb;rsp_error_o=req_write_i || !done;end
            32'h220:begin rsp_data_o=doa_frame[31:0];rsp_error_o=req_write_i;end
            32'h224:begin rsp_data_o=doa_frame[63:32];rsp_error_o=req_write_i;end
            32'h228:begin rsp_data_o=current_frame[31:0];rsp_error_o=req_write_i;end
            32'h22c:begin rsp_data_o=current_frame[63:32];rsp_error_o=req_write_i;end
            32'h230:begin rsp_data_o=VALID_CHANNEL_MASK[31:0];rsp_error_o=req_write_i;end
            32'h234:begin rsp_data_o=VALID_CHANNEL_MASK[63:32];rsp_error_o=req_write_i;end
            32'h238:begin rsp_data_o=VALID_CHANNEL_MASK[95:64];rsp_error_o=req_write_i;end
            32'h23c:begin rsp_data_o=VALID_CHANNEL_MASK[127:96];rsp_error_o=req_write_i;end
            32'h240:begin rsp_data_o=ACTIVE_CHANNELS;rsp_error_o=req_write_i;end
            32'h300:begin rsp_data_o={31'b0,beam_pending};rsp_error_o=req_write_i;end
            32'h304:begin rsp_data_o=delay_index;if(req_write_i)rsp_error_o=req_data_i>=128 || beam_pending;end
            32'h308:if(!req_write_i || req_data_i>32512 || beam_pending)rsp_error_o=1;
            32'h30c:if(!req_write_i || req_data_i!=1 || beam_pending)rsp_error_o=1;
            32'h310:begin rsp_data_o=beam_epoch;rsp_error_o=req_write_i;end
            32'h314:begin rsp_data_o=fault_count;rsp_error_o=req_write_i;end
            32'h400:begin rsp_data_o=count;rsp_error_o=req_write_i;end
            32'h404:begin rsp_data_o=drop_count;rsp_error_o=req_write_i;end
            32'h408:begin rsp_data_o=produced_count;rsp_error_o=req_write_i;end
            32'h40c:begin rsp_data_o=16000;rsp_error_o=req_write_i;end
            32'h410:begin rsp_data_o={{16{read_word[15]}},read_word[15:0]};rsp_error_o=req_write_i || !run_i || count==0;end
            32'h414:begin rsp_data_o=popped_frame[31:0];rsp_error_o=req_write_i;end
            32'h418:begin rsp_data_o=popped_frame[63:32];rsp_error_o=req_write_i;end
            32'h41c:begin rsp_data_o=popped_epoch;rsp_error_o=req_write_i;end
            default:rsp_error_o=1;
        endcase
        if(req_addr_i[1:0]!=0 || (req_write_i && req_strb_i!=15))rsp_error_o=1;
    end
    always @(posedge clk)begin
        if(push)fifo[wp]<={fir_epoch,fir_frame,fir_pcm};
        if(fetch)read_word<=fifo[rp];
    end
    always @(posedge clk or negedge rst_n)begin
        if(!rst_n)begin
            pair_index<=0;delay_index<=0;fault_count<=0;drop_count<=0;produced_count<=0;
            rp<=0;wp<=0;count<=0;read_pending<=0;popped_frame<=0;popped_epoch<=0;current_frame<=0;
        end else begin
            if(wr && off==32'h20c)pair_index<=req_data_i[5:0];
            if(wr && off==32'h304)delay_index<=req_data_i[6:0];
            if(wr && off==32'h308)delay_index<=delay_index+1'b1;
            if(pcm_fire_i && pcm_channel_i==127)current_frame<=pcm_frame_i;
            if(beam_fault || fir_fault)fault_count<=fault_count+1'b1;
            if(fetch)read_pending<=1;
            if(pop)begin rp<=rp+1'b1;read_pending<=0;popped_frame<=read_word[79:16];popped_epoch<=read_word[111:80];end
            if(push)wp<=wp+1'b1;
            case({push,pop})2'b10:count<=count+1'b1;2'b01:count<=count-1'b1;default:begin end endcase
            if(fir_valid && run_i)begin produced_count<=produced_count+1'b1;if(!push)drop_count<=drop_count+1'b1;end
            if(!run_i)begin count<=0;rp<=0;wp<=0;read_pending<=0;end
        end
    end
endmodule
`default_nettype wire
