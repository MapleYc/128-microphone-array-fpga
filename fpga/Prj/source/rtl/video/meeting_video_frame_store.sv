`default_nettype none
// Three DDR slots: one writing, one latest complete, one being transmitted.
// Single-beat HMIC AXI operations: WREADY consumes data (there is no WVALID),
// RVALID cannot be stalled. A read is issued only with a free output register.
module meeting_video_frame_store #(
    parameter integer WIDTH=1280,HEIGHT=720,FRAME_PERIOD_CYCLES=12000000,
    parameter integer FRAME_WORDS=WIDTH*HEIGHT/16,
    parameter [27:0] SLOT_BYTES=28'h0200000
)(
    input wire clk,rst_n,send_enable,
    input wire [257:0] in_data,input wire in_valid,output wire in_ready,
    output reg [257:0] out_data,output reg out_valid,input wire out_ready,
    output wire [27:0] awaddr,output wire awvalid,input wire awready,
    output wire [255:0] wdata,output wire [31:0] wstrb,input wire wready,
    output wire [27:0] araddr,output wire arvalid,input wire arready,
    input wire [255:0] rdata,input wire rvalid,rlast,
    output reg [31:0] completed,rejected,superseded,read_frames,
    output reg protocol_fault
);
    localparam W_IDLE=0,W_ADDR=1,W_DATA=2;
    localparam R_IDLE=0,R_HEADER=1,R_ADDR=2,R_DATA=3,R_SEND=4,R_HEADER_WAIT=5;
    reg [1:0] ws,write_slot,ready_slot,read_slot;
    reg [2:0] rs;
    reg writing,ready_valid,write_bad;
    reg [31:0] write_count,read_count,timer;
    reg [95:0] write_meta,ready_meta,read_meta;
    reg [255:0] write_word;
    wire read_busy=rs!=R_IDLE;
    wire incoming_start=in_valid && in_ready && in_data[257:256]==1;
    wire incoming_end=in_valid && in_ready && in_data[257:256]==2;
    wire good_end=incoming_end && writing && !write_bad && in_data[32] &&
        in_data[31:0]==WIDTH*HEIGHT && write_count==FRAME_WORDS;
    wire start_read=rs==R_IDLE && ready_valid && send_enable && timer==0 && !good_end;
    reg [1:0] free_slot;
    integer k;
    always @*begin
        free_slot=0;
        for(k=0;k<3;k=k+1)
            if((!read_busy || read_slot!=k) && (!ready_valid || ready_slot!=k))free_slot=k[1:0];
    end
    assign in_ready=ws==W_IDLE;
    assign awaddr=write_slot*SLOT_BYTES+{write_count[22:0],5'b0};
    assign awvalid=ws==W_ADDR;
    assign wdata=write_word;
    assign wstrb=ws!=W_IDLE ? 32'hffffffff : 32'b0;
    assign araddr=read_slot*SLOT_BYTES+{read_count[22:0],5'b0};
    assign arvalid=rs==R_ADDR;
    always @(posedge clk or negedge rst_n)begin
        if(!rst_n)begin
            ws<=W_IDLE;rs<=R_IDLE;write_slot<=0;ready_slot<=1;read_slot<=2;
            writing<=0;ready_valid<=0;write_bad<=0;write_count<=0;read_count<=0;timer<=0;
            write_meta<=0;ready_meta<=0;read_meta<=0;write_word<=0;out_data<=0;out_valid<=0;
            completed<=0;rejected<=0;superseded<=0;read_frames<=0;protocol_fault<=0;
        end else begin
            if(timer!=0)timer<=timer-1'b1;
            if(out_ready)out_valid<=0;
            case(ws)
                W_IDLE:if(in_valid)begin
                    case(in_data[257:256])
                        1:begin
                            if(writing)rejected<=rejected+1'b1;
                            write_slot<=free_slot;writing<=1;write_bad<=0;write_count<=0;write_meta<=in_data[95:0];
                        end
                        0:if(writing)begin
                            if(write_count<FRAME_WORDS)begin write_word<=in_data[255:0];ws<=W_ADDR;end
                            else write_bad<=1;
                        end
                        2:begin
                            writing<=0;
                            if(good_end)begin
                                ready_slot<=write_slot;ready_meta<=write_meta;ready_valid<=1;completed<=completed+1'b1;
                                if(ready_valid)superseded<=superseded+1'b1;
                            end else rejected<=rejected+1'b1;
                        end
                        default:begin write_bad<=1;protocol_fault<=1;end
                    endcase
                end
                W_ADDR:if(awready)begin
                    if(wready)begin write_count<=write_count+1'b1;ws<=W_IDLE;end
                    else ws<=W_DATA;
                end
                W_DATA:if(wready)begin write_count<=write_count+1'b1;ws<=W_IDLE;end
                default:begin ws<=W_IDLE;protocol_fault<=1;end
            endcase
            case(rs)
                R_IDLE:if(start_read)begin
                    read_slot<=ready_slot;read_meta<=ready_meta;ready_valid<=0;read_count<=0;
                    rs<=R_HEADER;timer<=FRAME_PERIOD_CYCLES-1;
                end
                R_HEADER:if(!out_valid || out_ready)begin
                    out_data<={2'b01,160'b0,read_meta};out_valid<=1;rs<=R_HEADER_WAIT;
                end
                R_HEADER_WAIT:if(out_valid && out_ready)rs<=R_ADDR;
                R_ADDR:if(arready)rs<=R_DATA;
                R_DATA:if(rvalid)begin
                    if(!rlast)protocol_fault<=1;
                    out_data<={2'b00,rdata};out_valid<=1;rs<=R_SEND;
                end
                R_SEND:if(out_valid && out_ready)begin
                    if(read_count==FRAME_WORDS-1)begin rs<=R_IDLE;read_frames<=read_frames+1'b1;end
                    else begin read_count<=read_count+1'b1;rs<=R_ADDR;end
                end
                default:begin rs<=R_IDLE;protocol_fault<=1;end
            endcase
        end
    end
`ifndef SYNTHESIS
    initial if(FRAME_WORDS*32>SLOT_BYTES || FRAME_PERIOD_CYCLES<1)$fatal(1,"Invalid frame slots/cadence");
    always @(posedge clk)if(rst_n && writing && read_busy && write_slot==read_slot)$fatal(1,"[TB_ERROR] frame ownership collision");
`endif
endmodule
`default_nettype wire
