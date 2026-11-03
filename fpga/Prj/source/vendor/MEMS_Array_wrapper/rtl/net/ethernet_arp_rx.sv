`timescale 1ns/1ps
// GMII includes 7 x 55 + D5, Ethernet header, payload/pad and little-endian FCS.
// One bundled-data mailbox: fields stay constant from req toggle until ack.
// A full mailbox drops new ARP messages, which peers can safely retransmit.
module ethernet_arp_rx #(
    parameter [47:0] LOCAL_MAC = 48'h020000000128,
    parameter [31:0] LOCAL_IP = 32'hc0a80a02
) (
    input wire rx_clk, input wire rst_n, input wire link_up,
    input wire link_epoch,
    input wire [7:0] gmii_rxd, input wire gmii_rx_dv, input wire gmii_rx_er,
    output reg message_req, input wire message_ack,
    output reg [47:0] message_mac, output reg [31:0] message_ip,
    output reg message_request, output reg message_epoch
);
    (* async_reg = "true", syn_preserve = 1 *) reg ack_meta, ack_sync;
    (* async_reg = "true", syn_preserve = 1 *) reg link_meta, link_sync;
    (* async_reg = "true", syn_preserve = 1 *) reg epoch_meta, epoch_sync;
    always @(posedge rx_clk or negedge rst_n) begin
        if (!rst_n) begin
            ack_meta <= 0; ack_sync <= 0; link_meta <= 0; link_sync <= 0;
            epoch_meta <= 0; epoch_sync <= 0;
        end else begin
            ack_meta <= message_ack; ack_sync <= ack_meta;
            link_meta <= link_up; link_sync <= link_meta;
            epoch_meta <= link_epoch; epoch_sync <= epoch_meta;
        end
    end
    function automatic [31:0] crc_byte(input [31:0] crc, input [7:0] data);
        reg [31:0] c; integer k;
        begin
            c = crc ^ {24'b0,data};
            for (k=0;k<8;k=k+1) c = c[0] ? (c >> 1)^32'hedb88320 : c >> 1;
            crc_byte = c;
        end
    endfunction
    localparam IDLE=0, PREAMBLE=1, FRAME=2, DISCARD=3;
    reg [1:0] state;
    reg [3:0] pre_count;
    reg [10:0] byte_count;
    reg [31:0] crc;
    reg bad, frame_epoch;
    reg [47:0] dst_mac, src_mac, sender_mac, target_mac;
    reg [31:0] sender_ip, target_ip;
    reg [15:0] ethertype, htype, ptype, operation;
    reg [7:0] hlen, plen;
    always @(posedge rx_clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE; pre_count <= 0; byte_count <= 0;
            crc <= 32'hffffffff; bad <= 0; frame_epoch <= 0;
            dst_mac <= 0; src_mac <= 0; sender_mac <= 0; target_mac <= 0;
            sender_ip <= 0; target_ip <= 0; ethertype <= 0; htype <= 0;
            ptype <= 0; operation <= 0; hlen <= 0; plen <= 0;
            message_req <= 0; message_mac <= 0; message_ip <= 0;
            message_request <= 0; message_epoch <= 0;
        end else if (!link_sync) begin
            state <= IDLE; byte_count <= 0; bad <= 0;
        end else begin
            case (state)
            IDLE: if (gmii_rx_dv) begin
                frame_epoch <= epoch_sync;
                bad <= gmii_rx_er;
                pre_count <= 1;
                state <= (gmii_rxd == 8'h55) ? PREAMBLE : DISCARD;
            end
            PREAMBLE: begin
                if (!gmii_rx_dv) state <= IDLE;
                else if (gmii_rx_er || frame_epoch != epoch_sync) state <= DISCARD;
                else if (pre_count == 7) begin
                    if (gmii_rxd == 8'hd5) begin
                        state <= FRAME; byte_count <= 0; crc <= 32'hffffffff;
                    end else state <= DISCARD;
                end else if (gmii_rxd == 8'h55) pre_count <= pre_count + 1'b1;
                else state <= DISCARD;
            end
            FRAME: begin
                if (gmii_rx_dv) begin
                    if (gmii_rx_er || frame_epoch != epoch_sync) bad <= 1;
                    if (byte_count == 1518) state <= DISCARD;
                    else byte_count <= byte_count + 1'b1;
                    crc <= crc_byte(crc,gmii_rxd);
                    if (byte_count < 6) dst_mac <= {dst_mac[39:0],gmii_rxd};
                    else if (byte_count < 12) src_mac <= {src_mac[39:0],gmii_rxd};
                    else if (byte_count < 14) ethertype <= {ethertype[7:0],gmii_rxd};
                    else if (byte_count < 16) htype <= {htype[7:0],gmii_rxd};
                    else if (byte_count < 18) ptype <= {ptype[7:0],gmii_rxd};
                    else if (byte_count == 18) hlen <= gmii_rxd;
                    else if (byte_count == 19) plen <= gmii_rxd;
                    else if (byte_count < 22) operation <= {operation[7:0],gmii_rxd};
                    else if (byte_count < 28) sender_mac <= {sender_mac[39:0],gmii_rxd};
                    else if (byte_count < 32) sender_ip <= {sender_ip[23:0],gmii_rxd};
                    else if (byte_count < 38) target_mac <= {target_mac[39:0],gmii_rxd};
                    else if (byte_count < 42) target_ip <= {target_ip[23:0],gmii_rxd};
                end else begin
                    state <= IDLE;
                    if (!bad && !gmii_rx_er && frame_epoch == epoch_sync &&
                        byte_count >= 64 && crc == 32'hdebb20e3 &&
                        ethertype == 16'h0806 && htype == 1 && ptype == 16'h0800 &&
                        hlen == 6 && plen == 4 && target_ip == LOCAL_IP &&
                        src_mac == sender_mac && sender_mac != 0 && !sender_mac[40] &&
                        // A request with SPA=0 is a valid RFC 5227 ARP probe.
                        (sender_ip != 0 || operation == 1) &&
                        (dst_mac == LOCAL_MAC || dst_mac == 48'hffffffffffff) &&
                        (operation == 1 || (operation == 2 && target_mac == LOCAL_MAC && dst_mac == LOCAL_MAC)) &&
                        message_req == ack_sync) begin
                        message_mac <= sender_mac; message_ip <= sender_ip;
                        message_request <= (operation == 1);
                        message_epoch <= frame_epoch;
                        message_req <= ~message_req;
                    end
                end
            end
            DISCARD: if (!gmii_rx_dv) state <= IDLE;
            default: state <= IDLE;
            endcase
        end
    end
endmodule
