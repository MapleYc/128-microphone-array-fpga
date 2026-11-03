`timescale 1ns/1ps
module diag_uart_tx #(parameter integer CLKS_PER_BIT=234)(
    input wire clk,rst_n,input wire [7:0] data,input wire valid,
    output wire ready,output wire tx);
    reg [9:0] shift;
    reg [15:0] timer;
    reg [3:0] bit_index;
    reg busy;
    assign ready=!busy;
    assign tx=busy?shift[0]:1'b1;
    always @(posedge clk) begin
        if(!rst_n) begin shift<=10'h3ff;timer<=0;bit_index<=0;busy<=0;end
        else if(!busy) begin
            if(valid) begin shift<={1'b1,data,1'b0};timer<=CLKS_PER_BIT-1;bit_index<=0;busy<=1;end
        end else if(timer==0) begin
            timer<=CLKS_PER_BIT-1;
            shift<={1'b1,shift[9:1]};
            if(bit_index==9)busy<=0;
            else bit_index<=bit_index+1'b1;
        end else timer<=timer-1'b1;
    end
endmodule

module diag_uart_rx #(parameter integer CLKS_PER_BIT=234)(
    input wire clk,rst_n,rx,output reg [7:0] data,output reg valid,error);
    (* syn_preserve=1 *) reg rx_meta,rx_sync;
    reg [1:0] state;
    reg [15:0] timer;
    reg [2:0] bit_index;
    reg [7:0] shift;
    always @(posedge clk) begin
        if(!rst_n) begin
            rx_meta<=1;rx_sync<=1;state<=0;timer<=0;bit_index<=0;
            shift<=0;data<=0;valid<=0;error<=0;
        end else begin
            rx_meta<=rx;rx_sync<=rx_meta;valid<=0;error<=0;
            case(state)
                0:if(!rx_sync)begin state<=1;timer<=CLKS_PER_BIT/2-1;end
                1:if(timer!=0)timer<=timer-1'b1;
                  else if(rx_sync)state<=0; // Reject a false start shorter than half a bit.
                  else begin state<=2;timer<=CLKS_PER_BIT-1;bit_index<=0;end
                2:if(timer!=0)timer<=timer-1'b1;
                  else begin
                      shift[bit_index]<=rx_sync;timer<=CLKS_PER_BIT-1;
                      if(bit_index==7)state<=3;else bit_index<=bit_index+1'b1;
                  end
                3:if(timer!=0)timer<=timer-1'b1;
                  else begin
                      if(rx_sync)begin data<=shift;valid<=1;state<=0;end
                      else begin error<=1;state<=0;end
                  end
            endcase
        end
    end
endmodule
