`default_nettype none
// 8N1 receiver. FIFO read and error clear are explicit accepted bus pulses.
module rv32_uart_rx_fifo #(
    parameter integer CLOCK_HZ=63000000,
    parameter integer BAUD=115200,
    parameter integer FIFO_LOG2=4
)(
    input wire clk,rst_n,rx_i,
    input wire pop_i,clear_overflow_i,clear_framing_i,
    output wire [7:0] data_o,
    output wire valid_o,full_o,
    output reg overflow_o,framing_o,
    output wire [FIFO_LOG2:0] count_o
);
    localparam integer DIV=CLOCK_HZ/BAUD;
    localparam integer TIMER_W=(DIV>1) ? $clog2(DIV) : 1;
    localparam integer DEPTH=1<<FIFO_LOG2;
    localparam [2:0] IDLE=0,START=1,DATA=2,STOP=3,WAIT_IDLE=4;
    reg rx_meta,rx_sync;
    reg [2:0] state,bit_index;
    reg [TIMER_W-1:0] timer;
    reg [7:0] shift,received;
    reg receive_pulse,frame_error;
    reg [7:0] fifo[0:DEPTH-1];
    reg [FIFO_LOG2-1:0] head,tail;
    reg [FIFO_LOG2:0] count;
    wire pop=pop_i && valid_o;
    wire push=receive_pulse && (!full_o || pop);
    assign valid_o=count!=0;
    assign full_o=count==DEPTH;
    assign count_o=count;
    assign data_o=valid_o ? fifo[head] : 8'b0;

    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin rx_meta<=1;rx_sync<=1;end
        else begin rx_meta<=rx_i;rx_sync<=rx_meta;end
    end
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            state<=IDLE;bit_index<=0;timer<=0;shift<=0;
            received<=0;receive_pulse<=0;frame_error<=0;
        end else begin
            receive_pulse<=0;frame_error<=0;
            case(state)
                IDLE:if(!rx_sync) begin state<=START;timer<=DIV/2-1;end
                START:if(timer!=0) timer<=timer-1'b1;
                    else if(!rx_sync) begin state<=DATA;bit_index<=0;timer<=DIV-1;end
                    else state<=IDLE;
                DATA:if(timer!=0) timer<=timer-1'b1;
                    else begin
                        shift[bit_index]<=rx_sync;timer<=DIV-1;
                        if(bit_index==7) state<=STOP;else bit_index<=bit_index+1'b1;
                    end
                STOP:if(timer!=0) timer<=timer-1'b1;
                    else if(rx_sync) begin received<=shift;receive_pulse<=1;state<=IDLE;end
                    else begin frame_error<=1;state<=WAIT_IDLE;end
                WAIT_IDLE:if(rx_sync) state<=IDLE;
                default:state<=IDLE;
            endcase
        end
    end
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin head<=0;tail<=0;count<=0;overflow_o<=0;framing_o<=0;end
        else begin
            if(clear_overflow_i) overflow_o<=0;
            if(clear_framing_i) framing_o<=0;
            if(frame_error) framing_o<=1;
            if(receive_pulse && full_o && !pop) overflow_o<=1;
            if(push) begin fifo[tail]<=received;tail<=tail+1'b1;end
            if(pop) head<=head+1'b1;
            case({push,pop})
                2'b10:count<=count+1'b1;
                2'b01:count<=count-1'b1;
                default:begin end
            endcase
        end
    end
    // synthesis translate_off
    initial if(DIV<8 || FIFO_LOG2<1) $fatal(1,"[TB_ERROR] UART RX invalid parameters");
    // synthesis translate_on
endmodule
`default_nettype wire
