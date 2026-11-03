`default_nettype none
// Non-backpressuring canonical-PCM tap. Fixed equal-weight broadside is NOT a
// steerable beamformer. Input is signed Q1.23 at 48 kHz per channel.
module array128_mono_stream_mmio #(
    parameter [31:0] BASE_ADDR=32'h20006000,
    parameter integer FIFO_DEPTH=1024,
    parameter integer DECIMATE_16K=0
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
    output wire irq_o
);
    localparam integer PW=$clog2(FIFO_DEPTH),CW=$clog2(FIFO_DEPTH+1);
    localparam [31:0] CONFIG_MASK=32'h000f7f03;
    reg [31:0] requested_config,active_config,epoch;
    reg [31:0] dropped_count,saturation_count,protocol_count,produced_count;
    reg [1:0] irq_enable;
    reg overflow_sticky,protocol_sticky,active,collecting,read_pending;
    reg [7:0] expected_channel;
    reg [63:0] held_frame,last_frame;
    reg [31:0] last_epoch;
    reg signed [31:0] sum;
    reg signed [23:0] selected;
    reg [PW-1:0] wr_ptr,rd_ptr;
    reg [CW-1:0] count;
    // Registered read permits block RAM inference. Metadata belongs to every
    // stored word, so a producer gap or configuration epoch is never hidden.
    reg [111:0] memory[0:FIFO_DEPTH-1];
    reg [111:0] read_word;
    wire [31:0] offset=req_addr_i-BASE_ADDR;
    wire config_write=req_valid_i && req_write_i && offset==32'h104 &&
        req_strb_i==15 && (req_data_i & ~CONFIG_MASK)==0 && req_data_i[19:16]<=8 && req_addr_i[1:0]==0;
    wire immediate_stop=!run_i || !requested_config[0] || (config_write && !req_data_i[0]);
    wire first=pcm_fire_i && pcm_first_i && pcm_channel_i==0 && !pcm_last_i;
    wire apply_config=!immediate_stop && first && (!active || requested_config!=active_config);
    wire flush=immediate_stop || apply_config;
    wire [31:0] frame_config=apply_config ? requested_config : active_config;
    wire data_read=!req_write_i && offset==32'h11c && count!=0 && !flush;
    assign req_ready_o=rst_n && (!data_read || read_pending);
    wire accepted=req_valid_i && req_ready_o && !rsp_error_o;
    wire pop=accepted && data_read;
    wire fetch=req_valid_i && data_read && !read_pending && !rsp_error_o;
    wire next_ok=collecting && pcm_channel_i==expected_channel[6:0] &&
        pcm_frame_i==held_frame && !pcm_first_i && pcm_last_i==(expected_channel==127);
    wire finish=pcm_fire_i && next_ok && pcm_last_i && active && !flush;
    wire signed [31:0] next_sum=sum+{{8{pcm_data_i[23]}},pcm_data_i};
    wire signed [23:0] chosen=(pcm_channel_i==active_config[14:8]) ? pcm_data_i : selected;
    // Separate signed operands: an unsigned concatenation in a ternary would
    // otherwise make >>> a logical shift before assignment to a signed wire.
    wire signed [31:0] average_value=next_sum >>> 7;
    wire signed [31:0] selected_value={{8{chosen[23]}},chosen};
    wire signed [31:0] q23_value=active_config[1] ? average_value : selected_value;
    wire signed [31:0] shifted=q23_value >>> active_config[19:16];
    wire saturated=shifted>32767 || shifted< -32768;
    wire [15:0] pcm16=shifted>32767 ? 16'h7fff : shifted< -32768 ? 16'h8000 : shifted[15:0];
    wire emit_valid,filter_fault;
    wire [15:0] emit_pcm;
    wire [63:0] emit_frame;
    wire [31:0] emit_epoch;
    generate if(DECIMATE_16K!=0) begin:g_resample
        meeting_mono_fir fir(.clk(clk),.rst_n(rst_n),.flush_i(flush),
            .in_valid_i(finish),.in_pcm_i(pcm16),.in_frame_i(held_frame),.in_epoch_i(epoch),
            .ready_o(),.out_valid_o(emit_valid),.out_pcm_o(emit_pcm),
            .out_frame_o(emit_frame),.out_epoch_o(emit_epoch),.fault_o(filter_fault));
    end else begin:g_raw
        assign emit_valid=finish;
        assign emit_pcm=pcm16;
        assign emit_frame=held_frame;
        assign emit_epoch=epoch;
        assign filter_fault=1'b0;
    end endgenerate
    wire push=emit_valid && !flush && (count<FIFO_DEPTH || pop);
    assign irq_o=rst_n && ((irq_enable[0] && count!=0) ||
        (irq_enable[1] && (overflow_sticky || protocol_sticky)));

    function automatic [31:0] increment(input [31:0] value);
        increment=(&value) ? value : value+1'b1;
    endfunction
    always @* begin
        rsp_data_o=0;rsp_error_o=0;
        case(offset)
            32'h100:begin rsp_data_o=DECIMATE_16K!=0 ? 32'h4d128002 : 32'h4d128001;rsp_error_o=req_write_i;end
            32'h104:begin
                rsp_data_o=requested_config;
                if(req_write_i) rsp_error_o=req_strb_i!=15 || (req_data_i & ~CONFIG_MASK)!=0 || req_data_i[19:16]>8;
            end
            32'h108:begin rsp_data_o=active_config;rsp_error_o=req_write_i;end
            32'h10c:begin rsp_data_o={28'b0,protocol_sticky,overflow_sticky,
                (requested_config[0] && (requested_config!=active_config || !active)),(active && !immediate_stop)};rsp_error_o=req_write_i;end
            32'h110:begin rsp_data_o=count;rsp_error_o=req_write_i;end
            32'h114:begin rsp_data_o=FIFO_DEPTH;rsp_error_o=req_write_i;end
            32'h118:begin rsp_data_o=DECIMATE_16K!=0 ? 16000 : 48000;rsp_error_o=req_write_i;end
            32'h11c:begin rsp_data_o={{16{read_word[15]}},read_word[15:0]};rsp_error_o=req_write_i || count==0 || flush;end
            32'h120:begin rsp_data_o=last_frame[31:0];rsp_error_o=req_write_i;end
            32'h124:begin rsp_data_o=last_frame[63:32];rsp_error_o=req_write_i;end
            32'h128:begin rsp_data_o=last_epoch;rsp_error_o=req_write_i;end
            32'h12c:begin rsp_data_o=epoch;rsp_error_o=req_write_i;end
            32'h130:begin rsp_data_o=dropped_count;rsp_error_o=req_write_i;end
            32'h134:begin rsp_data_o=saturation_count;rsp_error_o=req_write_i;end
            32'h138:begin rsp_data_o=protocol_count;rsp_error_o=req_write_i;end
            32'h13c:begin rsp_data_o=produced_count;rsp_error_o=req_write_i;end
            32'h140:begin rsp_data_o={30'b0,irq_enable};if(req_write_i) rsp_error_o=req_strb_i!=15 || |req_data_i[31:2];end
            default:rsp_error_o=1;
        endcase
        if(req_addr_i[1:0]!=0) begin rsp_data_o=0;rsp_error_o=1;end
    end
    always @(posedge clk) begin
        if(push) memory[wr_ptr]<={emit_epoch,emit_frame,emit_pcm};
        if(fetch) read_word<=memory[rd_ptr];
    end
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            requested_config<=32'h00080000;active_config<=32'h00080000;epoch<=0;
            dropped_count<=0;saturation_count<=0;protocol_count<=0;produced_count<=0;
            irq_enable<=0;overflow_sticky<=0;protocol_sticky<=0;active<=0;collecting<=0;read_pending<=0;
            expected_channel<=0;held_frame<=0;last_frame<=0;last_epoch<=0;sum<=0;selected<=0;
            wr_ptr<=0;rd_ptr<=0;count<=0;
        end else begin
            if(config_write) requested_config<=req_data_i;
            if(accepted && req_write_i && offset==32'h140) irq_enable<=req_data_i[1:0];
            if(fetch) read_pending<=1;
            if(pop) begin
                read_pending<=0;rd_ptr<=rd_ptr+1'b1;
                last_frame<=read_word[79:16];last_epoch<=read_word[111:80];
            end
            if(push) wr_ptr<=wr_ptr+1'b1;
            case({push,pop})
                2'b10:count<=count+1'b1;
                2'b01:count<=count-1'b1;
                default:begin end
            endcase
            if(emit_valid && !flush) begin
                produced_count<=increment(produced_count);
                if(!push) begin dropped_count<=increment(dropped_count);overflow_sticky<=1;end
            end
            if(finish && saturated) saturation_count<=increment(saturation_count);
            if(filter_fault) begin protocol_count<=increment(protocol_count);protocol_sticky<=1;end
            if(pcm_fire_i && active && !immediate_stop) begin
                if(first) begin
                    if(collecting) begin protocol_count<=increment(protocol_count);protocol_sticky<=1;end
                    collecting<=1;expected_channel<=1;held_frame<=pcm_frame_i;
                    sum<={{8{pcm_data_i[23]}},pcm_data_i};
                    selected<=frame_config[14:8]==0 ? pcm_data_i : 24'sd0;
                end else if(collecting) begin
                    if(!next_ok) begin collecting<=0;protocol_count<=increment(protocol_count);protocol_sticky<=1;end
                    else begin
                        sum<=next_sum;
                        if(pcm_channel_i==active_config[14:8]) selected<=pcm_data_i;
                        if(pcm_last_i) collecting<=0;else expected_channel<=expected_channel+1'b1;
                    end
                end
            end
            if(flush) begin
                count<=0;wr_ptr<=0;rd_ptr<=0;read_pending<=0;collecting<=0;
                if(immediate_stop) begin active<=0;active_config<=active_config & 32'hfffffffe;end
                else begin
                    active<=1;active_config<=requested_config;epoch<=epoch+1'b1;
                    collecting<=1;expected_channel<=1;held_frame<=pcm_frame_i;
                    sum<={{8{pcm_data_i[23]}},pcm_data_i};
                    selected<=requested_config[14:8]==0 ? pcm_data_i : 24'sd0;
                end
            end
        end
    end
`ifndef SYNTHESIS
    initial if(FIFO_DEPTH<2 || (FIFO_DEPTH & (FIFO_DEPTH-1))!=0) $fatal(1,"mono FIFO_DEPTH must be power of two >=2");
`endif
endmodule
`default_nettype wire
