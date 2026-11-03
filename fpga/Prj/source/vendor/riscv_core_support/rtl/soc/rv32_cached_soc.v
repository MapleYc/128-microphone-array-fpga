`default_nettype none
module rv32_cached_soc #(
    parameter integer CLOCK_HZ=63000000,UART_BAUD=115200,UART_FIFO_ADDR_WIDTH=11,
    parameter integer UART_INTER_BYTE_GAP_CYCLES=0,BUS_TIMEOUT=4096
)(
    input wire clk,rst_n,irq_external_i,uart_rx_i,input wire [3:0] gpio_i,
    output wire uart_tx,output wire [3:0] gpio_o,
    output reg benchmark_done,output reg [31:0] exit_status,
    output wire [2:0] commit_valid_o,commit_write_o,
    output wire [95:0] commit_pc_o,commit_instr_o,commit_data_o,output wire [14:0] commit_rd_o,
    output wire trap_valid_o,output wire [31:0] trap_pc_o,trap_cause_o,trap_tval_o,
    input wire invalidate_i,output wire invalidate_done_o,output wire fatal_o,
    output wire b_req_valid_o,b_req_write_o,input wire b_req_ready_i,
    output wire [31:0] b_req_addr_o,b_req_data_o,output wire [3:0] b_req_strb_o,
    output wire [2:0] b_req_len_o,
    input wire b_rsp_valid_i,b_rsp_error_i,b_rsp_last_i,input wire [31:0] b_rsp_data_i,
    output wire b_rsp_ready_o
);
    reg [2:0] reset_release;wire reset_n=reset_release[2];
    always @(posedge clk or negedge rst_n)
      if(!rst_n)reset_release<=0;else reset_release<={reset_release[1:0],1'b1};
    reg [1:0] irq_sync;
    always @(posedge clk or negedge reset_n)
      if(!reset_n)irq_sync<=0;else irq_sync<={irq_sync[0],irq_external_i};
    wire iv,ir,isv,isr;wire [31:0] ipc;wire [95:0] idata;wire [2:0] ierr;
    wire [1:0] dv,dw,dr,dsv,dse,dsr;
    wire [31:0] da[0:1],wd[0:1],rd[0:1];wire [3:0] st[0:1];
    wire [31:0] preview0,preview1,preview_load1,nextpc;
    wire [13:0] nextword1,nextword2;wire [1:0] preview_valid;
    reg [1:0] local_ram;
    always @(posedge clk or negedge reset_n)begin
      if(!reset_n)local_ram<=0;
      else begin
        if(preview_valid[0])local_ram[0]<=preview0[31:16]==16'h1000;
        if(preview_valid[1])local_ram[1]<=preview_load1[31:16]==16'h1000;
      end
    end
    wire data_drained;
    reg maintaining;reg [1:0] invalidated;
    wire [1:0] inv_ready;wire accept_new=reset_n && !invalidate_i && !maintaining && !fatal_o;
    assign invalidate_done_o=maintaining && (&invalidated);
    always @(posedge clk or negedge reset_n)begin
      if(!reset_n)begin maintaining<=0;invalidated<=0;end
      else begin
        if(invalidate_i && !maintaining)begin maintaining<=1;invalidated<=0;end
        if(maintaining)begin
          if(inv_ready[0])invalidated[0]<=1;
          if(inv_ready[1] && data_drained)invalidated[1]<=1;
          if((&invalidated) && !invalidate_i)maintaining<=0;
        end
      end
    end
    wire [1:0] cqv,cqw,cqr,csv,cse,csl,csr;wire [63:0] cqa,cqd,csd;
    wire [7:0] cqs;wire [5:0] cql;wire ifatal,dfatal,afatal;
    assign fatal_o=ifatal || dfatal || afatal;
    rv32_icache_triple #(.BUS_TIMEOUT(BUS_TIMEOUT)) u_icache(
      .clk(clk),.rst_n(reset_n),.req_valid_i(iv && accept_new),.req_ready_o(ir),.req_pc_i(ipc),
      .rsp_valid_o(isv),.rsp_ready_i(isr),.rsp_data_o(idata),.rsp_error_o(ierr),
      .invalidate_valid_i(maintaining && !invalidated[0]),.invalidate_ready_o(inv_ready[0]),.fatal_o(ifatal),
      .b_req_valid_o(cqv[0]),.b_req_write_o(cqw[0]),.b_req_ready_i(cqr[0]),.b_req_addr_o(cqa[31:0]),
      .b_req_data_o(cqd[31:0]),.b_req_strb_o(cqs[3:0]),.b_req_len_o(cql[2:0]),
      .b_rsp_valid_i(csv[0]),.b_rsp_ready_o(csr[0]),.b_rsp_data_i(csd[31:0]),.b_rsp_error_i(cse[0]),.b_rsp_last_i(csl[0]));
    localparam [1:0] IDLE=0,MEMORY=1,HELD=2;
    reg [1:0] route[0:1];reg [31:0] held_data[0:1];reg [1:0] held_error;
    assign data_drained=route[0]==IDLE && route[1]==IDLE;
    wire [1:0] select_ram={da[1][31:16]==16'h1000,da[0][31:16]==16'h1000};
    wire [1:0] dqv,dqr,dsv_cache,dse_cache,dsr_cache;wire [63:0] ddata_cache;
    genvar p;
    generate for(p=0;p<2;p=p+1)begin:g_data
      assign dqv[p]=dv[p] && route[p]==IDLE && select_ram[p] && accept_new;
      assign dsr_cache[p]=route[p]==MEMORY && dsr[p];
      assign dsv[p]=route[p]==MEMORY ? dsv_cache[p] : route[p]==HELD;
      assign dse[p]=route[p]==MEMORY ? dse_cache[p] : held_error[p];
      assign rd[p]=route[p]==MEMORY ? ddata_cache[p*32+:32] : held_data[p];
    end endgenerate
    rv32_cache_2way #(.PORTS(2),.BUS_TIMEOUT(BUS_TIMEOUT)) u_dcache(
      .clk(clk),.rst_n(reset_n),.req_valid_i(dqv),.req_write_i(dw),.req_ready_o(dqr),
      .req_addr_i({da[1],da[0]}),.req_data_i({wd[1],wd[0]}),.req_strb_i({st[1],st[0]}),
      .rsp_valid_o(dsv_cache),.rsp_ready_i(dsr_cache),.rsp_data_o(ddata_cache),.rsp_error_o(dse_cache),
      .invalidate_valid_i(maintaining && !invalidated[1] && data_drained),.invalidate_ready_o(inv_ready[1]),.busy_o(),.fatal_o(dfatal),
      .b_req_valid_o(cqv[1]),.b_req_write_o(cqw[1]),.b_req_ready_i(cqr[1]),.b_req_addr_o(cqa[63:32]),
      .b_req_data_o(cqd[63:32]),.b_req_strb_o(cqs[7:4]),.b_req_len_o(cql[5:3]),
      .b_rsp_valid_i(csv[1]),.b_rsp_ready_o(csr[1]),.b_rsp_data_i(csd[63:32]),.b_rsp_error_i(cse[1]),.b_rsp_last_i(csl[1]));
    rv32_cache_burst_arbiter u_arbiter(.clk(clk),.rst_n(reset_n),.q_valid_i(cqv),.q_write_i(cqw),.q_ready_o(cqr),
      .q_addr_i(cqa),.q_data_i(cqd),.q_strb_i(cqs),.q_len_i(cql),.s_valid_o(csv),.s_ready_i(csr),
      .s_data_o(csd),.s_error_o(cse),.s_last_o(csl),.b_valid_o(b_req_valid_o),.b_ready_i(b_req_ready_i),
      .b_write_o(b_req_write_o),.b_addr_o(b_req_addr_o),.b_data_o(b_req_data_o),.b_strb_o(b_req_strb_o),.b_len_o(b_req_len_o),
      .r_valid_i(b_rsp_valid_i),.r_ready_o(b_rsp_ready_o),.r_data_i(b_rsp_data_i),
      .r_error_i(b_rsp_error_i),.r_last_i(b_rsp_last_i),.fatal_o(afatal));
    wire feature_access=da[0]==32'h20000008 || da[0]==32'h2000000c || da[0]==32'h20000010 || da[0][31:5]==(32'h20003000>>5);
    wire feature_ready,feature_error,feature_irq;wire [31:0] feature_data;
    wire feature_request=dv[0] && route[0]==IDLE && !select_ram[0] && feature_access && accept_new;
    rv32_feature_peripherals #(.CLOCK_HZ(CLOCK_HZ),.UART_BAUD(UART_BAUD)) u_peripherals(
      .clk(clk),.rst_n(reset_n),.req_valid_i(feature_request),.req_write_i(dw[0]),
      .req_addr_i(da[0]),.req_data_i(wd[0]),.req_strb_i(st[0]),
      .req_ready_o(feature_ready),.rsp_data_o(feature_data),.rsp_error_o(feature_error),
      .uart_rx_i(uart_rx_i),.gpio_i(gpio_i),.gpio_o(gpio_o),.irq_o(feature_irq),.irq_sources_o());
    wire uart_ready,uart_busy,uart_overflow;
    assign dr[0]=accept_new && route[0]==IDLE && (select_ram[0] ? dqr[0] :
      feature_access ? feature_ready : !(dw[0] && da[0]==32'h20000000 && !uart_ready));
    assign dr[1]=accept_new && route[1]==IDLE && (select_ram[1] ? dqr[1] : 1'b1);
    wire mmio_fire=dv[0] && dr[0] && !select_ram[0];
    reg [63:0] cycle_counter,mtimecmp;reg msip;reg [31:0] marker_value;
    reg mmio_error;reg [31:0] mmio_data;
    always @* begin
      mmio_error=0;mmio_data=0;
      if(feature_access)begin mmio_data=feature_data;mmio_error=feature_error;end
      else case(da[0])
        32'h20000000:if(!dw[0])mmio_error=1;
        32'h20000004:begin mmio_data={29'b0,uart_overflow,uart_busy,uart_ready};if(dw[0])mmio_error=1;end
        32'h20002000:mmio_data=cycle_counter[31:0];
        32'h20002004:mmio_data=cycle_counter[63:32];
        32'h20002008:mmio_data=marker_value;
        32'h20002010:mmio_data=mtimecmp[31:0];
        32'h20002014:mmio_data=mtimecmp[63:32];
        32'h20002018:mmio_data={31'b0,msip};
        32'h2000f000:mmio_data=exit_status;
        default:mmio_error=1;
      endcase
    end
    wire write0=mmio_fire && dw[0] && !mmio_error;
    reg [7:0] uart_byte;
    always @* casez(st[0])
      4'b???1:uart_byte=wd[0][7:0];4'b??10:uart_byte=wd[0][15:8];
      4'b?100:uart_byte=wd[0][23:16];default:uart_byte=wd[0][31:24];
    endcase
    rv32_uart_tx_fifo #(.CLOCK_HZ(CLOCK_HZ),.BAUD(UART_BAUD),.FIFO_ADDR_WIDTH(UART_FIFO_ADDR_WIDTH),
      .BLOCK_RAM(1),.INTER_BYTE_GAP_CYCLES(UART_INTER_BYTE_GAP_CYCLES)) u_uart(
      .clk(clk),.rst_n(reset_n),.write_valid_i(write0 && da[0]==32'h20000000 && |st[0]),
      .write_data_i(uart_byte),.write_ready_o(uart_ready),.busy_o(uart_busy),.overflow_o(uart_overflow),.tx_o(uart_tx));
    function [31:0] merge_bytes;
      input [31:0] old_value,new_value;input [3:0] enables;integer b;
      begin merge_bytes=old_value;for(b=0;b<4;b=b+1)if(enables[b])merge_bytes[b*8+:8]=new_value[b*8+:8];end
    endfunction
    integer n;
    always @(posedge clk or negedge reset_n)begin
      if(!reset_n)begin
        route[0]<=IDLE;route[1]<=IDLE;held_data[0]<=0;held_data[1]<=0;held_error<=0;
        cycle_counter<=0;mtimecmp<=64'hffffffffffffffff;msip<=0;marker_value<=0;benchmark_done<=0;exit_status<=0;
      end else begin
        cycle_counter<=cycle_counter+1'b1;
        for(n=0;n<2;n=n+1)begin
          if(dv[n] && dr[n])begin
            if(select_ram[n])route[n]<=MEMORY;
            else begin route[n]<=HELD;held_data[n]<=n==0 ? mmio_data : 32'b0;held_error[n]<=n==0 ? mmio_error : 1'b1;end
          end
          if(dsv[n] && dsr[n])route[n]<=IDLE;
        end
        if(write0)case(da[0])
          32'h20002000:cycle_counter<={cycle_counter[63:32],merge_bytes(cycle_counter[31:0],wd[0],st[0])};
          32'h20002004:cycle_counter<={merge_bytes(cycle_counter[63:32],wd[0],st[0]),cycle_counter[31:0]};
          32'h20002008:marker_value<=merge_bytes(marker_value,wd[0],st[0]);
          32'h20002010:mtimecmp[31:0]<=merge_bytes(mtimecmp[31:0],wd[0],st[0]);
          32'h20002014:mtimecmp[63:32]<=merge_bytes(mtimecmp[63:32],wd[0],st[0]);
          32'h20002018:if(st[0][0])msip<=wd[0][0];
          32'h2000f000:begin benchmark_done<=1;exit_status<=merge_bytes(exit_status,wd[0],st[0]);end
          default:begin end
        endcase
      end
    end
    rv32_cached_core_bus u_cpu(
      .clk(clk),.rst_n(reset_n),.i_req_valid_o(iv),.i_req_ready_i(ir && accept_new),.i_req_pc_o(ipc),
      .i_rsp_valid_i(isv),.i_rsp_ready_o(isr),.i_rsp_data_i(idata),.i_rsp_error_i(ierr),
      .d0_req_valid_o(dv[0]),.d0_req_ready_i(dr[0]),.d0_req_write_o(dw[0]),.d0_req_addr_o(da[0]),.d0_req_data_o(wd[0]),.d0_req_strb_o(st[0]),
      .d0_rsp_valid_i(dsv[0]),.d0_rsp_ready_o(dsr[0]),.d0_rsp_data_i(rd[0]),.d0_rsp_error_i(dse[0]),
      .d1_req_valid_o(dv[1]),.d1_req_ready_i(dr[1]),.d1_req_write_o(dw[1]),.d1_req_addr_o(da[1]),.d1_req_data_o(wd[1]),.d1_req_strb_o(st[1]),
      .d1_rsp_valid_i(dsv[1]),.d1_rsp_ready_o(dsr[1]),.d1_rsp_data_i(rd[1]),.d1_rsp_error_i(dse[1]),.dmem_local_ram_i(local_ram),
      .irq_external_i(irq_sync[1] || feature_irq),.irq_software_i(msip),.irq_timer_i(cycle_counter>=mtimecmp),
      .commit_valid_o(commit_valid_o),.commit_write_o(commit_write_o),.commit_pc_o(commit_pc_o),.commit_instr_o(commit_instr_o),.commit_data_o(commit_data_o),.commit_rd_o(commit_rd_o),
      .trap_valid_o(trap_valid_o),.trap_pc_o(trap_pc_o),.trap_cause_o(trap_cause_o),.trap_tval_o(trap_tval_o),
      .imem_next_pc_o(nextpc),.imem_next_word1_o(nextword1),.imem_next_word2_o(nextword2),
      .dmem_preview_addr0_o(preview0),.dmem_preview_addr1_o(preview1),.dmem_preview_load1_o(preview_load1),.dmem_preview_valid_o(preview_valid));
endmodule
`default_nettype wire
