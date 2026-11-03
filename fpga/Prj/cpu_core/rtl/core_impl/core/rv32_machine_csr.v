`default_nettype none
// 单 hart、仅机器态的 CSR 寄存器组；核心串行处理所有 CSR 请求。
// clk 上升沿更新，rst_n 异步低有效；同拍优先级为陷阱高于 MRET，高于普通 CSR 写入。
module rv32_machine_csr (
    input wire clk, rst_n,
    input wire [11:0] addr_i,
    input wire [1:0] cmd_i,
    input wire [31:0] operand_i,
    input wire source_nonzero_i, access_i,
    output reg [31:0] rdata_o,
    output wire illegal_o,
    input wire [2:0] retire_count_i,
    input wire trap_i, mret_i,
    input wire [31:0] trap_pc_i, trap_cause_i, trap_tval_i,
    input wire irq_external_i, irq_software_i, irq_timer_i,
    output wire irq_pending_o,
    output wire [31:0] irq_cause_o, trap_vector_o, epc_o
);
    reg mie_global, mpie;
    reg [31:0] mie, mtvec, mscratch, mepc, mcause, mtval;
    reg [63:0] mcycle, minstret;
    reg [2:0] inhibit;
    wire [31:0] mip = {20'b0,irq_external_i,3'b0,irq_timer_i,3'b0,irq_software_i,3'b0};
    wire [31:0] enabled = mie & mip;
    assign irq_pending_o = mie_global && |enabled;
    assign irq_cause_o = enabled[11] ? 32'h8000000b : enabled[3] ? 32'h80000003 : 32'h80000007;
    assign trap_vector_o = mtvec;
    assign epc_o = mepc;
    reg exists;
    wire write_request = cmd_i==2'd1 || source_nonzero_i;
    // 按地址高低字段精确判定合法 CSR，不改变读值和写状态语义。
    reg legal_write_address, legal_readonly_address;
    always @* begin
        legal_write_address=1'b0;
        legal_readonly_address=1'b0;
        case(addr_i[11:8])
            4'h3:case(addr_i[7:0])
                8'h00,8'h01,8'h04,8'h05,8'h06,8'h10,8'h20,
                8'h40,8'h41,8'h42,8'h43,8'h44:legal_write_address=1'b1;
                default:begin end
            endcase
            4'hb:case(addr_i[7:0])
                8'h00,8'h80,8'h02,8'h82:legal_write_address=1'b1;
                default:begin end
            endcase
            4'hc:case(addr_i[7:0])
                8'h00,8'h80,8'h02,8'h82:legal_readonly_address=1'b1;
                default:begin end
            endcase
            4'hf:case(addr_i[7:0])
                8'h11,8'h12,8'h13,8'h14:legal_readonly_address=1'b1;
                default:begin end
            endcase
            default:begin end
        endcase
    end
    assign illegal_o = !(legal_write_address || (legal_readonly_address && !write_request));
    wire [31:0] new_value = cmd_i==1 ? operand_i : cmd_i==2 ? rdata_o|operand_i : rdata_o&~operand_i;
    always @* begin
        exists=1'b1;
        case(addr_i)
            12'h300:rdata_o=32'h1800 | (mie_global ? 32'h8:0) | (mpie ? 32'h80:0);
            12'h301:rdata_o=32'h40000100; // 只报告 I 扩展：Zmmul 并不等于完整 M 扩展。
            12'h304:rdata_o=mie;
            12'h305:rdata_o=mtvec;
            12'h306,12'h310:rdata_o=0;
            12'h320:rdata_o={29'b0,inhibit};
            12'h340:rdata_o=mscratch;
            12'h341:rdata_o=mepc;
            12'h342:rdata_o=mcause;
            12'h343:rdata_o=mtval;
            12'h344:rdata_o=mip; // 平台中断源字段只读。
            12'hb00,12'hc00:rdata_o=mcycle[31:0];
            12'hb80,12'hc80:rdata_o=mcycle[63:32];
            12'hb02,12'hc02:rdata_o=minstret[31:0];
            12'hb82,12'hc82:rdata_o=minstret[63:32];
            12'hf11,12'hf12,12'hf13,12'hf14:rdata_o=0;
            default:begin exists=0;rdata_o=0;end
        endcase
    end
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            mie_global<=0;mpie<=0;mie<=0;mtvec<=0;mscratch<=0;
            mepc<=0;mcause<=0;mtval<=0;mcycle<=0;minstret<=0;inhibit<=0;
        end else begin
            if(!inhibit[0]) mcycle<=mcycle+64'd1;
            if(!inhibit[2]) minstret<=minstret+{61'b0,retire_count_i};
            if(access_i && !illegal_o && write_request) begin
                case(addr_i)
                    12'h300:begin mie_global<=new_value[3];mpie<=new_value[7];end
                    12'h304:mie<=new_value & 32'h888;
                    12'h305:mtvec<={new_value[31:2],2'b0};
                    12'h320:inhibit<=new_value[2:0]&3'b101;
                    12'h340:mscratch<=new_value;
                    12'h341:mepc<={new_value[31:2],2'b0};
                    12'h342:mcause<=new_value;
                    12'h343:mtval<=new_value;
                    // 显式写计数器覆盖该拍整个隐式自增结果。
                    12'hb00:mcycle<={mcycle[63:32],new_value};
                    12'hb80:mcycle<={new_value,mcycle[31:0]};
                    12'hb02:minstret<={minstret[63:32],new_value};
                    12'hb82:minstret<={new_value,minstret[31:0]};
                    default:begin end // 可写 CSR 地址中的 WARL 字段按规定限制写入，其他字段保持只读。
                endcase
            end
            if(mret_i) begin mie_global<=mpie;mpie<=1;end
            if(trap_i) begin
                mepc<={trap_pc_i[31:2],2'b0};mcause<=trap_cause_i;mtval<=trap_tval_i;
                mpie<=mie_global;mie_global<=0;
            end
        end
    end
endmodule
`default_nettype wire
