`timescale 1ns/1ps
// RGMII 1 Gb/s pin interface for Logos2. RTL8211F must enable RX/TX internal
// delay through MDIO. A regional RX clock and a small input deskew compensate
// the FPGA's own clock insertion; they do not replace the PHY's RGMII delay.
module pango_rgmii_io #(parameter TX_ASYNC_RESET=1) (
    input  wire       tx_clk,
    input  wire       rst_n,
    input  wire [7:0] tx_data,
    input  wire       tx_en,
    input  wire       tx_er,
    input  wire       rgmii_rxc,
    input  wire       rgmii_rx_ctl,
    input  wire [3:0] rgmii_rxd,
    output wire       rx_clk,
    output wire [7:0] rx_data,
    output wire       rx_dv,
    output wire       rx_er,
    output wire       rgmii_txc,
    output wire       rgmii_tx_ctl,
    output wire [3:0] rgmii_txd
);
    wire rxc_in, rxctl_in, rxctl_delayed, txclk_out, txctl_out;
    wire [3:0] rxd_in, rxd_delayed, txd_out;
    wire rx_control_falling;
    reg [2:0] tx_release, rx_release /* synthesis syn_preserve=1 */;
    wire tx_reset = ~tx_release[2];
    // AV mode drives an idle control nibble through the clocked D input.
    // TX clock is free-running and the PHY remains reset during startup.
    // Avoid an asynchronous fabric-to-IOL reset with a half-cycle recovery
    // requirement. RX retains its existing local reset synchronizer.
    wire tx_iol_reset = TX_ASYNC_RESET ? tx_reset : 1'b0;
    wire tx_valid = tx_en && !tx_reset;
    wire tx_error = tx_er && !tx_reset;
    wire rx_reset = ~rx_release[2];

    always @(posedge tx_clk or negedge rst_n)
        if (!rst_n) tx_release <= 3'b0;
        else tx_release <= {tx_release[1:0], 1'b1};
    always @(posedge rx_clk or negedge rst_n)
        if (!rst_n) rx_release <= 3'b0;
        else rx_release <= {rx_release[1:0], 1'b1};

    GTP_INBUF #(.IOSTANDARD("LVCMOS33")) u_rxc (.I(rgmii_rxc), .O(rxc_in));
    // IDDR and the entire RX parser share this regional clock. Splitting the
    // IDDR and parser between regional/global trees would create another skew.
    GTP_CLKBUFR u_rxclk (.CLKIN(rxc_in), .CLKOUT(rx_clk));
    GTP_INBUF #(.IOSTANDARD("LVCMOS33")) u_rxctl (.I(rgmii_rx_ctl), .O(rxctl_in));
    // Static DELAY_STEP_VALUE is Gray coded: binary18 -> Gray0x1B. The final
    // routed multi-corner report, not nominal 10ps/tap, determines acceptance.
    GTP_IODELAY_E2 #(.DELAY_STEP_SEL("PARAMETER"),
        .DELAY_STEP_VALUE(8'h1B), .TDELAY_EN("FALSE")) u_rxctl_delay
        (.DI(rxctl_in), .DO(rxctl_delayed), .DELAY_SEL(1'b1),
         .DELAY_STEP(8'b0), .EN_N(1'b0));

    // SAME_EDGE accepts an entire GMII byte on one rising edge; D0 is low nibble.
    // Forwarded clock is free-running, including while the PHY is in reset.
    GTP_ODDR_E1 #(.ODDR_MODE("SAME_EDGE"), .GRS_EN("TRUE"), .RS_TYPE("ASYNC_RESET"))
        u_txc_ddr (.D0(1'b1), .D1(1'b0), .CLK(tx_clk), .CE(1'b1), .RS(1'b0), .Q(txclk_out));
    GTP_OUTBUF #(.IOSTANDARD("LVCMOS33"), .SLEW_RATE("FAST"), .DRIVE_STRENGTH("16"))
        u_txc (.I(txclk_out), .O(rgmii_txc));
    GTP_ODDR_E1 #(.ODDR_MODE("SAME_EDGE"), .GRS_EN("TRUE"), .RS_TYPE("ASYNC_RESET"))
        u_txctl_ddr (.D0(tx_valid), .D1(tx_valid ^ tx_error), .CLK(tx_clk), .CE(1'b1), .RS(tx_iol_reset), .Q(txctl_out));
    GTP_OUTBUF #(.IOSTANDARD("LVCMOS33"), .SLEW_RATE("FAST"), .DRIVE_STRENGTH("16"))
        u_txctl (.I(txctl_out), .O(rgmii_tx_ctl));
    GTP_IDDR_E1 #(.IDDR_MODE("SAME_PIPELINED"), .GRS_EN("TRUE"), .RS_TYPE("ASYNC_RESET"))
        u_rxctl_ddr (.D(rxctl_delayed), .CLK(rx_clk), .CE(1'b1), .RS(rx_reset),
                     .Q0(rx_dv), .Q1(rx_control_falling));
    assign rx_er = rx_dv ^ rx_control_falling;

    genvar i;
    generate for (i=0; i<4; i=i+1) begin : g_data
        GTP_ODDR_E1 #(.ODDR_MODE("SAME_EDGE"), .GRS_EN("TRUE"), .RS_TYPE("ASYNC_RESET"))
            u_tx_ddr (.D0(tx_data[i]), .D1(tx_data[i+4]), .CLK(tx_clk), .CE(1'b1), .RS(tx_iol_reset), .Q(txd_out[i]));
        GTP_OUTBUF #(.IOSTANDARD("LVCMOS33"), .SLEW_RATE("FAST"), .DRIVE_STRENGTH("16"))
            u_tx (.I(txd_out[i]), .O(rgmii_txd[i]));
        GTP_INBUF #(.IOSTANDARD("LVCMOS33")) u_rx (.I(rgmii_rxd[i]), .O(rxd_in[i]));
        GTP_IODELAY_E2 #(.DELAY_STEP_SEL("PARAMETER"),
            .DELAY_STEP_VALUE(8'h1B), .TDELAY_EN("FALSE")) u_delay
            (.DI(rxd_in[i]), .DO(rxd_delayed[i]), .DELAY_SEL(1'b1),
             .DELAY_STEP(8'b0), .EN_N(1'b0));
        GTP_IDDR_E1 #(.IDDR_MODE("SAME_PIPELINED"), .GRS_EN("TRUE"), .RS_TYPE("ASYNC_RESET"))
            u_rx_ddr (.D(rxd_delayed[i]), .CLK(rx_clk), .CE(1'b1), .RS(rx_reset),
                      .Q0(rx_data[i]), .Q1(rx_data[i+4]));
    end endgenerate
endmodule
