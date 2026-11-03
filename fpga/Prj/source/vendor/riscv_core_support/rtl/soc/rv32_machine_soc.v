`default_nettype none
// Functional SoC with optional peripheral extension; original local RAM path retained.
module rv32_machine_soc #(
    parameter integer CLOCK_HZ=27000000, UART_BAUD=115200,
    parameter integer UART_FIFO_ADDR_WIDTH=11,
    parameter integer UART_INTER_BYTE_GAP_CYCLES=0,
    parameter integer FEATURE_PERIPHERALS=0
)(
    input wire clk,rst_n,irq_external_i,
    input wire uart_rx_i,
    input wire [3:0] gpio_i,
    output wire [3:0] gpio_o,
    output wire uart_tx,
    output reg benchmark_done,
    output reg [31:0] exit_status,
    output wire trap_valid_o,
    output wire [31:0] trap_pc_o,trap_cause_o,trap_tval_o
);
    reg [2:0] reset_release;
    wire reset_n=reset_release[2];
    always @(posedge clk or negedge rst_n)
        if(!rst_n) reset_release<=0;else reset_release<={reset_release[1:0],1'b1};
    reg [1:0] irq_sync;
    always @(posedge clk or negedge reset_n)
        if(!reset_n) irq_sync<=0;else irq_sync<={irq_sync[0],irq_external_i};
    wire iv,ir;wire [31:0] ia;
    wire [31:0] ia1=ia+32'd4,ia2=ia+32'd8;
    wire [31:0] next_pc,preview0,preview1;
    wire [31:0] preview_load1;
    wire [1:0] preview_valid;
    wire [13:0] next_word1,next_word2;
    wire [95:0] idata;
    wire [2:0] ierr={ia2[31:16]!=0,ia1[31:16]!=0,ia[31:16]!=0};
    rv32_imem_dual #(.THIRD_ALU_SLOT(1)) u_imem(
        .addr0_i(next_pc[15:2]),.rdata0_o(idata[31:0]),
        .addr1_i(next_word1),.rdata1_o(idata[63:32]),
        .addr2_i(next_word2),.rdata2_o(idata[95:64]),.clk(clk),.rst(!reset_n));
    wire [1:0] dv,dw,dr,dsready;
    wire [31:0] da[0:1],wd[0:1],rd[0:1],ram[0:1];
    wire [3:0] st[0:1];
    wire [1:0] derr;
    wire sel0=da[0][31:16]==16'h1000,sel1=da[1][31:16]==16'h1000;
    // Classify the incoming descriptor at the existing RAM/EX capture edge.
    // The word address matches the original LSU bus address (low two bits zero).
    function automatic [3:0] response_address_class;
        input [31:0] address;
        begin
            if(address[31:16]==16'h1000) response_address_class=4'd1;
            else case({address[31:2],2'b00})
                32'h20000000:response_address_class=4'd2;
                32'h20000004:response_address_class=4'd3;
                32'h20002000:response_address_class=4'd4;
                32'h20002004:response_address_class=4'd5;
                32'h20002008:response_address_class=4'd6;
                32'h20002010:response_address_class=4'd7;
                32'h20002014:response_address_class=4'd8;
                32'h20002018:response_address_class=4'd9;
                32'h2000f000:response_address_class=4'd10;
                default:begin
                    if(FEATURE_PERIPHERALS &&
                       ({address[31:2],2'b00}==32'h20000008 ||
                        {address[31:2],2'b00}==32'h2000000c ||
                        {address[31:2],2'b00}==32'h20000010 ||
                        address[31:5]==(32'h20003000>>5))) response_address_class=4'd11;
                    else response_address_class=4'd0;
                end
            endcase
        end
    endfunction
    reg [3:0] response_class0;
    reg response_ram1;
    always @(posedge clk or negedge reset_n) begin
        if(!reset_n) begin response_class0<=0;response_ram1<=0;end
        else begin
            if(preview_valid[0]) response_class0<=response_address_class(preview0);
            if(preview_valid[1]) response_ram1<=preview_load1[31:16]==16'h1000;
        end
    end
    wire [1:0] response_ram={response_ram1,response_class0==4'd1};
    wire feature_access=FEATURE_PERIPHERALS && response_class0==4'd11;
    wire feature_ready,feature_error,feature_irq;
    wire [31:0] feature_data;
    generate if(FEATURE_PERIPHERALS) begin : g_peripherals
        rv32_feature_peripherals #(.CLOCK_HZ(CLOCK_HZ),.UART_BAUD(UART_BAUD)) u_peripherals(
            .clk(clk),.rst_n(reset_n),.req_valid_i(dv[0] && feature_access),
            .req_write_i(dw[0]),.req_addr_i(da[0]),.req_data_i(wd[0]),.req_strb_i(st[0]),
            .req_ready_o(feature_ready),.rsp_data_o(feature_data),.rsp_error_o(feature_error),
            .uart_rx_i(uart_rx_i),.gpio_i(gpio_i),.gpio_o(gpio_o),.irq_o(feature_irq),.irq_sources_o());
    end else begin : g_no_peripherals
        assign feature_ready=1'b1;assign feature_error=1'b1;assign feature_data=32'b0;
        assign feature_irq=1'b0;assign gpio_o=4'b0;
    end endgenerate
    wire write0=dv[0] && dr[0] && dw[0] && !derr[0];
    wire write1=dv[1] && dr[1] && dw[1] && !derr[1];
    // Scheduler never issues two writes in one group. Port1 only serves LW/SW.
    wire ram_write=(write0 && sel0)||(write1 && sel1);
    wire [31:0] ram_wa=write1 ? da[1] : da[0];
    wire [31:0] ram_wd=write1 ? wd[1] : wd[0];
    wire [3:0] ram_st=write1 ? st[1] : st[0];
    genvar r;
    generate for(r=0;r<2;r=r+1) begin : g_ram
        reg [31:0] held_read_address;
        reg [31:0] write_forward_data;
        reg [3:0] write_forward_mask;
        wire [31:0] preview_address=r==0 ? preview0 : preview_load1;
        wire [31:0] read_address=preview_valid[r] ? preview_address : held_read_address;
        wire [31:0] raw_data;
        // RAM samples the incoming EX group's address at the EX capture edge.
        // An older same-edge store must forward only the bytes it writes.
        always @(posedge clk or negedge reset_n) begin
            if(!reset_n) begin
                held_read_address<=0;write_forward_data<=0;write_forward_mask<=0;
            end else begin
                if(preview_valid[r]) held_read_address<=preview_address;
                write_forward_data<=ram_wd;
                write_forward_mask<=(ram_write && ram_wa[15:2]==read_address[15:2]) ? ram_st : 4'b0;
            end
        end
        genvar b;
        for(b=0;b<4;b=b+1) begin : g_byte_forward
            assign ram[r][b*8+:8]=write_forward_mask[b] ? write_forward_data[b*8+:8] : raw_data[b*8+:8];
        end
        rv32_dmem u_ram(.wr_data(ram_wd),.wr_addr(ram_wa[15:2]),.wr_en(ram_write),
            .wr_clk(clk),.wr_rst(!reset_n),.wr_byte_en(ram_st),.rd_data(raw_data),
            .rd_addr(read_address[15:2]),.rd_clk(clk),.rd_rst(!reset_n));
    end endgenerate
    reg [63:0] cycle_counter,mtimecmp;
    reg msip;
    reg [31:0] marker_value;
    wire uart_ready,uart_busy,uart_overflow;
    wire uart_write=write0 && da[0]==32'h20000000 && |st[0];
    reg [7:0] uart_byte;
    always @* casez(st[0])
        4'b???1:uart_byte=wd[0][7:0];4'b??10:uart_byte=wd[0][15:8];
        4'b?100:uart_byte=wd[0][23:16];default:uart_byte=wd[0][31:24];
    endcase
    rv32_uart_tx_fifo #(.CLOCK_HZ(CLOCK_HZ),.BAUD(UART_BAUD),
        .FIFO_ADDR_WIDTH(UART_FIFO_ADDR_WIDTH),.BLOCK_RAM(1),
        .INTER_BYTE_GAP_CYCLES(UART_INTER_BYTE_GAP_CYCLES)) u_uart(
        .clk(clk),.rst_n(reset_n),.write_valid_i(uart_write),.write_data_i(uart_byte),
        .write_ready_o(uart_ready),.busy_o(uart_busy),.overflow_o(uart_overflow),.tx_o(uart_tx));
    wire mmio_read=da[0]==32'h20000004 || da[0]==32'h20002000 || da[0]==32'h20002004 ||
        da[0]==32'h20002008 || da[0]==32'h20002010 || da[0]==32'h20002014 ||
        da[0]==32'h20002018 || da[0]==32'h2000f000;
    wire mmio_write=da[0]==32'h20000000 || da[0]==32'h20002008 ||
        da[0]==32'h20002000 || da[0]==32'h20002004 ||
        da[0]==32'h20002010 || da[0]==32'h20002014 || da[0]==32'h20002018 || da[0]==32'h2000f000;
    wire response_shared_mmio=response_class0>=4'd4 && response_class0<=4'd10;
    wire response_readable=response_class0==4'd1 || response_class0==4'd3 || response_shared_mmio;
    wire response_writable=response_class0==4'd1 || response_class0==4'd2 || response_shared_mmio;
    assign derr[0]=dv[0] ? (feature_access ? feature_error : !(dw[0] ? response_writable : response_readable)) : 1'b1;
    assign derr[1]=!sel1;
    assign dr[0]=reset_n && !(dv[0] && dw[0] && da[0]==32'h20000000 && !uart_ready) &&
                  (!feature_access || feature_ready);
    assign dr[1]=reset_n;
    reg [31:0] mmio_data;
    always @* begin
        case(response_class0)
            4'd3:mmio_data={29'b0,uart_overflow,uart_busy,uart_ready};
            4'd4:mmio_data=cycle_counter[31:0];
            4'd5:mmio_data=cycle_counter[63:32];
            4'd6:mmio_data=marker_value;
            4'd7:mmio_data=mtimecmp[31:0];
            4'd8:mmio_data=mtimecmp[63:32];
            4'd9:mmio_data={31'b0,msip};
            4'd10:mmio_data=exit_status;
            default:mmio_data=0;
        endcase
    end
    assign rd[0]=dv[0] ? (response_class0==4'd1 ? ram[0] : (feature_access ? feature_data : mmio_data)) : 32'b0;
    assign rd[1]=ram[1];
    function [31:0] merge_bytes;
        input [31:0] old_value,new_value;input [3:0] enables;
        integer b;
        begin
            merge_bytes=old_value;
            for(b=0;b<4;b=b+1) if(enables[b]) merge_bytes[b*8+:8]=new_value[b*8+:8];
        end
    endfunction
    always @(posedge clk or negedge reset_n) begin
        if(!reset_n) begin
            cycle_counter<=0;mtimecmp<=64'hffffffffffffffff;msip<=0;
            marker_value<=0;benchmark_done<=0;exit_status<=0;
        end else begin
            cycle_counter<=cycle_counter+64'd1;
            if(write0) case(da[0])
                32'h20002000:cycle_counter<={cycle_counter[63:32],merge_bytes(cycle_counter[31:0],wd[0],st[0])};
                32'h20002004:cycle_counter<={merge_bytes(cycle_counter[63:32],wd[0],st[0]),cycle_counter[31:0]};
                32'h20002008:marker_value<=merge_bytes(marker_value,wd[0],st[0]);
                32'h20002010:mtimecmp[31:0]<=merge_bytes(mtimecmp[31:0],wd[0],st[0]);
                32'h20002014:mtimecmp[63:32]<=merge_bytes(mtimecmp[63:32],wd[0],st[0]);
                32'h20002018:if(st[0][0]) msip<=wd[0][0];
                32'h2000f000:begin benchmark_done<=1;exit_status<=merge_bytes(exit_status,wd[0],st[0]);end
                default:begin end
            endcase
        end
    end
    rv32_core_bus #(.LOCAL_MEMORY_LOOKAHEAD(1),.LOCAL_MEMORY_RESPONSE_CLASS(1)) u_cpu(
        .clk(clk),.rst_n(reset_n),.i_req_valid_o(iv),.i_req_ready_i(reset_n),.i_req_pc_o(ia),
        .i_rsp_valid_i(iv && reset_n),.i_rsp_ready_o(ir),.i_rsp_data_i(idata),.i_rsp_error_i(ierr),
        .d0_req_valid_o(dv[0]),.d0_req_ready_i(dr[0]),.d0_req_write_o(dw[0]),
        .d0_req_addr_o(da[0]),.d0_req_data_o(wd[0]),.d0_req_strb_o(st[0]),
        .d0_rsp_valid_i(dv[0] && dr[0]),.d0_rsp_ready_o(dsready[0]),.d0_rsp_data_i(rd[0]),.d0_rsp_error_i(derr[0]),
        .d1_req_valid_o(dv[1]),.d1_req_ready_i(dr[1]),.d1_req_write_o(dw[1]),
        .d1_req_addr_o(da[1]),.d1_req_data_o(wd[1]),.d1_req_strb_o(st[1]),
        .d1_rsp_valid_i(dv[1] && dr[1]),.d1_rsp_ready_o(dsready[1]),.d1_rsp_data_i(rd[1]),.d1_rsp_error_i(derr[1]),
        .dmem_local_ram_i(response_ram),
        .irq_external_i(irq_sync[1] || feature_irq),.irq_software_i(msip),.irq_timer_i(cycle_counter>=mtimecmp),
        .commit_valid_o(),.commit_write_o(),.commit_pc_o(),.commit_instr_o(),.commit_data_o(),.commit_rd_o(),
        .trap_valid_o(trap_valid_o),.trap_pc_o(trap_pc_o),.trap_cause_o(trap_cause_o),.trap_tval_o(trap_tval_o),
        .imem_next_pc_o(next_pc),.imem_next_word1_o(next_word1),.imem_next_word2_o(next_word2),
        .dmem_preview_addr0_o(preview0),
        .dmem_preview_load1_o(preview_load1),
        .dmem_preview_addr1_o(preview1),.dmem_preview_valid_o(preview_valid));
endmodule
`default_nettype wire
