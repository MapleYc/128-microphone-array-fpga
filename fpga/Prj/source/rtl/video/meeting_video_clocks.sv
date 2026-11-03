`timescale 1ns/1ps
`default_nettype none
// Same Ethernet PLL/divider contract as the pinned MEMS_Array_wrapper.
// Successive 250 MHz rising edges establish both halves of the 125 MHz
// output; do not propagate the oscillator's 45..55% duty range to RGMII.
module meeting_video_clocks(
    input wire ref125,reset_n,
    output wire eth_clk,locked
);
    wire eth250,divided;
    pango_audio_gpll #(.INPUT_MHZ(125.0),.INPUT_DIV(4),
        .FEEDBACK_MULTIPLY(16),.OUTPUT_DIV(4))
        u_pll_eth(.clk_in(ref125),.rst(!reset_n),.clk_out(eth250),.locked(locked));
    GTP_IOCLKDIV_E2 #(.DIV_FACTOR("2")) u_eth_div(
        .CLKIN(eth250),.RST_N(reset_n && locked),.CE(1'b1),.CLKDIVOUT(divided));
    GTP_CLKBUFG u_eth_buf(.CLKIN(divided),.CLKOUT(eth_clk));
endmodule
`default_nettype wire
