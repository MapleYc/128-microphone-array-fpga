`default_nettype none
// UART 8N1 发送器：先把 CPU 写入的字符排入 2^FIFO_ADDR_WIDTH 深度队列，
// 再按位串行发送。FIFO 满时丢弃本次写入并置位 overflow_o，溢出标志保持到复位；
// 软件应先读 write_ready_o 对应的 MMIO 状态，避免溢出。
module rv32_uart_tx_fifo #(
    parameter integer CLOCK_HZ = 27000000,
    parameter integer BAUD = 115200,
    parameter integer FIFO_ADDR_WIDTH = 11,
    // 远程实验平台的接收桥可能较慢；字节间可额外保持空闲高电平，
    // 但每一位仍按 BAUD 的 8N1 时序发送。
    parameter integer INTER_BYTE_GAP_CYCLES = 0,
    parameter BLOCK_RAM = 1'b0
)(
    input  wire       clk,
    input  wire       rst_n,
    input  wire       write_valid_i,
    input  wire [7:0] write_data_i,
    output wire       write_ready_o,
    output wire       busy_o,
    output reg        overflow_o,
    output reg        tx_o
);
    // 四舍五入得到每位需要的系统时钟数，并保证计数器至少有两个状态。
    localparam integer BAUD_DIV_RAW = (CLOCK_HZ + (BAUD / 2)) / BAUD;
    localparam integer BAUD_DIV = (BAUD_DIV_RAW < 2) ? 2 : BAUD_DIV_RAW;
    localparam integer BAUD_COUNTER_WIDTH = $clog2(BAUD_DIV);
    localparam integer GAP_COUNTER_WIDTH =
        (INTER_BYTE_GAP_CYCLES < 2) ? 1 : $clog2(INTER_BYTE_GAP_CYCLES + 1);

    reg [FIFO_ADDR_WIDTH:0] write_ptr;
    reg [FIFO_ADDR_WIDTH:0] read_ptr;
    reg [9:0] shift_reg;
    reg [3:0] bit_index;
    reg [BAUD_COUNTER_WIDTH-1:0] baud_counter;
    reg [GAP_COUNTER_WIDTH-1:0] gap_counter;
    reg tx_active;

    // 指针多一位：低位为地址，高位绕回标志，用于区分满和空。
    wire fifo_empty = (write_ptr == read_ptr);
    wire fifo_full = (write_ptr[FIFO_ADDR_WIDTH] != read_ptr[FIFO_ADDR_WIDTH]) &&
                     (write_ptr[FIFO_ADDR_WIDTH-1:0] == read_ptr[FIFO_ADDR_WIDTH-1:0]);

    wire fifo_enqueue = rst_n && write_valid_i && !fifo_full;
    wire [7:0] fifo_head;
    generate if (BLOCK_RAM) begin : g_block_ram
        // ADS Synthesis User Guide 5.16: block_ram maps inferred RAM to DRM.
        // Data storage/read register have no reset; queue validity is in pointers.
        reg [7:0] fifo [0:(1 << FIFO_ADDR_WIDTH)-1] /* synthesis syn_ramstyle="block_ram" */;
        reg [7:0] registered_head, collision_data;
        reg collision_valid;
        wire collision = fifo_enqueue &&
                         write_ptr[FIFO_ADDR_WIDTH-1:0]==read_ptr[FIFO_ADDR_WIDTH-1:0];
        always @(posedge clk) begin
            if (fifo_enqueue)
                fifo[write_ptr[FIFO_ADDR_WIDTH-1:0]] <= write_data_i;
        end
        always @(posedge clk) begin
            registered_head <= fifo[read_ptr[FIFO_ADDR_WIDTH-1:0]];
            if (collision)
                collision_data <= write_data_i;
        end
        always @(posedge clk or negedge rst_n) begin
            if (!rst_n)
                collision_valid <= 1'b0;
            else
                collision_valid <= collision;
        end
        // An empty queue may be written on the previous edge. The synchronous
        // RAM then returned the old word; use the accepted byte for this head.
        // After read_ptr advances, a frame lasts at least 20 clocks, leaving
        // enough time to prefetch the next queued byte without delaying TX.
        assign fifo_head = collision_valid ? collision_data : registered_head;
    end else begin : g_distributed_ram
        reg [7:0] fifo [0:(1 << FIFO_ADDR_WIDTH)-1];
        always @(posedge clk) begin
            if (fifo_enqueue)
                fifo[write_ptr[FIFO_ADDR_WIDTH-1:0]] <= write_data_i;
        end
        assign fifo_head = fifo[read_ptr[FIFO_ADDR_WIDTH-1:0]];
    end endgenerate

    assign write_ready_o = !fifo_full;
    assign busy_o = tx_active || !fifo_empty;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            write_ptr   <= {(FIFO_ADDR_WIDTH+1){1'b0}};
            read_ptr    <= {(FIFO_ADDR_WIDTH+1){1'b0}};
            shift_reg   <= 10'h3ff;
            bit_index   <= 4'd0;
            baud_counter<= {BAUD_COUNTER_WIDTH{1'b0}};
            gap_counter <= {GAP_COUNTER_WIDTH{1'b0}};
            tx_active   <= 1'b0;
            overflow_o  <= 1'b0;
            tx_o        <= 1'b1;
        end else begin
            // 入队与发送可以同拍进行；满队列写入不会覆盖未发出的字符。
            if (write_valid_i) begin
                if (!fifo_full) begin
                    write_ptr <= write_ptr + {{FIFO_ADDR_WIDTH{1'b0}}, 1'b1};
                end else begin
                    overflow_o <= 1'b1;
                end
            end

            if (!tx_active) begin
                tx_o <= 1'b1;
                baud_counter <= {BAUD_COUNTER_WIDTH{1'b0}};
                bit_index <= 4'd0;
                if (gap_counter != {GAP_COUNTER_WIDTH{1'b0}}) begin
                    gap_counter <= gap_counter - 1'b1;
                end else if (!fifo_empty) begin
                    // 帧顺序为起始位 0、8 位数据低位先发、停止位 1。
                    shift_reg <= {1'b1, fifo_head, 1'b0};
                    read_ptr <= read_ptr + {{FIFO_ADDR_WIDTH{1'b0}}, 1'b1};
                    tx_active <= 1'b1;
                    tx_o <= 1'b0;
                end
            end else if (baud_counter == BAUD_DIV-1) begin
                baud_counter <= {BAUD_COUNTER_WIDTH{1'b0}};
                if (bit_index == 4'd9) begin
                    tx_active <= 1'b0;
                    tx_o <= 1'b1;
                    gap_counter <= INTER_BYTE_GAP_CYCLES;
                end else begin
                    bit_index <= bit_index + 4'd1;
                    shift_reg <= {1'b1, shift_reg[9:1]};
                    tx_o <= shift_reg[1];
                end
            end else begin
                baud_counter <= baud_counter + {{(BAUD_COUNTER_WIDTH-1){1'b0}}, 1'b1};
            end
        end
    end
endmodule
`default_nettype wire
