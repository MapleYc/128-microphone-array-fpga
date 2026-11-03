`timescale 1ns/1ps
// RTL8211F-CG startup and link supervisor. All state is in clk domain.
// Datasheet Rev.1.4: reset >=10 ms, wait >=50 ms after reset release (§7.17).
// This module assumes clk is stable and board supplies/25 MHz PHY crystal are
// ready before rst_n rises. Production defaults must not be shortened.
//
// Register sources:
// - RTL8211F(I)-CG/RTL8211FD(I)-CG Rev1.4, §7.11, §7.17, tables 22/24/25/
//   26/31/33/34/39/41/51: MDIO, reset, BMCR, PHY IDs, ANAR, GBCR, MMD,
//   PHYSR, page select and EEE advertisement (MMD7, address0x3c).
// - Linux drivers/net/phy/realtek/realtek_main.c, rtl8211f_config_rgmii_delay:
//   page 0xd08, TX 0x11 bit8 / RX 0x15 bit3 enable the internal 2 ns delays.
//   These bits are not documented by the public Rev1.4 register tables.
//   https://github.com/torvalds/linux/blob/master/drivers/net/phy/realtek/realtek_main.c
// Both delays are required with the board's FPGA edge-aligned RGMII pins.
// Unknown/reserved bits are preserved by RMW; known configuration is read back.
// Address zero is a Realtek broadcast alias: scan it but prefer a nonzero ID
// match. RTL8211F-CG is matched exactly (0x001cc916), not the other 8211 models.
module rtl8211f_manager #(
    parameter integer CLK_HZ = 125000000,
    parameter integer MDC_HZ = 1000000,
    parameter integer RESET_CYCLES = CLK_HZ/50,
    parameter integer SETTLE_CYCLES = CLK_HZ/20,
    parameter integer POLL_CYCLES = CLK_HZ/100,
    parameter integer RETRY_CYCLES = CLK_HZ
) (
    input wire clk,
    input wire rst_n,
    output reg phy_rst_n,
    output wire mdc,
    output wire mdio_out,
    output wire mdio_oe,
    input wire mdio_in,
    output reg ready,
    output reg link_up,
    output reg fault,
    output reg [4:0] phy_address,
    output wire [287:0] debug_status
);
    localparam [3:0] S_RESET=0, S_SETTLE=1, S_ID1_REQ=2, S_ID1_WAIT=3,
        S_ID2_REQ=4, S_ID2_WAIT=5, S_SCAN_NEXT=6, S_CFG_REQ=7,
        S_CFG_WAIT=8, S_POLL_DELAY=9, S_POLL_REQ=10, S_POLL_WAIT=11,
        S_RETRY=12;
    reg [3:0] state;
    reg [31:0] timer;
    reg zero_match;
    reg [4:0] cfg_step;
    reg [15:0] rmw_value;
    wire transaction_done, transaction_ack;
    wire [15:0] transaction_data;
    reg transaction_start, transaction_read;
    reg [4:0] transaction_reg;
    reg [15:0] transaction_write;
    reg check_enable;
    reg [15:0] check_mask, check_expected;

    // Read-only diagnostics survive automatic PHY retries, but clear on the
    // system reset. Read ACK counts exclude writes (Clause22 has no write ACK).
    reg [15:0] debug_id1,debug_id2,debug_phystatus;
    reg [31:0] debug_last_meta,debug_last_data,debug_transactions,debug_noacks;
    reg [31:0] debug_retries,debug_failure;
    wire cfg_failed=state==S_CFG_WAIT && transaction_done && transaction_read &&
        (!transaction_ack || (check_enable && ((transaction_data & check_mask)!=check_expected)));
    wire poll_failed=state==S_POLL_WAIT && transaction_done && !transaction_ack;
    wire scan_failed=state==S_SCAN_NEXT && phy_address==31 && !zero_match;
    assign debug_status={16'b0,debug_phystatus,debug_failure,debug_retries,debug_noacks,
        debug_transactions,debug_last_data,debug_last_meta,{debug_id1,debug_id2},
        {14'b0,phy_rst_n,fault,link_up,ready,phy_address,cfg_step,state}};
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            debug_id1<=0;debug_id2<=0;debug_phystatus<=0;debug_last_meta<=0;
            debug_last_data<=0;debug_transactions<=0;debug_noacks<=0;
            debug_retries<=0;debug_failure<=0;
        end else begin
            if(transaction_done) begin
                debug_transactions<=debug_transactions+1'b1;
                debug_last_meta<={11'b0,transaction_read,transaction_ack,phy_address,transaction_reg,cfg_step,state};
                debug_last_data<={transaction_write,transaction_data};
                if(transaction_read && !transaction_ack)debug_noacks<=debug_noacks+1'b1;
                if(state==S_ID1_WAIT && transaction_ack)debug_id1<=transaction_data;
                if(state==S_ID2_WAIT && transaction_ack)debug_id2<=transaction_data;
                if(state==S_POLL_WAIT && transaction_ack)debug_phystatus<=transaction_data;
            end
            if(cfg_failed || poll_failed || scan_failed) begin
                debug_retries<=debug_retries+1'b1;
                debug_failure<={state,cfg_step,transaction_read,transaction_ack,transaction_reg,transaction_data};
            end
        end
    end

    mdio_clause22 #(.CLK_HZ(CLK_HZ), .MDC_HZ(MDC_HZ)) u_mdio (
        .clk(clk), .rst_n(rst_n), .start(transaction_start),
        .read_op(transaction_read), .phy_addr(phy_address),
        .reg_addr(transaction_reg), .write_data(transaction_write),
        .busy(), .done(transaction_done), .read_ack(transaction_ack),
        .read_data(transaction_data), .mdc(mdc), .mdio_out(mdio_out),
        .mdio_oe(mdio_oe), .mdio_in(mdio_in)
    );

    always @* begin
        transaction_start = 1'b0;
        transaction_read = 1'b1;
        transaction_reg = 0;
        transaction_write = rmw_value;
        check_enable = 1'b0;
        check_mask = 16'hffff;
        check_expected = 0;
        case (state)
            S_ID1_REQ, S_ID1_WAIT: begin
                transaction_start = state == S_ID1_REQ;
                transaction_reg = 5'd2;
            end
            S_ID2_REQ, S_ID2_WAIT: begin
                transaction_start = state == S_ID2_REQ;
                transaction_reg = 5'd3;
            end
            S_CFG_REQ, S_CFG_WAIT: begin
                transaction_start = state == S_CFG_REQ;
                case (cfg_step)
                    0: begin transaction_read=0; transaction_reg=31; transaction_write=16'h0d08; end
                    1: begin transaction_reg=31; check_enable=1; check_mask=16'h0fff; check_expected=16'h0d08; end
                    2: transaction_reg=17;
                    3: begin transaction_read=0; transaction_reg=17; end
                    4: begin transaction_reg=17; check_enable=1; check_mask=16'h0100; check_expected=16'h0100; end
                    5: transaction_reg=21;
                    6: begin transaction_read=0; transaction_reg=21; end
                    7: begin transaction_reg=21; check_enable=1; check_mask=16'h0008; check_expected=16'h0008; end
                    8: begin transaction_read=0; transaction_reg=31; transaction_write=0; end
                    9: begin transaction_reg=31; check_enable=1; check_mask=16'h0fff; check_expected=0; end
                    10: transaction_reg=4;
                    11: begin transaction_read=0; transaction_reg=4; end
                    12: begin transaction_reg=4; check_enable=1; check_mask=16'hadff; check_expected=16'h0001; end
                    13: transaction_reg=9;
                    14: begin transaction_read=0; transaction_reg=9; end
                    15: begin transaction_reg=9; check_enable=1; check_mask=16'hf200; check_expected=16'h0200; end
                    // Disable EEE advertising before restarting negotiation.
                    // Clause22 indirect MMD access, no address post-increment.
                    16: begin transaction_read=0; transaction_reg=13; transaction_write=16'h0007; end
                    17: begin transaction_read=0; transaction_reg=14; transaction_write=16'h003c; end
                    18: begin transaction_read=0; transaction_reg=13; transaction_write=16'h4007; end
                    19: transaction_reg=14;
                    20: begin transaction_read=0; transaction_reg=14; end
                    21: begin transaction_reg=14; check_enable=1; check_mask=16'h0006; check_expected=0; end
                    22: begin transaction_read=0; transaction_reg=13; transaction_write=0; end
                    23: transaction_reg=0;
                    24: begin transaction_read=0; transaction_reg=0; end
                    // BMCR bit9 self-clears after restart; do not compare it.
                    25: begin transaction_reg=0; check_enable=1; check_mask=16'hfde0; check_expected=16'h1140; end
                    26: begin transaction_read=0; transaction_reg=31; transaction_write=16'h0a43; end
                    27: begin transaction_reg=31; check_enable=1; check_mask=16'h0fff; check_expected=16'h0a43; end
                    default: begin end
                endcase
            end
            S_POLL_REQ, S_POLL_WAIT: begin
                transaction_start = state == S_POLL_REQ;
                transaction_reg = 5'd26;
            end
            default: begin end
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= S_RESET;
            phy_rst_n <= 1'b0;
            ready <= 1'b0;
            link_up <= 1'b0;
            fault <= 1'b0;
            phy_address <= 0;
            timer <= 0;
            zero_match <= 1'b0;
            cfg_step <= 0;
            rmw_value <= 0;
        end else begin
            case (state)
                S_RESET: begin
                    phy_rst_n <= 1'b0;
                    ready <= 1'b0;
                    link_up <= 1'b0;
                    phy_address <= 0;
                    zero_match <= 1'b0;
                    // On a retry, PHYRSTB first falls on entry to this state.
                    // Count a full RESET_CYCLES after that edge as well.
                    if (timer >= RESET_CYCLES) begin
                        phy_rst_n <= 1'b1;
                        timer <= 0;
                        state <= S_SETTLE;
                    end else timer <= timer + 1'b1;
                end
                S_SETTLE: begin
                    if (timer >= SETTLE_CYCLES-1) begin
                        timer <= 0;
                        state <= S_ID1_REQ;
                    end else timer <= timer + 1'b1;
                end
                S_ID1_REQ: state <= S_ID1_WAIT;
                S_ID1_WAIT: if (transaction_done) begin
                    if (transaction_ack && transaction_data == 16'h001c)
                        state <= S_ID2_REQ;
                    else state <= S_SCAN_NEXT;
                end
                S_ID2_REQ: state <= S_ID2_WAIT;
                S_ID2_WAIT: if (transaction_done) begin
                    if (transaction_ack && transaction_data == 16'hc916) begin
                        if (phy_address == 0) begin
                            zero_match <= 1'b1;
                            state <= S_SCAN_NEXT;
                        end else begin
                            cfg_step <= 0;
                            state <= S_CFG_REQ;
                        end
                    end else state <= S_SCAN_NEXT;
                end
                S_SCAN_NEXT: begin
                    if (phy_address == 31) begin
                        if (zero_match) begin
                            phy_address <= 0;
                            cfg_step <= 0;
                            state <= S_CFG_REQ;
                        end else begin
                            fault <= 1'b1;
                            timer <= 0;
                            state <= S_RETRY;
                        end
                    end else begin
                        phy_address <= phy_address + 1'b1;
                        state <= S_ID1_REQ;
                    end
                end
                S_CFG_REQ: state <= S_CFG_WAIT;
                S_CFG_WAIT: if (transaction_done) begin
                    if (transaction_read && (!transaction_ack ||
                        (check_enable && ((transaction_data & check_mask) != check_expected)))) begin
                        ready <= 1'b0;
                        link_up <= 1'b0;
                        fault <= 1'b1;
                        timer <= 0;
                        state <= S_RETRY;
                    end else begin
                        case (cfg_step)
                            2: rmw_value <= transaction_data | 16'h0100;
                            5: rmw_value <= transaction_data | 16'h0008;
                            // No 10/100, PAUSE, remote-fault or extra next page.
                            10: rmw_value <= transaction_data & ~16'hade0;
                            // Normal test mode, automatic master/slave, advertise 1000FD.
                            // Register9 bit8 is RESERVED in RTL8211F: preserve it.
                            13: rmw_value <= (transaction_data & ~16'hf200) | 16'h0200;
                            19: rmw_value <= transaction_data & ~16'h0006;
                            // Auto-negotiate, restart, 1000FD forced-mode fallback;
                            // no reset, loopback, powerdown, isolate or test modes.
                            23: rmw_value <= (transaction_data & 16'h001f) | 16'h1340;
                            default: begin end
                        endcase
                        if (cfg_step == 27) begin
                            ready <= 1'b1;
                            timer <= 0;
                            state <= S_POLL_REQ;
                        end else begin
                            cfg_step <= cfg_step + 1'b1;
                            state <= S_CFG_REQ;
                        end
                    end
                end
                S_POLL_DELAY: begin
                    if (timer >= POLL_CYCLES-1) begin
                        timer <= 0;
                        state <= S_POLL_REQ;
                    end else timer <= timer + 1'b1;
                end
                S_POLL_REQ: state <= S_POLL_WAIT;
                S_POLL_WAIT: if (transaction_done) begin
                    timer <= 0;
                    if (!transaction_ack) begin
                        ready <= 1'b0;
                        link_up <= 1'b0;
                        fault <= 1'b1;
                        state <= S_RETRY;
                    end else begin
                        link_up <= (transaction_data & 16'h003c) == 16'h002c;
                        state <= S_POLL_DELAY;
                    end
                end
                S_RETRY: begin
                    if (timer >= RETRY_CYCLES-1) begin
                        timer <= 0;
                        state <= S_RESET;
                    end else timer <= timer + 1'b1;
                end
                default: begin
                    ready <= 1'b0;
                    link_up <= 1'b0;
                    fault <= 1'b1;
                    timer <= 0;
                    state <= S_RESET;
                end
            endcase
        end
    end
endmodule
