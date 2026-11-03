`default_nettype none
// Stateless response; every successful request handshake is exactly one access.
// MMIO is never issued speculatively by the CPU. No ready->valid dependency.
module rv32_feature_peripherals #(
    parameter integer CLOCK_HZ=63000000,UART_BAUD=115200,
    parameter integer RX_FIFO_LOG2=4
)(
    input wire clk,rst_n,
    input wire req_valid_i,req_write_i,
    input wire [31:0] req_addr_i,req_data_i,
    input wire [3:0] req_strb_i,
    output wire req_ready_o,
    output reg [31:0] rsp_data_o,
    output reg rsp_error_o,
    input wire uart_rx_i,
    input wire [3:0] gpio_i,
    output reg [3:0] gpio_o,
    output wire irq_o,
    output wire [1:0] irq_sources_o
);
    localparam [31:0] RXDATA=32'h20000008,RXSTATUS=32'h2000000c,RXIRQEN=32'h20000010;
    localparam [31:0] GIN=32'h20003000,GOUT=32'h20003004,GRISE=32'h20003008;
    localparam [31:0] GFALL=32'h2000300c,GPENDING=32'h20003010,GMASK=32'h20003014;
    wire [7:0] rx_byte;
    wire rx_valid,rx_full,rx_overflow,rx_framing;
    wire [RX_FIFO_LOG2:0] rx_count;
    reg [1:0] rx_irq_en;
    reg [3:0] gpio_meta,gpio_sync,gpio_previous;
    reg [3:0] rise_en,fall_en,pending,irq_mask;
    wire [3:0] events=((gpio_sync & ~gpio_previous) & rise_en) |
                       ((~gpio_sync & gpio_previous) & fall_en);
    assign req_ready_o=rst_n;
    wire accepted=req_valid_i && req_ready_o && !rsp_error_o;
    wire accepted_write=accepted && req_write_i && req_strb_i[0];
    wire clear_overflow=accepted_write && req_addr_i==RXSTATUS && req_data_i[2];
    wire clear_framing=accepted_write && req_addr_i==RXSTATUS && req_data_i[3];
    wire [3:0] clear_pending=(accepted_write && req_addr_i==GPENDING) ? req_data_i[3:0] : 4'b0;
    wire rx_pop=accepted && !req_write_i && req_addr_i==RXDATA;
    rv32_uart_rx_fifo #(.CLOCK_HZ(CLOCK_HZ),.BAUD(UART_BAUD),.FIFO_LOG2(RX_FIFO_LOG2)) u_rx(
        .clk(clk),.rst_n(rst_n),.rx_i(uart_rx_i),.pop_i(rx_pop),
        .clear_overflow_i(clear_overflow),.clear_framing_i(clear_framing),
        .data_o(rx_byte),.valid_o(rx_valid),.full_o(rx_full),
        .overflow_o(rx_overflow),.framing_o(rx_framing),.count_o(rx_count));
    assign irq_sources_o[0]=(rx_irq_en[0] && rx_valid) ||
                             (rx_irq_en[1] && (rx_overflow || rx_framing));
    assign irq_sources_o[1]=|(pending & irq_mask);
    assign irq_o=|irq_sources_o;
    always @* begin
        rsp_data_o=0;rsp_error_o=0;
        case(req_addr_i)
            RXDATA:begin rsp_data_o={rx_valid,23'b0,rx_byte};rsp_error_o=req_write_i;end
            RXSTATUS:rsp_data_o={28'b0,rx_framing,rx_overflow,rx_full,rx_valid};
            RXIRQEN:rsp_data_o={30'b0,rx_irq_en};
            GIN:begin rsp_data_o={28'b0,gpio_sync};rsp_error_o=req_write_i;end
            GOUT:rsp_data_o={28'b0,gpio_o};
            GRISE:rsp_data_o={28'b0,rise_en};
            GFALL:rsp_data_o={28'b0,fall_en};
            GPENDING:rsp_data_o={28'b0,pending};
            GMASK:rsp_data_o={28'b0,irq_mask};
            default:rsp_error_o=1;
        endcase
    end
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            gpio_meta<=0;gpio_sync<=0;gpio_previous<=0;
            gpio_o<=0;rise_en<=0;fall_en<=0;pending<=0;irq_mask<=0;rx_irq_en<=0;
        end else begin
            gpio_meta<=gpio_i;gpio_sync<=gpio_meta;gpio_previous<=gpio_sync;
            pending<=(pending & ~clear_pending) | events;
            if(accepted_write) case(req_addr_i)
                RXIRQEN:rx_irq_en<=req_data_i[1:0];
                GOUT:gpio_o<=req_data_i[3:0];
                GRISE:rise_en<=req_data_i[3:0];
                GFALL:fall_en<=req_data_i[3:0];
                GMASK:irq_mask<=req_data_i[3:0];
                default:begin end
            endcase
        end
    end
endmodule
`default_nettype wire
