`default_nettype none
// One whole buffered command and a retained owner through the last response.
module rv32_cache_burst_arbiter(
    input wire clk,rst_n,
    input wire [1:0] q_valid_i,q_write_i,output reg [1:0] q_ready_o,
    input wire [63:0] q_addr_i,q_data_i,input wire [7:0] q_strb_i,input wire [5:0] q_len_i,
    output reg [1:0] s_valid_o,s_error_o,s_last_o,input wire [1:0] s_ready_i,
    output reg [63:0] s_data_o,
    output wire b_valid_o,b_write_o,input wire b_ready_i,
    output wire [31:0] b_addr_o,b_data_o,output wire [3:0] b_strb_o,
    output wire [2:0] b_len_o,
    input wire r_valid_i,r_error_i,r_last_i,input wire [31:0] r_data_i,
    output wire r_ready_o,output reg fatal_o
);
    localparam [1:0] IDLE=0,SEND=1,WAIT=2,POISON=3;
    reg [1:0] state;
    reg owner,turn,wr;
    reg [31:0] addr,data;
    reg [3:0] strb;
    reg [2:0] len,beat;
    wire pick=q_valid_i[0] && q_valid_i[1] ? turn : q_valid_i[1];
    always @* begin
        q_ready_o=0;s_valid_o=0;s_error_o=0;s_last_o=0;s_data_o=0;
        if(rst_n && state==IDLE && !fatal_o && !r_valid_i) q_ready_o[pick]=1;
        if(rst_n && state==WAIT) begin
            s_valid_o[owner]=r_valid_i;s_error_o[owner]=r_error_i;
            s_last_o[owner]=r_last_i;s_data_o[owner*32+:32]=r_data_i;
        end
    end
    assign b_valid_o=rst_n && state==SEND && !fatal_o && !r_valid_i;
    assign b_write_o=wr;assign b_addr_o=addr;assign b_data_o=data;
    assign b_strb_o=strb;assign b_len_o=len;
    assign r_ready_o=rst_n && (state==WAIT ? s_ready_i[owner] :
                              (state==IDLE || state==POISON));
    wire unexpected=r_valid_i && state!=WAIT && state!=POISON;
    wire bad_last=state==WAIT && r_valid_i && r_ready_o && r_last_i!=(beat==len);
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            state<=IDLE;owner<=0;turn<=0;wr<=0;addr<=0;data<=0;strb<=0;len<=0;beat<=0;fatal_o<=0;
        end else if(unexpected || bad_last) begin state<=POISON;fatal_o<=1;end
        else case(state)
            IDLE:if(q_valid_i[pick] && q_ready_o[pick]) begin
                owner<=pick;turn<=!pick;wr<=q_write_i[pick];
                addr<=q_addr_i[pick*32+:32];len<=q_len_i[pick*3+:3];
                if(q_write_i[pick]) begin data<=q_data_i[pick*32+:32];strb<=q_strb_i[pick*4+:4];end
                else begin data<=0;strb<=0;end
                beat<=0;state<=SEND;
            end
            SEND:if(b_valid_o && b_ready_i) state<=WAIT;
            WAIT:if(r_valid_i && r_ready_o) begin
                if(r_last_i) state<=IDLE;else beat<=beat+1'b1;
            end
            default:state<=POISON;
        endcase
    end
endmodule
`default_nettype wire
