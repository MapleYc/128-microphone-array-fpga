`default_nettype none
// 总线响应约定：响应组合输出在请求握手处采样；每次握手仅执行一次副作用。
// 所有算法接口与 clk 同域。跨域必须在本模块之外添加异步 FIFO/握手桥。
module array128_audio_mmio #(
    parameter [31:0] BASE_ADDR = 32'h20006000
)(
    input wire clk, rst_n,
    input wire req_valid_i, req_write_i,
    input wire [31:0] req_addr_i, req_data_i,
    input wire [3:0] req_strb_i,
    output wire req_ready_o,
    output reg [31:0] rsp_data_o,
    output reg rsp_error_o,
    output reg cfg_valid_o,
    input wire cfg_ready_i,
    output wire [31:0] cfg_control_o,
    input wire sample_tick_i, frame_tick_i, capture_overflow_i,
    input wire result_valid_i,
    output wire result_ready_o,
    input wire signed [31:0] result_azimuth_i, result_elevation_i,
    input wire [15:0] result_confidence_i,
    input wire [31:0] result_frame_i,
    output wire irq_o
);
    localparam [31:0] CONTROL_MASK = 32'h0000ff07;
    reg [31:0] control_reg, sample_count, frame_count;
    reg [3:0] irq_enable;
    reg [2:0] events;
    reg result_pending;
    reg [31:0] result_seq, result_azimuth, result_elevation, result_frame;
    reg [15:0] result_confidence;
    wire [31:0] offset = req_addr_i - BASE_ADDR;
    wire control_write = req_write_i && offset == 32'h004 && |req_strb_i;
    // 配置邮箱满时仅阻塞下一次配置写；状态读取/结果 ACK 仍能前进。
    assign req_ready_o = rst_n && !(control_write && cfg_valid_o && !cfg_ready_i);
    wire accepted = req_valid_i && req_ready_o && !rsp_error_o;
    wire write_fire = accepted && req_write_i;
    wire ack_result = write_fire && offset == 32'h034 && req_strb_i[0] && req_data_i[0];
    wire [2:0] clear_events = (write_fire && offset == 32'h010 && req_strb_i[0]) ? req_data_i[2:0] : 3'b0;
    wire cfg_fire = cfg_valid_o && cfg_ready_i;
    // CPU 读取期间保持整组结果；ACK 可与下一组结果握手同拍发生。
    assign result_ready_o = rst_n && (!result_pending || ack_result);
    wire result_fire = result_valid_i && result_ready_o;
    wire result_blocked = result_valid_i && !result_ready_o;
    assign cfg_control_o = control_reg;
    assign irq_o = rst_n && ((irq_enable[0] && result_pending) ||
                            (irq_enable[1] && events[0]) ||
                            (irq_enable[2] && events[1]) ||
                            (irq_enable[3] && events[2]));
    function automatic [31:0] merge_bytes;
        input [31:0] old_value, new_value;
        input [3:0] strobes;
        integer b;
        begin
            merge_bytes = old_value;
            for (b = 0; b < 4; b = b + 1)
                if (strobes[b]) merge_bytes[b*8+:8] = new_value[b*8+:8];
        end
    endfunction
    always @* begin
        rsp_data_o = 32'b0;
        rsp_error_o = 1'b0;
        case (offset)
            32'h000: begin rsp_data_o = 32'ha1280001; rsp_error_o = req_write_i; end
            32'h004: rsp_data_o = control_reg;
            32'h008: begin rsp_data_o = {27'b0, events[2], events[1], events[0], cfg_valid_o, result_pending}; rsp_error_o = req_write_i; end
            32'h00c: rsp_data_o = {28'b0, irq_enable};
            32'h010: rsp_data_o = {29'b0, events};
            32'h014: begin rsp_data_o = sample_count; rsp_error_o = req_write_i; end
            32'h018: begin rsp_data_o = frame_count; rsp_error_o = req_write_i; end
            32'h020: begin rsp_data_o = result_seq; rsp_error_o = req_write_i; end
            32'h024: begin rsp_data_o = result_azimuth; rsp_error_o = req_write_i; end
            32'h028: begin rsp_data_o = result_elevation; rsp_error_o = req_write_i; end
            32'h02c: begin rsp_data_o = {16'b0, result_confidence}; rsp_error_o = req_write_i; end
            32'h030: begin rsp_data_o = result_frame; rsp_error_o = req_write_i; end
            32'h034: rsp_data_o = 32'b0;
            default: rsp_error_o = 1'b1;
        endcase
        if (req_addr_i[1:0] != 2'b00) begin
            rsp_error_o = 1'b1;
            rsp_data_o = 32'b0;
        end
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            control_reg <= 0;
            cfg_valid_o <= 0;
            irq_enable <= 0;
            events <= 0;
            sample_count <= 0;
            frame_count <= 0;
            result_pending <= 0;
            result_seq <= 0;
            result_azimuth <= 0;
            result_elevation <= 0;
            result_confidence <= 0;
            result_frame <= 0;
        end else begin
            // 同拍清除与新事件冲突时，新事件优先，避免软件清除吞事件。
            events <= (events & ~clear_events) | {cfg_fire, result_blocked, capture_overflow_i};
            if (sample_tick_i) sample_count <= sample_count + 32'd1;
            if (frame_tick_i) frame_count <= frame_count + 32'd1;
            if (cfg_fire) cfg_valid_o <= 1'b0;
            if (write_fire && offset == 32'h004 && |req_strb_i) begin
                control_reg <= merge_bytes(control_reg, req_data_i, req_strb_i) & CONTROL_MASK;
                cfg_valid_o <= 1'b1;
            end
            if (write_fire && offset == 32'h00c && req_strb_i[0]) irq_enable <= req_data_i[3:0];
            if (ack_result) result_pending <= 1'b0;
            if (result_fire) begin
                result_pending <= 1'b1;
                result_seq <= result_seq + 32'd1;
                result_azimuth <= result_azimuth_i;
                result_elevation <= result_elevation_i;
                result_confidence <= result_confidence_i;
                result_frame <= result_frame_i;
            end
        end
    end
endmodule
`default_nettype wire
