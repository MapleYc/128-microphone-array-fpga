`timescale 1ns/1ps
// Complete-packet store-and-forward transmitter, 125 MHz GMII, full duplex.
// Input accepts 1..1472 bytes. Length is repeated on every accepted byte;
// last must coincide with length. A malformed input is drained through last.
// link_up is the physical link, synchronous to clk. ARP runs without payload.
// stream_enable independently flushes UDP on acquisition/mode changes while
// retaining the learned peer and servicing ARP. The producer flushes with it.
// tx_packet_done pulses after the final UDP FCS byte; tx_error is sticky until
// a new link/stream session starts. Intentional mode flushes must not poison BIST.
// IPv4/UDP: RFC 791 / RFC 768; address resolution: RFC 826.
module ethernet_udp_engine #(
    parameter [47:0] LOCAL_MAC = 48'h020000000128,
    parameter [31:0] LOCAL_IP = 32'hc0a80a02,
    parameter [31:0] PEER_IP = 32'hc0a80a01,
    parameter [15:0] LOCAL_PORT = 16'd5000,
    parameter [15:0] PEER_PORT = 16'd5000,
    parameter integer ARP_RETRY_CYCLES = 125000000
) (
    input wire clk, input wire rst_n, input wire link_up, input wire stream_enable,
    output wire peer_ready,
    input wire [7:0] tx_payload_data, input wire tx_payload_valid,
    output wire tx_payload_ready, input wire tx_payload_last,
    input wire [15:0] tx_payload_length,
    output reg tx_packet_done, output reg tx_error,
    input wire rx_clk, input wire [7:0] gmii_rxd,
    input wire gmii_rx_dv, input wire gmii_rx_er,
    output reg [7:0] gmii_txd, output reg gmii_tx_en, output wire gmii_tx_er,
    output wire [255:0] debug_status,output wire [127:0] debug_rx
);
    localparam IDLE=0, PREAMBLE=1, BODY=2, FCS=3, IFG=4;
    localparam UDP=0, ARP_REQUEST=1, ARP_REPLY=2;
    reg [2:0] state;
    reg [1:0] kind;
    reg [3:0] pre_index, ifg_count;
    reg [10:0] body_index, body_length;
    reg [1:0] fcs_index;
    reg [31:0] crc, fcs;
    reg [15:0] ip_id, frame_ip_id, ip_checksum, udp_checksum;
    reg [47:0] frame_mac, frame_arp_mac;
    reg [31:0] frame_arp_ip;
    reg [47:0] peer_mac, reply_mac;
    reg [31:0] reply_ip;
    reg peer_valid, reply_pending;
    reg [31:0] retry_count;
    reg previous_link, link_epoch;

    reg [7:0] payload_mem [0:1471];
    reg [7:0] payload_read_data, word_high;
    reg [10:0] write_count, payload_read_address;
    reg [15:0] packet_length;
    reg [31:0] payload_sum;
    reg packet_full, draining;
    wire accepted = tx_payload_valid && tx_payload_ready;
    wire length_valid = tx_payload_length != 0 && tx_payload_length <= 1472;
    wire [15:0] active_length = write_count == 0 ? tx_payload_length : packet_length;
    wire input_good = length_valid && (write_count == 0 || tx_payload_length == packet_length) &&
                       (tx_payload_last == (write_count == active_length - 1'b1));
    assign peer_ready = rst_n && link_up && peer_valid;
    assign tx_payload_ready = rst_n && link_up && stream_enable && !packet_full;
    assign gmii_tx_er = 1'b0;
    // The single synchronous read port is prefetched one GMII byte ahead.
    always @* begin
        payload_read_address = 0;
        if (state == BODY && kind == UDP && body_index >= 41 &&
            body_index < 41 + packet_length)
            payload_read_address = body_index - 41;
    end
    always @(posedge clk) begin
        if (accepted && !draining && input_good)
            payload_mem[write_count] <= tx_payload_data;
        payload_read_data <= payload_mem[payload_read_address];
    end

    function automatic [31:0] crc_byte(input [31:0] c0, input [7:0] d);
        reg [31:0] c; integer k;
        begin
            c = c0 ^ {24'b0,d};
            for (k=0;k<8;k=k+1) c = c[0] ? (c >> 1)^32'hedb88320 : c >> 1;
            crc_byte = c;
        end
    endfunction
    function automatic [15:0] checksum(input [31:0] sum);
        reg [16:0] fold1, fold2;
        begin
            fold1 = {1'b0,sum[15:0]} + {1'b0,sum[31:16]};
            fold2 = {1'b0,fold1[15:0]} + fold1[16];
            checksum = ~fold2[15:0];
        end
    endfunction
    wire [15:0] ip_length = packet_length + 16'd28;
    wire [15:0] udp_length = packet_length + 16'd8;
    wire [31:0] ip_header_sum = 32'h4500 + {16'b0,ip_length} + {16'b0,ip_id} +
        32'h4000 + 32'h4011 + {16'b0,LOCAL_IP[31:16]} + {16'b0,LOCAL_IP[15:0]} +
        {16'b0,PEER_IP[31:16]} + {16'b0,PEER_IP[15:0]};
    wire [31:0] udp_header_sum = payload_sum + {16'b0,LOCAL_IP[31:16]} +
        {16'b0,LOCAL_IP[15:0]} + {16'b0,PEER_IP[31:16]} + {16'b0,PEER_IP[15:0]} +
        32'd17 + {16'b0,udp_length} + {16'b0,LOCAL_PORT} + {16'b0,PEER_PORT} + {16'b0,udp_length};
    wire [15:0] udp_check_value = checksum(udp_header_sum);

    wire message_req, message_request, message_epoch;
    wire [47:0] message_mac;
    wire [31:0] message_ip;
    reg message_ack;
    (* async_reg = "true", syn_preserve = 1 *) reg req_meta, req_sync;

    reg [31:0] debug_arp_requests,debug_arp_replies,debug_arp_received;
    reg [31:0] debug_udp_sent,debug_frames_sent;
    assign debug_status={{16'b0,peer_mac[47:32]},peer_mac[31:0],debug_frames_sent,
        debug_udp_sent,debug_arp_received,debug_arp_replies,debug_arp_requests,
        {20'b0,kind,state,draining,reply_pending,tx_error,packet_full,peer_valid,link_up,gmii_tx_en}};
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            debug_arp_requests<=0;debug_arp_replies<=0;debug_arp_received<=0;
            debug_udp_sent<=0;debug_frames_sent<=0;
        end else begin
            if(req_sync!=message_ack && (!reply_pending || !link_up || message_epoch!=link_epoch) &&
                link_up && message_epoch==link_epoch) debug_arp_received<=debug_arp_received+1'b1;
            if(link_up && (kind!=UDP || stream_enable) && state==FCS && fcs_index==3) begin
                debug_frames_sent<=debug_frames_sent+1'b1;
                if(kind==UDP)debug_udp_sent<=debug_udp_sent+1'b1;
                if(kind==ARP_REQUEST)debug_arp_requests<=debug_arp_requests+1'b1;
                if(kind==ARP_REPLY)debug_arp_replies<=debug_arp_replies+1'b1;
            end
        end
    end
    // rst_n is local to the TX/system domain. PHY RXC is independent and may
    // stop while reset is asserted, so all RX parser/mailbox registers need
    // their own asynchronous assertion and three-clock synchronous release.
    (* syn_preserve = 1 *) reg [2:0] rx_reset_release;
    wire rx_rst_n = rx_reset_release[2];
    reg [31:0] debug_rx_frames,debug_rx_errors,debug_rx_bytes,debug_rx_cycles;
    reg debug_rx_active,debug_rx_bad;
    assign debug_rx={debug_rx_cycles,debug_rx_bytes,debug_rx_errors,debug_rx_frames};
    always @(posedge rx_clk or negedge rx_rst_n) begin
        if(!rx_rst_n) begin
            debug_rx_frames<=0;debug_rx_errors<=0;debug_rx_bytes<=0;debug_rx_cycles<=0;
            debug_rx_active<=0;debug_rx_bad<=0;
        end else begin
            debug_rx_cycles<=debug_rx_cycles+1'b1;
            debug_rx_active<=gmii_rx_dv;
            if(gmii_rx_dv) begin
                debug_rx_bytes<=debug_rx_bytes+1'b1;
                debug_rx_bad<=gmii_rx_er | (debug_rx_active && debug_rx_bad);
            end else if(debug_rx_active) begin
                debug_rx_frames<=debug_rx_frames+1'b1;
                if(debug_rx_bad || gmii_rx_er)debug_rx_errors<=debug_rx_errors+1'b1;
                debug_rx_bad<=0;
            end
        end
    end
    always @(posedge rx_clk or negedge rst_n) begin
        if (!rst_n) rx_reset_release <= 3'b000;
        else rx_reset_release <= {rx_reset_release[1:0],1'b1};
    end
    ethernet_arp_rx #(.LOCAL_MAC(LOCAL_MAC), .LOCAL_IP(LOCAL_IP)) u_arp_rx (
        .rx_clk(rx_clk), .rst_n(rx_rst_n), .link_up(link_up), .link_epoch(link_epoch),
        .gmii_rxd(gmii_rxd), .gmii_rx_dv(gmii_rx_dv), .gmii_rx_er(gmii_rx_er),
        .message_req(message_req), .message_ack(message_ack),
        .message_mac(message_mac), .message_ip(message_ip),
        .message_request(message_request), .message_epoch(message_epoch)
    );
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin req_meta <= 0; req_sync <= 0; end
        else begin req_meta <= message_req; req_sync <= req_meta; end
    end

    reg [7:0] body_byte;
    always @* begin
        body_byte = 0;
        if (body_index < 6) body_byte = frame_mac >> ((5-body_index)*8);
        else if (body_index < 12) body_byte = LOCAL_MAC >> ((11-body_index)*8);
        else if (body_index == 12) body_byte = 8'h08;
        else if (body_index == 13) body_byte = kind == UDP ? 8'h00 : 8'h06;
        else if (kind == UDP) begin
            case (body_index)
                14: body_byte=8'h45; 15: body_byte=0;
                16: body_byte=ip_length[15:8]; 17: body_byte=ip_length[7:0];
                18: body_byte=frame_ip_id[15:8]; 19: body_byte=frame_ip_id[7:0];
                20: body_byte=8'h40; 21: body_byte=0;
                22: body_byte=8'h40; 23: body_byte=8'h11;
                24: body_byte=ip_checksum[15:8]; 25: body_byte=ip_checksum[7:0];
                26: body_byte=LOCAL_IP[31:24]; 27: body_byte=LOCAL_IP[23:16];
                28: body_byte=LOCAL_IP[15:8]; 29: body_byte=LOCAL_IP[7:0];
                30: body_byte=PEER_IP[31:24]; 31: body_byte=PEER_IP[23:16];
                32: body_byte=PEER_IP[15:8]; 33: body_byte=PEER_IP[7:0];
                34: body_byte=LOCAL_PORT[15:8]; 35: body_byte=LOCAL_PORT[7:0];
                36: body_byte=PEER_PORT[15:8]; 37: body_byte=PEER_PORT[7:0];
                38: body_byte=udp_length[15:8]; 39: body_byte=udp_length[7:0];
                40: body_byte=udp_checksum[15:8]; 41: body_byte=udp_checksum[7:0];
                default: if (body_index < 42 + packet_length) body_byte=payload_read_data;
            endcase
        end else begin
            case (body_index)
                14: body_byte=0; 15: body_byte=1;
                16: body_byte=8'h08; 17: body_byte=0;
                18: body_byte=6; 19: body_byte=4;
                20: body_byte=0; 21: body_byte=kind == ARP_REQUEST ? 1 : 2;
                22: body_byte=LOCAL_MAC[47:40]; 23: body_byte=LOCAL_MAC[39:32];
                24: body_byte=LOCAL_MAC[31:24]; 25: body_byte=LOCAL_MAC[23:16];
                26: body_byte=LOCAL_MAC[15:8]; 27: body_byte=LOCAL_MAC[7:0];
                28: body_byte=LOCAL_IP[31:24]; 29: body_byte=LOCAL_IP[23:16];
                30: body_byte=LOCAL_IP[15:8]; 31: body_byte=LOCAL_IP[7:0];
                32: body_byte=frame_arp_mac[47:40]; 33: body_byte=frame_arp_mac[39:32];
                34: body_byte=frame_arp_mac[31:24]; 35: body_byte=frame_arp_mac[23:16];
                36: body_byte=frame_arp_mac[15:8]; 37: body_byte=frame_arp_mac[7:0];
                38: body_byte=frame_arp_ip[31:24]; 39: body_byte=frame_arp_ip[23:16];
                40: body_byte=frame_arp_ip[15:8]; 41: body_byte=frame_arp_ip[7:0];
                default: body_byte=0;
            endcase
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state<=IDLE; kind<=UDP; pre_index<=0; ifg_count<=0;
            body_index<=0; body_length<=0; fcs_index<=0;
            crc<=32'hffffffff; fcs<=0; ip_id<=0; frame_ip_id<=0;
            ip_checksum<=0; udp_checksum<=0; frame_mac<=0;
            frame_arp_mac<=0; frame_arp_ip<=0;
            peer_mac<=0; peer_valid<=0; reply_mac<=0; reply_ip<=0; reply_pending<=0;
            retry_count<=0; previous_link<=0; link_epoch<=0; message_ack<=0;
            word_high<=0; write_count<=0; packet_length<=0; payload_sum<=0;
            packet_full<=0; draining<=0;
            gmii_txd<=0; gmii_tx_en<=0; tx_packet_done<=0; tx_error<=0;
        end else begin
            tx_packet_done<=0;
            previous_link<=link_up;
            if(link_up && !previous_link)tx_error<=0;
            if (previous_link && !link_up) link_epoch<=~link_epoch;
            // req_sync has two synchronizer stages; the bundled fields have
            // already been held for >=2 destination clocks at this sampling edge.
            if (req_sync != message_ack && (!reply_pending || !link_up || message_epoch != link_epoch)) begin
                message_ack<=req_sync;
                if (link_up && message_epoch == link_epoch) begin
                    if (message_ip == PEER_IP) begin peer_mac<=message_mac; peer_valid<=1; end
                    if (message_request) begin
                        reply_pending<=1; reply_mac<=message_mac; reply_ip<=message_ip;
                    end
                end
            end
            if (!link_up) begin
                if (packet_full || write_count != 0 || draining || state == PREAMBLE || state == BODY || state == FCS)
                    tx_error<=1;
                state<=IDLE; gmii_txd<=0; gmii_tx_en<=0;
                write_count<=0; packet_length<=0; payload_sum<=0; packet_full<=0; draining<=0;
                peer_valid<=0; peer_mac<=0; reply_pending<=0; retry_count<=0;
            end else begin
                if (retry_count != 0) retry_count<=retry_count-1'b1;
                // A mode change cancels only the old UDP payload/session. ARP
                // state and an in-flight ARP frame belong to the physical link.
                if (!stream_enable) begin
                    write_count<=0;packet_length<=0;payload_sum<=0;
                    packet_full<=0;draining<=0;tx_error<=0;
                end
                if (accepted) begin
                    if (draining) begin
                        if (tx_payload_last) begin draining<=0; write_count<=0; payload_sum<=0; end
                    end else if (!input_good) begin
                        tx_error<=1; draining<=!tx_payload_last; write_count<=0; payload_sum<=0;
                    end else begin
                        if (write_count == 0) packet_length<=tx_payload_length;
                        word_high<=tx_payload_data;
                        if (write_count[0]) payload_sum<=payload_sum + {16'b0,word_high,tx_payload_data};
                        else if (tx_payload_last) payload_sum<=payload_sum + {16'b0,tx_payload_data,8'b0};
                        if (tx_payload_last) packet_full<=1;
                        else write_count<=write_count+1'b1;
                    end
                end
                case (state)
                IDLE: begin
                    gmii_tx_en<=0;
                    pre_index<=0; body_index<=0; crc<=32'hffffffff;
                    if (stream_enable && packet_full && peer_valid) begin
                        kind<=UDP; frame_mac<=peer_mac; frame_ip_id<=ip_id;
                        ip_checksum<=checksum(ip_header_sum);
                        udp_checksum<=udp_check_value == 0 ? 16'hffff : udp_check_value;
                        body_length<=packet_length < 18 ? 11'd60 : packet_length[10:0]+11'd42;
                        state<=PREAMBLE;
                    end else if (reply_pending) begin
                        kind<=ARP_REPLY; frame_mac<=reply_mac; frame_arp_mac<=reply_mac;
                        frame_arp_ip<=reply_ip; body_length<=60;
                        reply_pending<=0; state<=PREAMBLE;
                    end else if (!peer_valid && retry_count == 0) begin
                        kind<=ARP_REQUEST; frame_mac<=48'hffffffffffff; frame_arp_mac<=0;
                        frame_arp_ip<=PEER_IP; body_length<=60;
                        retry_count<=ARP_RETRY_CYCLES; state<=PREAMBLE;
                    end
                end
                PREAMBLE: begin
                    gmii_tx_en<=1;
                    gmii_txd<=pre_index == 7 ? 8'hd5 : 8'h55;
                    if (pre_index == 7) state<=BODY;
                    else pre_index<=pre_index+1'b1;
                end
                BODY: begin
                    gmii_tx_en<=1; gmii_txd<=body_byte;
                    crc<=crc_byte(crc,body_byte);
                    if (body_index == body_length-1'b1) begin
                        fcs<=~crc_byte(crc,body_byte); fcs_index<=0; state<=FCS;
                    end else body_index<=body_index+1'b1;
                end
                FCS: begin
                    gmii_tx_en<=1; gmii_txd<=fcs[7:0]; fcs<={8'b0,fcs[31:8]};
                    if (fcs_index == 3) begin
                        state<=IFG; ifg_count<=0;
                        if (kind == UDP && stream_enable) begin
                            tx_packet_done<=1; ip_id<=ip_id+1'b1;
                            packet_full<=0; write_count<=0; payload_sum<=0;
                        end
                    end else fcs_index<=fcs_index+1'b1;
                end
                IFG: begin
                    gmii_tx_en<=0;
                    if (ifg_count == 11) state<=IDLE;
                    else ifg_count<=ifg_count+1'b1;
                end
                default: state<=IDLE;
                endcase
                // Finish an inter-frame gap after aborting an old UDP frame.
                // Do not let the FCS path report completion on the flush edge.
                if (!stream_enable && kind==UDP &&
                    (state==PREAMBLE || state==BODY || state==FCS)) begin
                    state<=IFG;ifg_count<=0;gmii_tx_en<=0;gmii_txd<=0;
                    tx_packet_done<=0;
                end
            end
        end
    end
endmodule
