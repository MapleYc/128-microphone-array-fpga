`default_nettype none
// Application-neutral serialized burst backend using the actual generated IP.
// This is on-chip RAM, not a DDR-controller model or a DDR acceptance claim.
module rv32_cache_native_ram(
    input wire clk,rst_n,
    input wire req_valid_i,req_write_i,output wire req_ready_o,
    input wire [31:0] req_addr_i,req_data_i,
    input wire [3:0] req_strb_i,input wire [2:0] req_len_i,
    output reg rsp_valid_o,output reg [31:0] rsp_data_o,
    output reg rsp_error_o,rsp_last_o,input wire rsp_ready_i
);
    localparam [1:0] IDLE=0,READ_WAIT=1,READ_TAKE=2,RESPONSE=3;
    reg [1:0] state;
    reg data_bank,bad_read;
    reg [13:0] word_address;
    reg [2:0] beat,last_beat;
    wire text_map=req_addr_i[31:16]==16'h0000;
    wire data_map=req_addr_i[31:16]==16'h1000;
    wire legal_write=data_map && req_addr_i[1:0]==0 && req_len_i==0;
    wire legal_read=(text_map || data_map) && req_addr_i[4:0]==0 && req_len_i==7;
    assign req_ready_o=rst_n && state==IDLE;
    wire fire=req_valid_i && req_ready_o;
    wire native_write=fire && req_write_i && legal_write;
    wire [31:0] rom_data,ram_data;
    rv32_imem u_rom(.addr(word_address),.rd_data(rom_data),.clk(clk),.rst(!rst_n));
    rv32_dmem u_ram(.wr_data(req_data_i),.wr_addr(req_addr_i[15:2]),
      .wr_en(native_write),.wr_clk(clk),.wr_rst(!rst_n),.wr_byte_en(req_strb_i),
      .rd_data(ram_data),.rd_addr(word_address),.rd_clk(clk),.rd_rst(!rst_n));

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n)begin
            state<=IDLE;data_bank<=0;bad_read<=0;word_address<=0;beat<=0;last_beat<=0;
            rsp_valid_o<=0;rsp_data_o<=0;rsp_error_o<=0;rsp_last_o<=0;
        end else begin
            case(state)
                IDLE:if(fire)begin
                    beat<=0;last_beat<=req_len_i;word_address<=req_addr_i[15:2];
                    data_bank<=data_map;bad_read<=!legal_read;
                    if(req_write_i)begin
                        rsp_valid_o<=1;rsp_data_o<=0;rsp_error_o<=!legal_write;
                        rsp_last_o<=1;state<=RESPONSE;
                    end else state<=READ_WAIT;
                end
                // At command/next-beat acceptance, word_address changes after
                // the native read edge. Wait one real edge, then take that data.
                READ_WAIT:state<=READ_TAKE;
                READ_TAKE:begin
                    rsp_valid_o<=1;rsp_data_o<=bad_read ? 32'b0 : data_bank ? ram_data : rom_data;
                    rsp_error_o<=bad_read;rsp_last_o<=beat==last_beat;state<=RESPONSE;
                end
                RESPONSE:if(rsp_valid_o && rsp_ready_i)begin
                    rsp_valid_o<=0;
                    if(rsp_last_o)state<=IDLE;
                    else begin beat<=beat+1'b1;word_address<=word_address+1'b1;state<=READ_WAIT;end
                end
                default:begin state<=IDLE;rsp_valid_o<=0;end
            endcase
        end
    end
`ifndef SYNTHESIS
    // Required valid fields only; inactive payload and read write-data are ignored.
    always @(posedge clk)if(rst_n && fire)begin
        if($isunknown({req_write_i,req_addr_i,req_len_i}))begin
            $display("[TB_ERROR] native RAM required command XZ");$fatal(1);
        end
        if(req_write_i && $isunknown({req_data_i,req_strb_i}))begin
            $display("[TB_ERROR] native RAM required write payload XZ");$fatal(1);
        end
    end
`endif
endmodule
`default_nettype wire
