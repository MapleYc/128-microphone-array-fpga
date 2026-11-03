`default_nettype none
// One elastic three-word fetch descriptor, with per-word bounds/faults.
module rv32_icache_triple #(
    parameter [31:0] EXEC_BYTES=32'h00010000,
    parameter integer SET_BITS=6,BUS_TIMEOUT=4096
)(
    input wire clk,rst_n,
    input wire req_valid_i,output wire req_ready_o,input wire [31:0] req_pc_i,
    output wire rsp_valid_o,input wire rsp_ready_i,
    output wire [95:0] rsp_data_o,output wire [2:0] rsp_error_o,
    input wire invalidate_valid_i,output wire invalidate_ready_o,
    output wire fatal_o,
    output wire b_req_valid_o,b_req_write_o,input wire b_req_ready_i,
    output wire [31:0] b_req_addr_o,b_req_data_o,
    output wire [3:0] b_req_strb_o,output wire [2:0] b_req_len_o,
    input wire b_rsp_valid_i,b_rsp_error_i,b_rsp_last_i,input wire [31:0] b_rsp_data_i,
    output wire b_rsp_ready_o
);
    reg active;
    reg [31:0] base_pc;
    reg [2:0] sent,done,errors;
    reg [95:0] data;
    wire [95:0] addresses;
    wire [2:0] qvalid,qready,svalid,sready,serror;
    wire [95:0] sdata;
    wire unit_invalidate_ready;
    assign req_ready_o=rst_n && !active && !invalidate_valid_i && !fatal_o;
    assign rsp_valid_o=rst_n && active && (&done);
    assign rsp_data_o=data;assign rsp_error_o=errors;
    assign invalidate_ready_o=!active && unit_invalidate_ready;
    genvar p;
    generate for(p=0;p<3;p=p+1) begin : g_word
        assign addresses[p*32+:32]=base_pc+(p*4);
        assign qvalid[p]=active && !sent[p] && !done[p];
        assign sready[p]=active && !done[p];
    end endgenerate
    rv32_cache_2way #(.PORTS(3),.SET_BITS(SET_BITS),.WRITABLE(0),.BUS_TIMEOUT(BUS_TIMEOUT)) u_cache(
        .clk(clk),.rst_n(rst_n),.req_valid_i(qvalid),.req_write_i(3'b0),
        .req_ready_o(qready),.req_addr_i(addresses),.req_data_i(96'b0),.req_strb_i(12'b0),
        .rsp_valid_o(svalid),.rsp_ready_i(sready),.rsp_data_o(sdata),.rsp_error_o(serror),
        .invalidate_valid_i(invalidate_valid_i && !active),.invalidate_ready_o(unit_invalidate_ready),
        .busy_o(),.fatal_o(fatal_o),.b_req_valid_o(b_req_valid_o),.b_req_write_o(b_req_write_o),
        .b_req_ready_i(b_req_ready_i),.b_req_addr_o(b_req_addr_o),.b_req_data_o(b_req_data_o),
        .b_req_strb_o(b_req_strb_o),.b_req_len_o(b_req_len_o),
        .b_rsp_valid_i(b_rsp_valid_i),.b_rsp_ready_o(b_rsp_ready_o),.b_rsp_data_i(b_rsp_data_i),
        .b_rsp_error_i(b_rsp_error_i),.b_rsp_last_i(b_rsp_last_i));
    function automatic bad_word;
        input [31:0] address;
        begin bad_word=(|address[1:0]) || address>=EXEC_BYTES;end
    endfunction
    integer k;
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin active<=0;base_pc<=0;sent<=0;done<=0;errors<=0;data<=0;end
        else begin
            if(req_valid_i && req_ready_o) begin
                active<=1;base_pc<=req_pc_i;sent<=0;data<=0;
                done<={bad_word(req_pc_i+32'd8),bad_word(req_pc_i+32'd4),bad_word(req_pc_i)};
                errors<={bad_word(req_pc_i+32'd8),bad_word(req_pc_i+32'd4),bad_word(req_pc_i)};
            end
            for(k=0;k<3;k=k+1) begin
                if(qvalid[k] && qready[k]) sent[k]<=1;
                if(svalid[k] && sready[k]) begin
                    done[k]<=1;data[k*32+:32]<=sdata[k*32+:32];errors[k]<=serror[k];
                end
            end
            if(rsp_valid_o && rsp_ready_i) active<=0;
        end
    end
endmodule
`default_nettype wire
