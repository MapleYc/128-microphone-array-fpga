`timescale 1ns/1ps
`default_nettype none
// Mini200 D18 reference. Integer GPLL cascade from the pinned vendor reference:
// 27 * 16 * 2 / 9 = 96 MHz; 96 / 5 * 16 * 2 / 5 = 122.88 MHz.
// Only audio clocks are instantiated: no dependency on the 125 MHz oscillator.
module meeting_audio_clocks #(
    parameter integer POWER_ON_CYCLES=65536,
    parameter integer LOCK_STABLE_CYCLES=270000
)(
    input wire ref_clk_27, reset_n,
    output wire sys_clk, audio_clk, locked,
    output wire [1:0] locks
);
    wire ref_in, clk96_raw, clk96, audio_raw;
    wire lock96, lock_audio, audio_pll_release;
    GTP_INBUF #(.IOSTANDARD("LVCMOS33")) u_ref(.I(ref_clk_27),.O(ref_in));
    GTP_CLKBUFG u_ref_buf(.CLKIN(ref_in),.CLKOUT(sys_clk));
    // Use the always-running 27 MHz reference, not a PLL output, for startup.
    // The qualified reset below keeps the complete pipeline reset while both
    // PLL outputs run for another 10 ms after their LOCK signals become stable.
    localparam integer PW=POWER_ON_CYCLES<2 ? 1 : $clog2(POWER_ON_CYCLES);
    localparam integer LW=LOCK_STABLE_CYCLES<2 ? 1 : $clog2(LOCK_STABLE_CYCLES);
    reg [PW-1:0] power_count=0;
    reg power_ready=0;
    always @(posedge sys_clk or negedge reset_n) begin
        if(!reset_n)begin power_count<=0;power_ready<=0;end
        else if(!power_ready)begin
            if(power_count==POWER_ON_CYCLES-1)power_ready<=1;
            else power_count<=power_count+1'b1;
        end
    end
    wire pll_reset_n=reset_n && power_ready;
    pango_audio_gpll #(.INPUT_MHZ(27.0),.INPUT_DIV(1),.OUTPUT_DIV(9))
        u_pll96(.clk_in(ref_in),.rst(!pll_reset_n),.clk_out(clk96_raw),.locked(lock96));
    GTP_CLKBUFG u_96_buf(.CLKIN(clk96_raw),.CLKOUT(clk96));
    reset_release u_audio_pll_reset(.clk(clk96),.arst_n(pll_reset_n && lock96),.rst_n(audio_pll_release));
    pango_audio_gpll #(.INPUT_MHZ(96.0),.INPUT_DIV(5),.OUTPUT_DIV(5))
        u_audio_pll(.clk_in(clk96),.rst(!audio_pll_release),.clk_out(audio_raw),.locked(lock_audio));
    GTP_CLKBUFG u_audio_buf(.CLKIN(audio_raw),.CLKOUT(audio_clk));
    assign locks={lock_audio,lock96};
    wire raw_locked=pll_reset_n && lock96 && lock_audio;
    (* syn_preserve=1 *) reg [1:0] lock_release_pipe=0;
    reg [LW-1:0] stable_count=0;
    reg qualified=0;
    always @(posedge sys_clk or negedge raw_locked)begin
        if(!raw_locked)lock_release_pipe<=0;
        else lock_release_pipe<={lock_release_pipe[0],1'b1};
    end
    always @(posedge sys_clk or negedge raw_locked)begin
        if(!raw_locked)begin stable_count<=0;qualified<=0;end
        else if(!lock_release_pipe[1])begin stable_count<=0;qualified<=0;end
        else if(!qualified)begin
            if(stable_count==LOCK_STABLE_CYCLES-1)qualified<=1;
            else stable_count<=stable_count+1'b1;
        end
    end
    // Loss of either raw LOCK asserts reset immediately; release is qualified.
    assign locked=raw_locked && qualified;
    // synthesis translate_off
    initial if(POWER_ON_CYCLES<1 || LOCK_STABLE_CYCLES<1)
        $fatal(1,"Invalid audio startup qualification cycles");
    // synthesis translate_on
endmodule
`default_nettype wire
