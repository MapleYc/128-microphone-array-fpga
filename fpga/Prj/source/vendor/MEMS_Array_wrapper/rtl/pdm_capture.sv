`timescale 1ns/1ps
// 64 shared DATA wires: ch 2*k is LR=GND, ch 2*k+1 is LR=VDD.
// Source-synchronous sampling: synchronizer latency is included in SAMPLE_TICK.
module pdm_capture #(
    parameter integer PAIRS = 64,
    parameter integer CLOCK_DIV = 40,
    parameter integer SAMPLE_TICK = 19,
    parameter integer STARTUP_CYCLES = 153600
) (
    input  wire clk, rst_n, enable,
    input  wire [PAIRS-1:0] pdm_data,
    output reg mic_clk,
    output reg [2*PAIRS-1:0] bits_o,
    output reg valid_o,
    output reg started_o
);
    localparam integer PW = $clog2(CLOCK_DIV);
    localparam integer SW = (STARTUP_CYCLES < 2) ? 1 : $clog2(STARTUP_CYCLES+1);
    reg [PW-1:0] phase;
    reg [SW-1:0] startup;
    (* syn_preserve=1 *) reg [PAIRS-1:0] data_meta, data_sync;
    reg [PAIRS-1:0] high_phase;
    integer k;

    always @(posedge clk) begin
        if (!rst_n || !enable) begin
            phase <= 0;
            startup <= 0;
            mic_clk <= 0;
            valid_o <= 0;
            started_o <= 0;
            bits_o <= 0;
            high_phase <= 0;
            data_meta <= 0;
            data_sync <= 0;
        end else begin
            data_meta <= pdm_data;
            data_sync <= data_meta;
            valid_o <= 0;
            phase <= (phase == CLOCK_DIV-1) ? 0 : phase + 1'b1;
            if (phase == 0) mic_clk <= 1;
            if (phase == CLOCK_DIV/2) mic_clk <= 0;
            if (phase == SAMPLE_TICK) high_phase <= data_sync;
            if (phase == CLOCK_DIV/2 + SAMPLE_TICK) begin
                for (k=0; k<PAIRS; k=k+1) begin
                    bits_o[2*k] <= data_sync[k];
                    bits_o[2*k+1] <= high_phase[k];
                end
                if (startup == STARTUP_CYCLES) begin
                    started_o <= 1;
                    valid_o <= 1;
                end else startup <= startup + 1'b1;
            end
        end
    end
    // synthesis translate_off
    initial begin
        if (CLOCK_DIV < 8 || CLOCK_DIV%2 || SAMPLE_TICK < 3 ||
            SAMPLE_TICK >= CLOCK_DIV/2 || STARTUP_CYCLES < 0)
            $fatal(1, "Invalid PDM capture parameters");
    end
    // synthesis translate_on
endmodule
