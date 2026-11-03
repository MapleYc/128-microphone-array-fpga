`default_nettype none
// 六个异步读口、三个同步写口；同址写入时最年轻槽位优先。
// 各槽使用独立单写 LUTRAM，通过可复位的最新写入者标签选择读值。
module rv32_regfile_6r3w(
    input wire clk,rst_n,
    input wire we0_i,input wire [4:0] waddr0_i,input wire [31:0] wdata0_i,
    input wire we1_i,input wire [4:0] waddr1_i,input wire [31:0] wdata1_i,
    input wire we2_i,input wire [4:0] waddr2_i,input wire [31:0] wdata2_i,
    input wire [4:0] raddr0_1_i,output wire [31:0] rdata0_1_o,
    input wire [4:0] raddr0_2_i,output wire [31:0] rdata0_2_o,
    input wire [4:0] raddr1_1_i,output wire [31:0] rdata1_1_o,
    input wire [4:0] raddr1_2_i,output wire [31:0] rdata1_2_o,
    input wire [4:0] raddr2_1_i,output wire [31:0] rdata2_1_o,
    input wire [4:0] raddr2_2_i,output wire [31:0] rdata2_2_o
);
    wire [1:0] owner[0:31];
    wire [31:0] initialized;
    wire [4:0] read_addr[0:5];
    wire [31:0] read_data[0:5];
    assign read_addr[0]=raddr0_1_i;assign read_addr[1]=raddr0_2_i;
    assign read_addr[2]=raddr1_1_i;assign read_addr[3]=raddr1_2_i;
    assign read_addr[4]=raddr2_1_i;assign read_addr[5]=raddr2_2_i;
    assign rdata0_1_o=read_data[0];assign rdata0_2_o=read_data[1];
    assign rdata1_1_o=read_data[2];assign rdata1_2_o=read_data[3];
    assign rdata2_1_o=read_data[4];assign rdata2_2_o=read_data[5];
    assign owner[0]=0;assign initialized[0]=0;
    genvar g;
    generate for(g=1;g<32;g=g+1) begin : g_tag
        reg [1:0] latest;
        reg used;
        assign owner[g]=latest;assign initialized[g]=used;
        always @(posedge clk or negedge rst_n) begin
            if(!rst_n) begin used<=0;latest<=0;end
            else if(we2_i && waddr2_i==g) begin used<=1;latest<=2;end
            else if(we1_i && waddr1_i==g) begin used<=1;latest<=1;end
            else if(we0_i && waddr0_i==g) begin used<=1;latest<=0;end
        end
    end endgenerate
    genvar p;
    generate for(p=0;p<6;p=p+1) begin : g_read_port
        reg [31:0] bank0[0:31] /* synthesis syn_ramstyle="lut_ram" */;
        reg [31:0] bank1[0:31] /* synthesis syn_ramstyle="lut_ram" */;
        reg [31:0] bank2[0:31] /* synthesis syn_ramstyle="lut_ram" */;
        always @(posedge clk) begin
            if(rst_n && we0_i && waddr0_i!=0) bank0[waddr0_i]<=wdata0_i;
            if(rst_n && we1_i && waddr1_i!=0) bank1[waddr1_i]<=wdata1_i;
            if(rst_n && we2_i && waddr2_i!=0) bank2[waddr2_i]<=wdata2_i;
        end
        wire [4:0] addr=read_addr[p];
        wire [31:0] stored= !initialized[addr] ? 32'b0 :
            owner[addr]==2 ? bank2[addr] : owner[addr]==1 ? bank1[addr] : bank0[addr];
        // 复位期间仍保留组合写穿透；持久化存储写入受复位条件限制。
        assign read_data[p]=addr==0 ? 32'b0 :
            (we2_i && waddr2_i==addr && waddr2_i!=0) ? wdata2_i :
            (we1_i && waddr1_i==addr && waddr1_i!=0) ? wdata1_i :
            (we0_i && waddr0_i==addr && waddr0_i!=0) ? wdata0_i : stored;
    end endgenerate
    // 仅仿真保留完整寄存器诊断视图，避免综合出额外 32 个读口破坏分布式 RAM 推断。
    // synthesis translate_off
    wire [31:0] x[0:31];
    assign x[0]=0;
    genvar v;
    generate for(v=1;v<32;v=v+1) begin : g_debug_view
        assign x[v]=!initialized[v] ? 32'b0 : owner[v]==2 ? g_read_port[0].bank2[v] :
            owner[v]==1 ? g_read_port[0].bank1[v] : g_read_port[0].bank0[v];
    end endgenerate
    // synthesis translate_on
endmodule
`default_nettype wire
