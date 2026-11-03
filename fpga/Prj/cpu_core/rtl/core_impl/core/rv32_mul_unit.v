`default_nettype none
// Zmmul 组合乘法单元。结果在当前 EX 周期直接由输入操作数产生；
// 需要多周期握手的调用方自行寄存结果；本模块仅提供组合接口。
module rv32_mul_unit(
    input  wire [1:0]  op_i,
    input  wire [31:0] a_i,
    input  wire [31:0] b_i,
    output wire [31:0] result_o
);
    // 四个独立、不超过 18 位的部分积复用四个 APM 乘法器。
    // 高位分块保留各自符号属性，避免完整乘积后再串联有符号高位修正减法。
    wire sign_a = (op_i == 2'd1) || (op_i == 2'd2);
    wire sign_b = (op_i == 2'd1);
    wire signed [16:0] a_lo = {1'b0, a_i[15:0]};
    wire signed [16:0] b_lo = {1'b0, b_i[15:0]};
    wire signed [16:0] a_hi = {sign_a & a_i[31], a_i[31:16]};
    wire signed [16:0] b_hi = {sign_b & b_i[31], b_i[31:16]};
    wire [31:0] p00 = a_lo * b_lo;
    wire signed [33:0] p01 = a_lo * b_hi;
    wire signed [33:0] p10 = a_hi * b_lo;
    wire signed [33:0] p11 = a_hi * b_hi;

    // 低 16 位不参与后续进位传播；其余四个 48 位项经两级进位保存压缩和一次加法合并。
    // 截断按模 2 的 64 次方处理，与 Zmmul 各运算的位级语义一致。
    wire [47:0] t00 = {32'b0, p00[31:16]};
    wire [47:0] t01 = {{14{p01[33]}}, p01};
    wire [47:0] t10 = {{14{p10[33]}}, p10};
    wire [47:0] t11 = {p11[31:0], 16'b0};
    wire [47:0] sum1 = t00 ^ t01 ^ t10;
    wire [47:0] carry1 = ((t00 & t01) | (t00 & t10) | (t01 & t10)) << 1;
    wire [47:0] sum2 = sum1 ^ carry1 ^ t11;
    wire [47:0] carry2 = ((sum1 & carry1) | (sum1 & t11) | (carry1 & t11)) << 1;
    wire [47:0] upper = sum2 + carry2;
    wire [63:0] product = {upper, p00[15:0]};

    reg [31:0] next_result;
    always @(*) begin
        case (op_i)
            2'd0: next_result = product[31:0];  // MUL
            2'd1: next_result = product[63:32]; // MULH
            2'd2: next_result = product[63:32]; // MULHSU
            2'd3: next_result = product[63:32]; // MULHU
            default: next_result = 32'b0;
        endcase
    end

    assign result_o = next_result;
endmodule
`default_nettype wire

// 四个硬乘法器的 XY 输入寄存器承担既有 ID/EX 捕获边沿。
// 不启用 MULT_REG/P_REG，不新增执行周期。
`default_nettype none
module rv32_mul_unit_capture(
    input wire clk, rst_n, capture_i,
    input wire [1:0] op_d_i, op_ex_i,
    input wire [31:0] a_d_i, b_d_i,
    output wire [31:0] result_o
);
    wire sign_a = (op_d_i == 2'd1) || (op_d_i == 2'd2);
    wire sign_b = (op_d_i == 2'd1);
    wire [16:0] a_lo = {1'b0,a_d_i[15:0]};
    wire [16:0] b_lo = {1'b0,b_d_i[15:0]};
    wire [16:0] a_hi = {sign_a & a_d_i[31],a_d_i[31:16]};
    wire [16:0] b_hi = {sign_b & b_d_i[31],b_d_i[31:16]};
    wire [33:0] p00,p01,p10,p11;
    rv32_mul_capture_apm u_00(clk,rst_n,capture_i,a_lo,b_lo,p00);
    rv32_mul_capture_apm u_01(clk,rst_n,capture_i,a_lo,b_hi,p01);
    rv32_mul_capture_apm u_10(clk,rst_n,capture_i,a_hi,b_lo,p10);
    rv32_mul_capture_apm u_11(clk,rst_n,capture_i,a_hi,b_hi,p11);
    wire [47:0] t00 = {32'b0,p00[31:16]};
    wire [47:0] t01 = {{14{p01[33]}},p01};
    wire [47:0] t10 = {{14{p10[33]}},p10};
    wire [47:0] t11 = {p11[31:0],16'b0};
    wire [47:0] sum1 = t00 ^ t01 ^ t10;
    wire [47:0] carry1 = ((t00 & t01) | (t00 & t10) | (t01 & t10)) << 1;
    wire [47:0] sum2 = sum1 ^ carry1 ^ t11;
    wire [47:0] carry2 = ((sum1 & carry1) | (sum1 & t11) | (carry1 & t11)) << 1;
    wire [47:0] upper = sum2 + carry2;
    wire [63:0] product = {upper,p00[15:0]};
    reg [31:0] next_result;
    always @(*) begin
        case(op_ex_i)
            2'd0: next_result=product[31:0];
            2'd1,2'd2,2'd3: next_result=product[63:32];
            default: next_result=32'b0;
        endcase
    end
    assign result_o=next_result;
endmodule

module rv32_mul_capture_apm(
    input wire clk,rst_n,capture_i,
    input wire [16:0] a_i,b_i,
    output wire [33:0] product_o
);
    wire [47:0] p;
    GTP_APM_E2 #(
        .GRS_EN("FALSE"),.USE_POSTADD(0),.USE_PREADD(0),.USE_MULT(1),
        .X_REG(1),.Y_REG(1),.MULT_REG(0),.P_REG(0),.ASYNC_RST(1),
        .X_SEL(0),.USE_SIMD(0),.MODEIN_REG(0)
    ) u_apm (
        .P(p),.CPO(),.COUT(),.CXO(),.CXBO(),
        .X({{13{a_i[16]}},a_i}),.Y({b_i[16],b_i}),
        .CXI(30'b0),.CXBI(25'b0),.XB(25'b0),.Z(48'b0),.CPI(48'b0),.CIN(1'b0),
        .MODEY(3'b000),.MODEZ(4'b0000),.MODEIN(5'b00010),.CLK(clk),
        .CEX1(capture_i),.CEY1(capture_i),
        .CEX2(1'b0),.CEX3(1'b0),.CEXB(1'b0),.CEY2(1'b0),.CEZ(1'b0),
        .CEPRE(1'b0),.CEM(1'b0),.CEP(1'b0),.CEMODEY(1'b0),.CEMODEZ(1'b0),.CEMODEIN(1'b0),
        .RSTX(!rst_n),.RSTY(!rst_n),.RSTXB(1'b0),.RSTZ(1'b0),.RSTPRE(1'b0),
        .RSTM(1'b0),.RSTP(1'b0),.RSTMODEY(1'b0),.RSTMODEZ(1'b0),.RSTMODEIN(1'b0)
    );
    assign product_o=p[33:0];
endmodule
`default_nettype wire
