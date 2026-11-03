`default_nettype none
// Full pinned PDM/CIC5/FIR63/FIR127 front end, CPU-clock canonical PCM stream.
// Clock source, physical input phase/voltage and pad constraints are external.
module array128_full_frontend #(
    parameter integer STARTUP_CYCLES=153600,SETTLE_FRAMES=96,
    parameter integer DC_ESTIMATE_LOG2=0,FIFO_ADDRESS_BITS=9
)(
    input wire audio_clk,sys_clk,rst_n,run_i,
    input wire [63:0] pdm_data_i,
    output wire [7:0] mic_clk_o,
    output wire pcm_valid_o,pcm_first_o,pcm_last_o,
    output wire [6:0] pcm_channel_o,
    output wire [63:0] pcm_frame_o,
    output wire signed [23:0] pcm_data_o,
    input wire pcm_ready_i,
    output wire diag_valid_o,
    output wire [31:0] dropped_frames_o,
    output wire [4:0] status_o,
    output wire protocol_error_o,
    output wire [31:0] published_frames_o
);
    wire global_audio_reset_n,wr_reset_n,rd_reset_n;
    reset_release u_global_audio_reset(.clk(audio_clk),.arst_n(rst_n),.rst_n(global_audio_reset_n));
    // RUN deassertion flushes both FIFO domains together. Release is local.
    wire stream_arst_n=rst_n && run_i;
    reset_release u_write_reset(.clk(audio_clk),.arst_n(stream_arst_n),.rst_n(wr_reset_n));
    reset_release u_read_reset(.clk(sys_clk),.arst_n(stream_arst_n),.rst_n(rd_reset_n));
    wire raw_valid,raw_first,raw_last,raw_ready,started,overflow,processing_error,clipped,dc_ready,mic_clock;
    wire [6:0] raw_channel;
    wire [63:0] raw_frame;
    wire signed [23:0] raw_data;
    wire [31:0] dropped;
    mems_array_top #(.STARTUP_CYCLES(STARTUP_CYCLES),.SETTLE_FRAMES(SETTLE_FRAMES),
        .DC_ESTIMATE_LOG2(DC_ESTIMATE_LOG2),.FIR_LANES(8)) u_mems(
        .clk(audio_clk),.rst_n(global_audio_reset_n),.enable(wr_reset_n),.pdm_data(pdm_data_i),
        .mic_clk(mic_clock),.started(started),.cfg_valid(1'b0),.cfg_channel(7'd0),
        .cfg_gain(24'sd4194304),.cfg_offset(32'sd0),.cfg_ready(),
        .pcm_data(raw_data),.pcm_channel(raw_channel),.pcm_frame_id(raw_frame),
        .pcm_valid(raw_valid),.pcm_first(raw_first),.pcm_last(raw_last),.pcm_ready(raw_ready),
        .overflow(overflow),.dropped_frames(dropped),.processing_error(processing_error),.clipped(clipped),
        .parity_cfg_valid(1'b0),.parity_even_gain(24'd4194304),.parity_odd_gain(24'd4194304),
        .parity_cfg_ready(),.parity_bypass(1'b0),.parity_active_even_gain(),.parity_active_odd_gain(),
        .dc_ready(dc_ready),.dc_rejected());
    // Mirror pdm_capture's fixed CLOCK_DIV=40 phase, without an output LUT.
    // Both reset releases are local to audio_clk. Assertion is asynchronous;
    // deassertion is sampled on the same next edge as the unchanged capture.
    wire mic_bank_reset_n=global_audio_reset_n && wr_reset_n;
    reg [5:0] mic_clock_phase;
    (* syn_preserve=1 *) reg [7:0] mic_clock_bank;
    always @(posedge audio_clk or negedge mic_bank_reset_n) begin
        if (!mic_bank_reset_n) begin
            mic_clock_phase <= 6'd0;
            mic_clock_bank <= 8'h00;
        end else begin
            mic_clock_phase <= (mic_clock_phase == 6'd39) ? 6'd0 : mic_clock_phase + 1'b1;
            if (mic_clock_phase == 6'd0) mic_clock_bank <= 8'hff;
            if (mic_clock_phase == 6'd20) mic_clock_bank <= 8'h00;
        end
    end
    assign mic_clk_o=mic_clock_bank;
    array128_pcm_cdc_reorder #(.FIFO_ADDRESS_BITS(FIFO_ADDRESS_BITS)) u_bridge(
        .src_clk(audio_clk),.src_rst_n(wr_reset_n),.dst_clk(sys_clk),.dst_rst_n(rd_reset_n),
        .src_valid_i(raw_valid),.src_first_i(raw_first),.src_last_i(raw_last),.src_channel_i(raw_channel),
        .src_frame_i(raw_frame),.src_data_i(raw_data),.src_ready_o(raw_ready),
        .dst_valid_o(pcm_valid_o),.dst_first_o(pcm_first_o),.dst_last_o(pcm_last_o),.dst_channel_o(pcm_channel_o),
        .dst_frame_o(pcm_frame_o),.dst_data_o(pcm_data_o),.dst_ready_i(pcm_ready_i),
        .protocol_error_o(protocol_error_o),.published_frames_o(published_frames_o));
    wire [36:0] diagnostic;
    wire diag_busy;
    diag_snapshot_cdc #(.WIDTH(37)) u_diagnostics(
        .dst_clk(sys_clk),.dst_rst_n(stream_arst_n),.launch(!diag_busy),.src_clk(audio_clk),
        .src_data({dropped,dc_ready,clipped,processing_error,overflow,started}),
        .data(diagnostic),.valid(diag_valid_o),.busy(diag_busy));
    assign dropped_frames_o=diagnostic[36:5];
    assign status_o=diagnostic[4:0];
endmodule
`default_nettype wire
