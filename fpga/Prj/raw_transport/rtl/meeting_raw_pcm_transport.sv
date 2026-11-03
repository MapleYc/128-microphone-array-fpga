`default_nettype none
// Tap canonical CPU-domain PCM. Acquisition is never backpressured by this
// diagnostic stream. Overflow is sticky for the network session and marks
// all following M128 packets unusable for acoustic acceptance until restart.
module meeting_raw_pcm_transport #(
    parameter integer FIFO_ADDRESS_BITS=12
)(
    input wire src_clk,eth_clk,reset_n,
    input wire stream_enable,network_ready,
    input wire pcm_valid,input wire signed [23:0] pcm_data,
    input wire [6:0] pcm_channel,input wire [63:0] pcm_frame,
    input wire [3:0] source_status,
    output wire [7:0] payload_data,output wire payload_valid,
    input wire payload_ready,output wire payload_last,
    output wire [15:0] payload_length,
    output wire framing_error,
    output reg overflow_sticky,
    output reg [31:0] dropped_samples,
    output reg [31:0] stream_epoch
);
    // RUN is a source-side stop request, never an asynchronous packet abort.
    // A SEND packet already visible to the arbiter drains through real LAST;
    // only then do both FIFO domains/partial COLLECT flush before re-enabling.
    // Link loss is different: the MAC and arbiter abort together externally.
    wire whole_src_rst_n,whole_eth_rst_n;
    reset_release whole_source_reset(.clk(src_clk),.arst_n(reset_n),.rst_n(whole_src_rst_n));
    reset_release whole_network_reset(.clk(eth_clk),.arst_n(reset_n),.rst_n(whole_eth_rst_n));
    reg raw_active;
    localparam [1:0] OFF=0,RUNNING=1,DRAIN=2;
    reg [1:0] mode;
    (* syn_preserve=1 *) reg [1:0] enable_sync,stop_sync,active_sync;
    reg stop_request;
    wire packet_sending;
    always @(posedge src_clk or negedge whole_src_rst_n)begin
        if(!whole_src_rst_n)begin active_sync<=0;stop_request<=1;end
        else begin
            active_sync<={active_sync[0],raw_active};
            if(!stream_enable)stop_request<=1;
            else if(!active_sync[1])stop_request<=0;
        end
    end
    always @(posedge eth_clk or negedge whole_eth_rst_n)begin
        if(!whole_eth_rst_n)begin
            enable_sync<=0;stop_sync<=3;raw_active<=0;mode<=OFF;stream_epoch<=0;
        end else begin
            enable_sync<={enable_sync[0],stream_enable};
            stop_sync<={stop_sync[0],stop_request};
            if(!network_ready)begin raw_active<=0;mode<=OFF;end
            else case(mode)
                OFF: if(enable_sync[1] && !stop_sync[1])begin
                    raw_active<=1;stream_epoch<=stream_epoch+1'b1;mode<=RUNNING;
                end
                RUNNING: if(!enable_sync[1] || stop_sync[1])mode<=DRAIN;
                DRAIN: if(!packet_sending)begin raw_active<=0;mode<=OFF;end
                default:begin raw_active<=0;mode<=OFF;end
            endcase
        end
    end
    wire common_reset_n=reset_n && network_ready && raw_active;
    wire src_rst_n,eth_rst_n;
    reset_release source_reset(.clk(src_clk),.arst_n(common_reset_n),.rst_n(src_rst_n));
    reset_release network_reset(.clk(eth_clk),.arst_n(common_reset_n),.rst_n(eth_rst_n));
    wire ready,read_valid,read_ready;
    reg frame_aligned;
    wire accept_position=frame_aligned || pcm_channel==0;
    wire [100:0] read_data;
    meeting_raw_async_fifo #(.WIDTH(101),.ADDRESS_BITS(FIFO_ADDRESS_BITS)) samples(
        .wr_clk(src_clk),.wr_rst_n(src_rst_n),
        .wr_data({source_status,pcm_channel==0,pcm_channel==127,pcm_frame,pcm_channel,pcm_data}),
        .wr_valid(pcm_valid && accept_position && stream_enable && !stop_request),.wr_ready(ready),
        .rd_clk(eth_clk),.rd_rst_n(eth_rst_n),.rd_data(read_data),.rd_valid(read_valid),.rd_ready(read_ready));
    always @(posedge src_clk or negedge src_rst_n) begin
        if(!src_rst_n)begin overflow_sticky<=0;dropped_samples<=0;frame_aligned<=0;end
        else begin
            if(pcm_valid && pcm_channel==0)frame_aligned<=1;
            if(pcm_valid && accept_position && stream_enable && !stop_request && !ready)begin overflow_sticky<=1;dropped_samples<=dropped_samples+1'b1;end
        end
    end
    (* syn_preserve=1 *) reg [1:0] overflow_sync;
    always @(posedge eth_clk or negedge eth_rst_n) begin
        if(!eth_rst_n)overflow_sync<=0;else overflow_sync<={overflow_sync[0],overflow_sticky};
    end
    pcm_udp_packetizer packets(.clk(eth_clk),.rst_n(eth_rst_n),.stream_epoch(stream_epoch),
        .selftest_mode(1'b0),.selftest_error(1'b0),
        .sample_data(read_data[23:0]),.sample_channel(read_data[30:24]),.sample_frame_id(read_data[94:31]),
        .sample_first(read_data[96]),.sample_last(read_data[95]),.sample_status(read_data[100:97]|{1'b0,overflow_sync[1],2'b00}),
        .sample_valid(read_valid),.sample_ready(read_ready),
        .payload_data(payload_data),.payload_valid(payload_valid),.payload_ready(payload_ready),
        .payload_last(payload_last),.payload_length(payload_length),.framing_error(framing_error),.packet_accepted(),.packet_sending(packet_sending));
endmodule
`default_nettype wire
