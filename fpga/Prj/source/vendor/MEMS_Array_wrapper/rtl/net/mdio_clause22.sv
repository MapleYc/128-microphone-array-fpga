`timescale 1ns/1ps
// Clause 22 master. start is accepted only while busy=0; done pulses for one clk.
// mdio_oe=1 drives the pad; use a real pull-up on the board. Every transaction
// includes 32 preamble bits and at least one released idle bit.
// PHY read data is produced AFTER MDC rises (RTL8211F t6 <= 300 ns).
// Sample near the following falling edge, allowing 350 ns plus synchronization.
module mdio_clause22 #(
    parameter integer CLK_HZ = 125000000,
    parameter integer MDC_HZ = 1000000
) (
    input  wire clk,
    input  wire rst_n,
    input  wire start,
    input  wire read_op,
    input  wire [4:0] phy_addr,
    input  wire [4:0] reg_addr,
    input  wire [15:0] write_data,
    output reg busy,
    output reg done,
    output reg read_ack,
    output reg [15:0] read_data,
    output reg mdc,
    output reg mdio_out,
    output reg mdio_oe,
    input  wire mdio_in
);
    // Clamp the requested MDC rate to the Clause 22 limit. The additional
    // read margin also lengthens writes, simplifying board timing constraints.
    localparam integer RATE = MDC_HZ > 2500000 ? 2500000 : MDC_HZ;
    localparam integer LOW_CYCLES_RAW = (CLK_HZ + 2*RATE - 1)/(2*RATE);
    localparam integer LOW_CYCLES = LOW_CYCLES_RAW < 2 ? 2 : LOW_CYCLES_RAW;
    // ceil(CLK_HZ * 350 ns) without overflowing a 32-bit constant expression.
    localparam integer READ_CYCLES = ((CLK_HZ + 1999999)/2000000)*7/10 + 3;
    localparam integer HIGH_CYCLES = LOW_CYCLES > READ_CYCLES ? LOW_CYCLES : READ_CYCLES;
    localparam integer DIV_W = $clog2(HIGH_CYCLES + 1);
    reg [DIV_W-1:0] divider;
    reg [6:0] bit_index;
    reg [63:0] tx_shift;
    reg transaction_read;
    // Pango PDS recognizes syn_preserve; ASYNC_REG alone is ignored there.
    (* syn_preserve = 1 *) reg mdio_meta, mdio_sync;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            mdio_meta <= 1'b1;
            mdio_sync <= 1'b1;
        end else begin
            mdio_meta <= mdio_in;
            mdio_sync <= mdio_meta;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            busy <= 1'b0;
            done <= 1'b0;
            read_ack <= 1'b0;
            read_data <= 16'd0;
            mdc <= 1'b0;
            mdio_out <= 1'b1;
            mdio_oe <= 1'b0;
            divider <= 0;
            bit_index <= 0;
            tx_shift <= 0;
            transaction_read <= 1'b0;
        end else begin
            done <= 1'b0;
            if (!busy) begin
                mdc <= 1'b0;
                mdio_oe <= 1'b0;
                divider <= 0;
                if (start) begin
                    busy <= 1'b1;
                    read_ack <= 1'b0;
                    read_data <= 16'd0;
                    bit_index <= 0;
                    transaction_read <= read_op;
                    tx_shift <= {32'hffffffff, 2'b01,
                                 (read_op ? 2'b10 : 2'b01), phy_addr,
                                 reg_addr, 2'b10, write_data};
                    mdio_oe <= 1'b1;
                    mdio_out <= 1'b1;
                end
            end else if ((!mdc && divider == LOW_CYCLES-1) ||
                         (mdc && divider == HIGH_CYCLES-1)) begin
                divider <= 0;
                mdc <= !mdc;
                if (mdc) begin
                    // At read bit 46 rising edge both ends had released TA-Z;
                    // the PHY then drives TA-0 before this falling edge.
                    if (transaction_read && bit_index == 46)
                        read_ack <= !mdio_sync;
                    if (transaction_read && bit_index >= 47 && bit_index <= 62)
                        read_data <= {read_data[14:0], mdio_sync};
                    if (bit_index == 64) begin
                        busy <= 1'b0;
                        done <= 1'b1;
                        mdio_oe <= 1'b0;
                    end else begin
                        bit_index <= bit_index + 1'b1;
                        tx_shift <= {tx_shift[62:0], 1'b1};
                        mdio_out <= tx_shift[62];
                        mdio_oe <= transaction_read ? (bit_index < 45) : (bit_index < 63);
                    end
                end
            end else begin
                divider <= divider + 1'b1;
            end
        end
    end
endmodule
