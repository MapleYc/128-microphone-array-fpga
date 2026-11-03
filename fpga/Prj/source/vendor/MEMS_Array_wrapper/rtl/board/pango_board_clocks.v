`timescale 1ns/1ps
// PG2L200H clock resources. No fabric clock gating or fractional divider is used.
// Board references: D18 = 27 MHz; N3/N2 = 125 MHz SSTL15D_I differential input.
module pango_board_clocks (
    input  wire ref_clk_27,
    input  wire ref_clk_125_p,
    input  wire ref_clk_125_n,
    input  wire rst_n,
    output wire audio_clk,
    output wire eth_clk,
    output wire locked,
    output wire monitor_ref_clk, monitor_raw125_clk, monitor_clk96,
    output wire [2:0] monitor_locks
);
    wire clk_27_ibuf, clk_125_ibuf, clk_96_raw, clk_96, audio_raw;
    wire eth_250_raw, eth_divided;
    wire lock_96, lock_audio, lock_eth;
    reg [2:0] release_audio /* synthesis syn_preserve=1 */;

    GTP_INBUF #(.IOSTANDARD("LVCMOS33")) u_ref27 (
        .I(ref_clk_27), .O(clk_27_ibuf)
    );
    GTP_INBUFDS #(.IOSTANDARD("SSTL15D_I"), .TERM_DIFF("ON")) u_ref125 (
        .I(ref_clk_125_p), .IB(ref_clk_125_n), .O(clk_125_ibuf)
    );
    // The board oscillator guarantees only 45..55% duty. Generate 250 MHz
    // then divide on consecutive rising edges so the 125 MHz duty no longer
    // copies the oscillator (or PLL output) high/low asymmetry.
    // PFD=125/4=31.25 MHz, valid for OPTIMIZED bandwidth (10..40 MHz).
    // 125 / 4 * 16 * 2 = 1000 MHz VCO; 1000 / 4 = 250 MHz.
    pango_audio_gpll #(.INPUT_MHZ(125.0), .INPUT_DIV(4),
                      .FEEDBACK_MULTIPLY(16), .OUTPUT_DIV(4))
        u_pll_eth (.clk_in(clk_125_ibuf), .rst(~rst_n),
                   .clk_out(eth_250_raw), .locked(lock_eth));
    // This primitive has asynchronous reset assertion and internal synchronous
    // release. Its DIV=2 mode uses rising edges only (Logos2 Clock UG section2.4).
    GTP_IOCLKDIV_E2 #(.DIV_FACTOR("2")) u_eth_div (
        .CLKIN(eth_250_raw), .RST_N(rst_n & lock_eth), .CE(1'b1),
        .CLKDIVOUT(eth_divided));
    GTP_CLKBUFG u_eth_buf (.CLKIN(eth_divided), .CLKOUT(eth_clk));

    // 27 / 1 * 16 * 2 = 864 MHz VCO; 864 / 9 = 96 MHz.
    // FDIV=2 keeps the feedback duty-cycle setting within the documented range.
    pango_audio_gpll #(.INPUT_MHZ(27.0), .INPUT_DIV(1), .OUTPUT_DIV(9))
        u_pll_96 (.clk_in(clk_27_ibuf), .rst(~rst_n),
                  .clk_out(clk_96_raw), .locked(lock_96));
    GTP_CLKBUFG u_96_buf (.CLKIN(clk_96_raw), .CLKOUT(clk_96));

    // Assert immediately when the first PLL unlocks; release on its stable clock.
    always @(posedge clk_96 or negedge rst_n or negedge lock_96)
        if (!rst_n || !lock_96) release_audio <= 3'b000;
        else release_audio <= {release_audio[1:0], 1'b1};

    // 96 / 5 * 16 * 2 = 614.4 MHz VCO; 614.4 / 5 = 122.88 MHz.
    // Both PFD rates (27 and 19.2 MHz) and VCO rates obey UG040004/DS04001.
    pango_audio_gpll #(.INPUT_MHZ(96.0), .INPUT_DIV(5), .OUTPUT_DIV(5))
        u_pll_audio (.clk_in(clk_96), .rst(~release_audio[2]),
                     .clk_out(audio_raw), .locked(lock_audio));
    GTP_CLKBUFG u_audio_buf (.CLKIN(audio_raw), .CLKOUT(audio_clk));
    assign locked = rst_n & lock_96 & lock_audio & lock_eth;
    // Independent diagnostic taps; PLL outputs/reset sequencing unchanged; Ethernet IDIV/MDIV corrected.
    GTP_CLKBUFG u_monitor_ref (.CLKIN(clk_27_ibuf), .CLKOUT(monitor_ref_clk));
    GTP_CLKBUFG u_monitor_raw125 (.CLKIN(clk_125_ibuf), .CLKOUT(monitor_raw125_clk));
    assign monitor_clk96 = clk_96;
    assign monitor_locks = {lock_eth, lock_audio, lock_96};
endmodule

// Direct instantiation of the device primitive keeps synthesis and vendor-model
// simulation on the same implementation. Unused configuration inputs are tied.
module pango_audio_gpll #(
    parameter real INPUT_MHZ = 27.0,
    parameter integer INPUT_DIV = 1,
    parameter integer FEEDBACK_MULTIPLY = 16,
    parameter integer OUTPUT_DIV = 9
) (
    input wire clk_in,
    input wire rst,
    output wire clk_out,
    output wire locked
);
    GTP_GPLL #(
        .CLKIN_FREQ(INPUT_MHZ), .LOCK_MODE(1'b0),
        .STATIC_RATIOI(INPUT_DIV), .STATIC_RATIOM(FEEDBACK_MULTIPLY),
        .STATIC_RATIOF(2.0), .STATIC_DUTYF(2),
        .STATIC_RATIO0(OUTPUT_DIV), .STATIC_DUTY0(OUTPUT_DIV),
        .STATIC_RATIO1(8), .STATIC_DUTY1(8),
        .STATIC_RATIO2(8), .STATIC_DUTY2(8),
        .STATIC_RATIO3(8), .STATIC_DUTY3(8),
        .STATIC_RATIO4(8), .STATIC_DUTY4(8),
        .STATIC_RATIO5(8), .STATIC_DUTY5(8),
        .STATIC_RATIO6(8), .STATIC_DUTY6(8),
        .INTERNAL_FB("CLKOUTF"), .EXTERNAL_FB("DISABLE"),
        .BANDWIDTH("OPTIMIZED"), .SSC_MODE("DISABLE")
    ) u_gpll (
        .CLKIN1(clk_in), .CLKIN2(1'b0), .CLKIN_SEL(1'b0), .CLKFB(1'b0),
        .CLKOUT0(clk_out), .CLKOUT0N(), .CLKOUT1(), .CLKOUT1N(),
        .CLKOUT2(), .CLKOUT2N(), .CLKOUT3(), .CLKOUT3N(), .CLKOUT4(),
        .CLKOUT5(), .CLKOUT6(), .CLKOUTF(), .CLKOUTFN(), .DPS_DONE(),
        .APB_READY(), .APB_RDATA(), .LOCK(locked), .RST(rst), .PLL_PWD(1'b0),
        .DPS_CLK(1'b0), .DPS_EN(1'b0), .DPS_DIR(1'b0),
        .CLKOUT0_SYN(1'b0), .CLKOUT1_SYN(1'b0), .CLKOUT2_SYN(1'b0),
        .CLKOUT3_SYN(1'b0), .CLKOUT4_SYN(1'b0), .CLKOUT5_SYN(1'b0),
        .CLKOUT6_SYN(1'b0), .CLKOUTF_SYN(1'b0),
        .APB_CLK(1'b0), .APB_RST_N(1'b0), .APB_ADDR(5'b0),
        .APB_SEL(1'b0), .APB_EN(1'b0), .APB_WRITE(1'b0), .APB_WDATA(16'b0)
    );
endmodule
