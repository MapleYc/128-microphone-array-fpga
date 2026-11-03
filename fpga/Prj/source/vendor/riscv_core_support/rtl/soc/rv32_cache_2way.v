`default_nettype none
// Blocking two-way cache with independent synchronous lookup/read replicas.
// One response per accepted port; a CPU group assigns older requests to lower
// ports and does not start its next group until every response has completed.
module rv32_cache_2way #(
    parameter integer PORTS=2,
    parameter integer SET_BITS=6,
    parameter integer WRITABLE=1,
    parameter integer BUS_TIMEOUT=4096
)(
    input wire clk,rst_n,
    input wire [PORTS-1:0] req_valid_i,req_write_i,
    output wire [PORTS-1:0] req_ready_o,
    input wire [PORTS*32-1:0] req_addr_i,req_data_i,
    input wire [PORTS*4-1:0] req_strb_i,
    output reg [PORTS-1:0] rsp_valid_o,rsp_error_o,
    input wire [PORTS-1:0] rsp_ready_i,
    output reg [PORTS*32-1:0] rsp_data_o,
    input wire invalidate_valid_i,
    output wire invalidate_ready_o,busy_o,
    output reg fatal_o,
    output wire b_req_valid_o,b_req_write_o,
    input wire b_req_ready_i,
    output wire [31:0] b_req_addr_o,b_req_data_o,
    output wire [3:0] b_req_strb_o,
    output wire [2:0] b_req_len_o,
    input wire b_rsp_valid_i,b_rsp_error_i,b_rsp_last_i,
    input wire [31:0] b_rsp_data_i,
    output wire b_rsp_ready_o
);
    localparam integer SETS=1<<SET_BITS;
    localparam integer WORDS=SETS*8;
    localparam integer TAG_BITS=27-SET_BITS;
    localparam integer OWNER_BITS=PORTS<=1 ? 1 : $clog2(PORTS);
    localparam integer TIME_BITS=BUS_TIMEOUT<=1 ? 1 : $clog2(BUS_TIMEOUT+1);
    localparam [2:0] P_IDLE=0,P_LOOKUP=1,P_MISS=2,P_HOLD=3,P_RETRY=4;
    localparam [2:0] G_IDLE=0,G_WRITE_PREP=1,G_REQ=2,G_READ=3,
                     G_WRITE=4,G_WAKE=5,G_POISON=6;
    reg [2:0] state[0:PORTS-1],engine;
    reg [31:0] address[0:PORTS-1],write_data[0:PORTS-1];
    reg [3:0] write_strb[0:PORTS-1];
    reg writing[0:PORTS-1];
    reg [31:0] held_data[0:PORTS-1];
    reg held_error[0:PORTS-1];
    reg [TAG_BITS-1:0] tag0[0:SETS-1],tag1[0:SETS-1];
    reg [SETS-1:0] valid0,valid1,lru;
    wire [TAG_BITS-1:0] read_tag0[0:PORTS-1],read_tag1[0:PORTS-1];
    wire [31:0] read_data0[0:PORTS-1],read_data1[0:PORTS-1];
    wire [PORTS-1:0] read_valid0,read_valid1;
    reg [PORTS-1:0] older_write,older_busy,hit0,hit1,lookup_fault;
    integer p,k,chosen;
    reg all_idle;
    always @* begin
        chosen=-1;all_idle=1'b1;
        older_write=0;older_busy=0;hit0=0;hit1=0;lookup_fault=0;
        rsp_valid_o=0;rsp_error_o=0;rsp_data_o=0;
        for(p=0;p<PORTS;p=p+1) begin
            if(state[p]!=P_IDLE) all_idle=1'b0;
            if(chosen<0 && state[p]==P_MISS) chosen=p;
            for(k=0;k<p;k=k+1) begin
                if(state[k]!=P_IDLE || req_valid_i[k]) older_busy[p]=1'b1;
                if(state[k]!=P_IDLE && state[k]!=P_HOLD && writing[k])
                    older_write[p]=1'b1;
            end
            hit0[p]=read_valid0[p] && read_tag0[p]==address[p][31:SET_BITS+5];
            hit1[p]=read_valid1[p] && read_tag1[p]==address[p][31:SET_BITS+5];
            lookup_fault[p]=|address[p][1:0] || (writing[p] && !WRITABLE);
            if(state[p]==P_HOLD) begin
                rsp_valid_o[p]=1'b1;rsp_error_o[p]=held_error[p];
                rsp_data_o[p*32+:32]=held_data[p];
            end else if(state[p]==P_LOOKUP && engine==G_IDLE && !older_write[p] &&
                        (lookup_fault[p] || (!writing[p] && (hit0[p] || hit1[p])))) begin
                rsp_valid_o[p]=1'b1;rsp_error_o[p]=lookup_fault[p];
                rsp_data_o[p*32+:32]=lookup_fault[p] ? 32'b0 :
                    hit0[p] ? read_data0[p] : read_data1[p];
            end
        end
    end
    assign busy_o=engine!=G_IDLE || !all_idle;
    assign invalidate_ready_o=rst_n && !fatal_o && engine==G_IDLE && all_idle;
    reg [OWNER_BITS-1:0] owner;
    reg owner_write,victim,write_hit0,write_hit1;
    reg [31:0] bus_addr,bus_data;
    reg [3:0] bus_strb;
    reg [2:0] beat;
    reg error_seen;
    reg [31:0] result_word;
    reg [TIME_BITS-1:0] wait_count;
    wire bus_fire=b_rsp_valid_i && b_rsp_ready_o;
    wire refill_write=engine==G_READ && bus_fire && !b_rsp_error_i;
    wire store_update=engine==G_WRITE && bus_fire && b_rsp_last_i && !b_rsp_error_i;
    wire [SET_BITS-1:0] owner_set=address[owner][SET_BITS+4:5];
    wire [SET_BITS+2:0] refill_index={owner_set,beat};
    wire [SET_BITS+2:0] store_index=address[owner][SET_BITS+4:2];
    // The engine serializes refill and write-through-hit completion. Present
    // one physical write port to each way instead of two different addresses.
    // The owner probe was clocked before G_WRITE_PREP and stays held while the
    // backend is busy; untouched bytes therefore retain that exact word.
    function [31:0] merge_store_bytes;
        input [31:0] previous_word,new_word;input [3:0] mask;integer b;
        begin
            merge_store_bytes=previous_word;
            for(b=0;b<4;b=b+1)if(mask[b])merge_store_bytes[b*8+:8]=new_word[b*8+:8];
        end
    endfunction
    wire [SET_BITS+2:0] memory_write_index=refill_write ? refill_index : store_index;
    wire memory_write0=(refill_write && !victim) || (store_update && write_hit0);
    wire memory_write1=(refill_write && victim) || (store_update && write_hit1);
    wire [31:0] memory_write_data0=refill_write ? b_rsp_data_i :
        merge_store_bytes(read_data0[owner],bus_data,bus_strb);
    wire [31:0] memory_write_data1=refill_write ? b_rsp_data_i :
        merge_store_bytes(read_data1[owner],bus_data,bus_strb);
    assign b_req_valid_o=rst_n && engine==G_REQ && !fatal_o;
    assign b_req_write_o=owner_write;
    assign b_req_addr_o=bus_addr;
    assign b_req_data_o=bus_data;
    assign b_req_strb_o=bus_strb;
    assign b_req_len_o=owner_write ? 3'd0 : 3'd7;
    assign b_rsp_ready_o=rst_n && (engine==G_READ || engine==G_WRITE || engine==G_POISON);
    wire protocol_bad=!fatal_o && b_rsp_valid_i &&
        ((engine!=G_READ && engine!=G_WRITE) ||
         (engine==G_READ && b_rsp_last_i!=(beat==3'd7)) ||
         (engine==G_WRITE && !b_rsp_last_i));
    wire waiting=(engine==G_REQ && !b_req_ready_i) ||
                 ((engine==G_READ || engine==G_WRITE) && !b_rsp_valid_i);
    wire timed_out=waiting && wait_count==BUS_TIMEOUT-1;
    genvar g;
    generate for(g=0;g<PORTS;g=g+1) begin : g_port
        // A younger write waits for the older port to finish. Read-only ports
        // never inspect inactive write payloads to decide request readiness.
        assign req_ready_o[g]=rst_n && !fatal_o && !invalidate_valid_i &&
            engine==G_IDLE && state[g]==P_IDLE &&
            (!req_valid_i[g] || !req_write_i[g] || !WRITABLE || !older_busy[g]);
        wire accepted=req_valid_i[g] && req_ready_o[g];
        wire refresh=(engine==G_WAKE && state[g]==P_RETRY);
        wire write_probe=engine==G_IDLE && chosen==g && writing[g];
        wire [31:0] read_address=accepted ? req_addr_i[g*32+:32] : address[g];
        reg [TAG_BITS-1:0] tq0,tq1;
        reg vq0,vq1;
        reg [31:0] dq0,dq1;
        reg [31:0] memory0[0:WORDS-1] /* synthesis syn_ramstyle = "block_ram" */;
        reg [31:0] memory1[0:WORDS-1] /* synthesis syn_ramstyle = "block_ram" */;
        assign read_tag0[g]=tq0;assign read_tag1[g]=tq1;
        assign read_valid0[g]=vq0;assign read_valid1[g]=vq1;
        assign read_data0[g]=dq0;assign read_data1[g]=dq1;
        always @(posedge clk) begin
            if(accepted || refresh || write_probe) begin
                tq0<=tag0[read_address[SET_BITS+4:5]];
                tq1<=tag1[read_address[SET_BITS+4:5]];
                vq0<=valid0[read_address[SET_BITS+4:5]];
                vq1<=valid1[read_address[SET_BITS+4:5]];
                dq0<=memory0[read_address[SET_BITS+4:2]];
                dq1<=memory1[read_address[SET_BITS+4:2]];
            end
            // Identical full-word writes to every read replica, one port/way.
            if(memory_write0)memory0[memory_write_index]<=memory_write_data0;
            if(memory_write1)memory1[memory_write_index]<=memory_write_data1;
        end
    end endgenerate
    integer i;
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            valid0<=0;valid1<=0;lru<=0;engine<=G_IDLE;fatal_o<=0;
            owner<=0;owner_write<=0;victim<=0;write_hit0<=0;write_hit1<=0;
            bus_addr<=0;bus_data<=0;bus_strb<=0;beat<=0;error_seen<=0;
            result_word<=0;wait_count<=0;
            for(i=0;i<PORTS;i=i+1) begin
                state[i]<=P_IDLE;address[i]<=0;write_data[i]<=0;write_strb[i]<=0;
                writing[i]<=0;held_data[i]<=0;held_error[i]<=0;
            end
        end else begin
            if(waiting) wait_count<=wait_count+1'b1;else wait_count<=0;
            for(i=0;i<PORTS;i=i+1) begin
                if(req_valid_i[i] && req_ready_o[i]) begin
                    address[i]<=req_addr_i[i*32+:32];writing[i]<=req_write_i[i];
                    if(req_write_i[i]) begin
                        write_data[i]<=req_data_i[i*32+:32];write_strb[i]<=req_strb_i[i*4+:4];
                    end
                    state[i]<=P_LOOKUP;
                end
                if(state[i]==P_HOLD && rsp_ready_i[i]) state[i]<=P_IDLE;
                if(state[i]==P_LOOKUP && engine==G_IDLE && !older_write[i]) begin
                    if(rsp_valid_o[i]) begin
                        if(!rsp_error_o[i])
                            lru[address[i][SET_BITS+4:5]]<=hit0[i] ? 1'b1 : 1'b0;
                        if(rsp_ready_i[i]) state[i]<=P_IDLE;
                        else begin
                            state[i]<=P_HOLD;held_data[i]<=rsp_data_o[i*32+:32];
                            held_error[i]<=rsp_error_o[i];
                        end
                    end else state[i]<=P_MISS;
                end
            end
            if(protocol_bad || timed_out) begin
                fatal_o<=1;engine<=G_POISON;
                for(i=0;i<PORTS;i=i+1)
                    if(state[i]!=P_IDLE && state[i]!=P_HOLD) begin
                        state[i]<=P_HOLD;held_data[i]<=0;held_error[i]<=1;
                    end
            end else case(engine)
                G_IDLE:begin
                    if(invalidate_valid_i && invalidate_ready_o) begin
                        valid0<=0;valid1<=0;lru<=0;
                    end else if(chosen>=0) begin
                        owner<=chosen;owner_write<=writing[chosen];
                        bus_data<=write_data[chosen];bus_strb<=write_strb[chosen];
                        error_seen<=0;result_word<=0;beat<=0;
                        if(writing[chosen]) begin
                            bus_addr<=address[chosen];engine<=G_WRITE_PREP;
                        end else begin
                            bus_addr<={address[chosen][31:5],5'b0};
                            if(!valid0[address[chosen][SET_BITS+4:5]]) begin
                                victim<=0;valid0[address[chosen][SET_BITS+4:5]]<=0;
                            end else if(!valid1[address[chosen][SET_BITS+4:5]]) begin
                                victim<=1;valid1[address[chosen][SET_BITS+4:5]]<=0;
                            end else begin
                                victim<=lru[address[chosen][SET_BITS+4:5]];
                                if(!lru[address[chosen][SET_BITS+4:5]]) valid0[address[chosen][SET_BITS+4:5]]<=0;
                                else valid1[address[chosen][SET_BITS+4:5]]<=0;
                            end
                            engine<=G_REQ;
                        end
                    end
                end
                G_WRITE_PREP:begin
                    write_hit0<=read_valid0[owner] && read_tag0[owner]==address[owner][31:SET_BITS+5];
                    write_hit1<=read_valid1[owner] && read_tag1[owner]==address[owner][31:SET_BITS+5];
                    engine<=G_REQ;
                end
                G_REQ:if(b_req_ready_i) engine<=owner_write ? G_WRITE : G_READ;
                G_READ:if(bus_fire) begin
                    error_seen<=error_seen || b_rsp_error_i;
                    if(beat==address[owner][4:2]) result_word<=b_rsp_data_i;
                    if(b_rsp_last_i) begin
                        held_data[owner]<=error_seen || b_rsp_error_i ? 0 :
                            beat==address[owner][4:2] ? b_rsp_data_i : result_word;
                        held_error[owner]<=error_seen || b_rsp_error_i;
                        state[owner]<=P_HOLD;
                        if(!error_seen && !b_rsp_error_i) begin
                            if(!victim) begin tag0[owner_set]<=address[owner][31:SET_BITS+5];valid0[owner_set]<=1;end
                            else begin tag1[owner_set]<=address[owner][31:SET_BITS+5];valid1[owner_set]<=1;end
                            lru[owner_set]<=!victim;
                        end
                        for(i=0;i<PORTS;i=i+1) if(i!=owner &&
                            (state[i]==P_LOOKUP || state[i]==P_MISS)) state[i]<=P_RETRY;
                        engine<=G_WAKE;
                    end else beat<=beat+1'b1;
                end
                G_WRITE:if(bus_fire) begin
                    held_data[owner]<=0;held_error[owner]<=b_rsp_error_i;
                    state[owner]<=P_HOLD;
                    if(!b_rsp_error_i && (write_hit0 || write_hit1)) lru[owner_set]<=write_hit0 ? 1'b1 : 1'b0;
                    for(i=0;i<PORTS;i=i+1) if(i!=owner &&
                        (state[i]==P_LOOKUP || state[i]==P_MISS)) state[i]<=P_RETRY;
                    engine<=G_WAKE;
                end
                G_WAKE:begin
                    for(i=0;i<PORTS;i=i+1) if(state[i]==P_RETRY) state[i]<=P_LOOKUP;
                    engine<=G_IDLE;
                end
                default:engine<=G_POISON;
            endcase
        end
    end
endmodule
`default_nettype wire
