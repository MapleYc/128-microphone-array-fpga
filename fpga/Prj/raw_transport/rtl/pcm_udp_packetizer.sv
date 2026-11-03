`timescale 1ns/1ps
// M128 protocol v1: 32-byte header + three {BE64 frame_id, 128xLE24 PCM} records.
// Full payload is committed before any byte is exposed to the Ethernet engine.
module pcm_udp_packetizer (
    input wire clk,rst_n,
    input wire [31:0] stream_epoch,
    input wire selftest_mode,selftest_error,
    input wire [23:0] sample_data,
    input wire [6:0] sample_channel,
    input wire [63:0] sample_frame_id,
    input wire sample_first,sample_last,
    input wire [3:0] sample_status,
    input wire sample_valid,
    output wire sample_ready,
    output wire [7:0] payload_data,
    output reg payload_valid,
    input wire payload_ready,
    output reg payload_last,
    output wire [15:0] payload_length,
    output reg framing_error,
    output wire packet_sending,
    output reg packet_accepted
);
    localparam [1:0] COLLECT=0,BYTE1=1,BYTE2=2,SEND=3;
    localparam integer PAYLOAD_BYTES=1208;
    reg [1:0] state,frame_index;
    reg [6:0] expected_channel;
    reg [10:0] write_address,send_index;
    reg [23:0] sample_hold;
    reg [63:0] frame_ids[0:2];
    reg [7:0] pcm_memory[0:2047];
    reg [31:0] aggregate_flags,packet_flags,epoch_snapshot;
    reg [63:0] sequence_number,sequence_snapshot;
    reg [7:0] memory_read,metadata_read;
    reg metadata_select;
    wire valid_position=(sample_channel==expected_channel) &&
        (sample_first==(expected_channel==0)) && (sample_last==(expected_channel==127)) &&
        ((expected_channel==0) || (sample_frame_id==frame_ids[frame_index]));
    wire restart_position=sample_first && sample_channel==0 && !sample_last;
    wire take_sample=sample_valid && sample_ready;
    wire good_sample=take_sample && (valid_position || restart_position);
    wire [10:0] sample_write_address=valid_position ? write_address : 11'd0;
    assign sample_ready=rst_n && state==COLLECT;
    assign payload_length=16'd1208;
    assign packet_sending=state==SEND;
    assign payload_data=metadata_select ? metadata_read : memory_read;

    function automatic [7:0] header_byte(input [10:0] index);
        begin
            case(index)
                0:header_byte=8'h4d;1:header_byte=8'h31;2:header_byte=8'h32;3:header_byte=8'h38;
                4:header_byte=1;5:header_byte=32;6:header_byte=1;7:header_byte=128;
                8:header_byte=0;9:header_byte=0;10:header_byte=8'hbb;11:header_byte=8'h80;
                12:header_byte=0;13:header_byte=3;14:header_byte=8'h01;15:header_byte=8'h88;
                16,17,18,19,20,21,22,23:header_byte=sequence_snapshot>>((23-index)*8);
                24,25,26,27:header_byte=epoch_snapshot>>((27-index)*8);
                28,29,30,31:header_byte=packet_flags>>((31-index)*8);
                default:header_byte=0;
            endcase
        end
    endfunction
    reg [1:0] read_record;
    reg [10:0] record_offset,read_address;
    reg [7:0] next_metadata;
    reg next_is_metadata;
    always @* begin
        read_record=0;record_offset=0;read_address=0;
        next_metadata=header_byte(send_index);next_is_metadata=1;
        if(send_index>=32) begin
            if(send_index<424) begin read_record=0;record_offset=send_index-32;end
            else if(send_index<816) begin read_record=1;record_offset=send_index-424;end
            else begin read_record=2;record_offset=send_index-816;end
            if(record_offset<8) next_metadata=frame_ids[read_record]>>((7-record_offset)*8);
            else begin
                next_is_metadata=0;
                read_address=read_record*384+record_offset-8;
            end
        end
    end
    wire fetch_byte=rst_n && state==SEND && (!payload_valid || payload_ready) && send_index<PAYLOAD_BYTES;
    // One synchronous write port and one synchronous read port.
    always @(posedge clk) begin
        if(good_sample) pcm_memory[sample_write_address]<=sample_data[7:0];
        else if(rst_n && state==BYTE1) pcm_memory[write_address]<=sample_hold[15:8];
        else if(rst_n && state==BYTE2) pcm_memory[write_address]<=sample_hold[23:16];
        if(fetch_byte) memory_read<=pcm_memory[read_address];
    end
    integer f;
    always @(posedge clk) begin
        if(!rst_n) begin
            state<=COLLECT;frame_index<=0;expected_channel<=0;write_address<=0;send_index<=0;
            sample_hold<=0;aggregate_flags<=0;packet_flags<=0;epoch_snapshot<=0;
            sequence_number<=0;sequence_snapshot<=0;metadata_read<=0;metadata_select<=1;
            payload_valid<=0;payload_last<=0;framing_error<=0;packet_accepted<=0;
            for(f=0;f<3;f=f+1) frame_ids[f]<=0;
        end else begin
            packet_accepted<=0;
            case(state)
                COLLECT: if(take_sample) begin
                    if(!valid_position) begin
                        framing_error<=1;frame_index<=0;expected_channel<=0;
                        write_address<=0;aggregate_flags<=0;
                    end
                    if(valid_position || restart_position) begin
                        sample_hold<=sample_data;state<=BYTE1;
                        write_address<=sample_write_address+1'b1;
                        if(valid_position) begin
                            aggregate_flags<=aggregate_flags|{25'd0,sample_status[3],3'd0,sample_status[2:0]};
                            if(expected_channel==0) frame_ids[frame_index]<=sample_frame_id;
                        end else begin
                            frame_ids[0]<=sample_frame_id;
                            aggregate_flags<={25'd0,sample_status[3],3'd0,sample_status[2:0]};
                        end
                    end
                end
                BYTE1: begin write_address<=write_address+1'b1;state<=BYTE2;end
                BYTE2: begin
                    write_address<=write_address+1'b1;state<=COLLECT;
                    if(expected_channel==127) begin
                        expected_channel<=0;
                        if(frame_index==2) begin
                            state<=SEND;send_index<=0;payload_valid<=0;payload_last<=0;
                            sequence_snapshot<=sequence_number;epoch_snapshot<=stream_epoch;
                            packet_flags<=aggregate_flags|{26'd0,selftest_error,selftest_mode,framing_error,3'd0};
                        end else frame_index<=frame_index+1'b1;
                    end else expected_channel<=expected_channel+1'b1;
                end
                SEND: if(!payload_valid || payload_ready) begin
                    if(send_index<PAYLOAD_BYTES) begin
                        metadata_select<=next_is_metadata;metadata_read<=next_metadata;
                        payload_valid<=1;payload_last<=(send_index==PAYLOAD_BYTES-1);
                        send_index<=send_index+1'b1;
                    end else begin
                        payload_valid<=0;payload_last<=0;packet_accepted<=1;
                        sequence_number<=sequence_number+1'b1;
                        state<=COLLECT;frame_index<=0;expected_channel<=0;write_address<=0;aggregate_flags<=0;
                    end
                end
                default: state<=COLLECT;
            endcase
        end
    end
endmodule
