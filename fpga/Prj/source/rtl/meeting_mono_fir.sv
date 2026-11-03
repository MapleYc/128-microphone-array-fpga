`default_nettype none
// Exact Q15 Kaiser127 /3 decimator, same rounding/phase as the C golden.
// No reset on memory: valid_history masks unwritten locations. One MAC, two
// clocks/tap, <=255 clocks/output versus 3937 clocks/16k sample at 63 MHz.
// Output timestamp is the newest 48k input, not the FIR center (minus 63).
module meeting_mono_fir(
    input wire clk,rst_n,flush_i,in_valid_i,
    input wire signed [15:0] in_pcm_i,
    input wire [63:0] in_frame_i,
    input wire [31:0] in_epoch_i,
    output wire ready_o,
    output reg out_valid_o,
    output reg signed [15:0] out_pcm_o,
    output reg [63:0] out_frame_o,
    output reg [31:0] out_epoch_o,
    output reg fault_o
);
    reg signed [15:0] history[0:127];
    reg [6:0] wr_ptr,newest,tap;
    reg [7:0] valid_history;
    reg [1:0] phase,state;
    reg have_frame;
    reg [63:0] last_frame;
    reg [31:0] last_epoch;
    reg signed [15:0] sample_q,coeff_q;
    reg signed [31:0] acc;
    wire discontinuity=have_frame && (in_epoch_i!=last_epoch || in_frame_i!=last_frame+64'd1);
    wire restart=discontinuity || state!=0;
    wire [6:0] read_addr=newest-tap;
    wire signed [31:0] product=sample_q*coeff_q;
    wire signed [32:0] total=$signed({acc[31],acc})+$signed({product[31],product});
    wire signed [33:0] magnitude=total<0 ? -$signed({total[32],total}) : $signed({total[32],total});
    wire signed [33:0] rounded=(magnitude+34'sd16384) >>> 15;
    wire signed [33:0] result_value=total<0 ? -rounded : rounded;
    assign ready_o=state==0 && !flush_i;
    function automatic signed [15:0] coeff(input [6:0] k);
        begin
            case(k)
                7'd0:coeff=16'sd0;
                7'd1:coeff=16'sd0;
                7'd2:coeff=16'sd0;
                7'd3:coeff=-16'sd1;
                7'd4:coeff=-16'sd1;
                7'd5:coeff=16'sd0;
                7'd6:coeff=16'sd2;
                7'd7:coeff=16'sd2;
                7'd8:coeff=16'sd0;
                7'd9:coeff=-16'sd3;
                7'd10:coeff=-16'sd5;
                7'd11:coeff=-16'sd3;
                7'd12:coeff=16'sd3;
                7'd13:coeff=16'sd9;
                7'd14:coeff=16'sd9;
                7'd15:coeff=16'sd0;
                7'd16:coeff=-16'sd12;
                7'd17:coeff=-16'sd18;
                7'd18:coeff=-16'sd8;
                7'd19:coeff=16'sd12;
                7'd20:coeff=16'sd28;
                7'd21:coeff=16'sd23;
                7'd22:coeff=-16'sd5;
                7'd23:coeff=-16'sd37;
                7'd24:coeff=-16'sd44;
                7'd25:coeff=-16'sd14;
                7'd26:coeff=16'sd37;
                7'd27:coeff=16'sd69;
                7'd28:coeff=16'sd47;
                7'd29:coeff=-16'sd22;
                7'd30:coeff=-16'sd89;
                7'd31:coeff=-16'sd93;
                7'd32:coeff=-16'sd16;
                7'd33:coeff=16'sd93;
                7'd34:coeff=16'sd144;
                7'd35:coeff=16'sd80;
                7'd36:coeff=-16'sd68;
                7'd37:coeff=-16'sd189;
                7'd38:coeff=-16'sd171;
                7'd39:coeff=16'sd0;
                7'd40:coeff=16'sd206;
                7'd41:coeff=16'sd276;
                7'd42:coeff=16'sd120;
                7'd43:coeff=-16'sd172;
                7'd44:coeff=-16'sd374;
                7'd45:coeff=-16'sd293;
                7'd46:coeff=16'sd59;
                7'd47:coeff=16'sd433;
                7'd48:coeff=16'sd509;
                7'd49:coeff=16'sd158;
                7'd50:coeff=-16'sd410;
                7'd51:coeff=-16'sd750;
                7'd52:coeff=-16'sd510;
                7'd53:coeff=16'sd244;
                7'd54:coeff=16'sd986;
                7'd55:coeff=16'sd1058;
                7'd56:coeff=16'sd185;
                7'd57:coeff=-16'sd1185;
                7'd58:coeff=-16'sd2016;
                7'd59:coeff=-16'sd1283;
                7'd60:coeff=16'sd1318;
                7'd61:coeff=16'sd5017;
                7'd62:coeff=16'sd8267;
                7'd63:coeff=16'sd9564;
                7'd64:coeff=16'sd8267;
                7'd65:coeff=16'sd5017;
                7'd66:coeff=16'sd1318;
                7'd67:coeff=-16'sd1283;
                7'd68:coeff=-16'sd2016;
                7'd69:coeff=-16'sd1185;
                7'd70:coeff=16'sd185;
                7'd71:coeff=16'sd1058;
                7'd72:coeff=16'sd986;
                7'd73:coeff=16'sd244;
                7'd74:coeff=-16'sd510;
                7'd75:coeff=-16'sd750;
                7'd76:coeff=-16'sd410;
                7'd77:coeff=16'sd158;
                7'd78:coeff=16'sd509;
                7'd79:coeff=16'sd433;
                7'd80:coeff=16'sd59;
                7'd81:coeff=-16'sd293;
                7'd82:coeff=-16'sd374;
                7'd83:coeff=-16'sd172;
                7'd84:coeff=16'sd120;
                7'd85:coeff=16'sd276;
                7'd86:coeff=16'sd206;
                7'd87:coeff=16'sd0;
                7'd88:coeff=-16'sd171;
                7'd89:coeff=-16'sd189;
                7'd90:coeff=-16'sd68;
                7'd91:coeff=16'sd80;
                7'd92:coeff=16'sd144;
                7'd93:coeff=16'sd93;
                7'd94:coeff=-16'sd16;
                7'd95:coeff=-16'sd93;
                7'd96:coeff=-16'sd89;
                7'd97:coeff=-16'sd22;
                7'd98:coeff=16'sd47;
                7'd99:coeff=16'sd69;
                7'd100:coeff=16'sd37;
                7'd101:coeff=-16'sd14;
                7'd102:coeff=-16'sd44;
                7'd103:coeff=-16'sd37;
                7'd104:coeff=-16'sd5;
                7'd105:coeff=16'sd23;
                7'd106:coeff=16'sd28;
                7'd107:coeff=16'sd12;
                7'd108:coeff=-16'sd8;
                7'd109:coeff=-16'sd18;
                7'd110:coeff=-16'sd12;
                7'd111:coeff=16'sd0;
                7'd112:coeff=16'sd9;
                7'd113:coeff=16'sd9;
                7'd114:coeff=16'sd3;
                7'd115:coeff=-16'sd3;
                7'd116:coeff=-16'sd5;
                7'd117:coeff=-16'sd3;
                7'd118:coeff=16'sd0;
                7'd119:coeff=16'sd2;
                7'd120:coeff=16'sd2;
                7'd121:coeff=16'sd0;
                7'd122:coeff=-16'sd1;
                7'd123:coeff=-16'sd1;
                7'd124:coeff=16'sd0;
                7'd125:coeff=16'sd0;
                7'd126:coeff=16'sd0;
                default:coeff=0;
            endcase
        end
    endfunction
    always @(posedge clk) begin
        if(in_valid_i && !flush_i) history[restart ? 7'd0 : wr_ptr]<=in_pcm_i;
        if(state==1) begin sample_q<=history[read_addr];coeff_q<=coeff(tap);end
    end
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            wr_ptr<=0;newest<=0;tap<=0;valid_history<=0;phase<=0;state<=0;
            have_frame<=0;last_frame<=0;last_epoch<=0;acc<=0;
            out_valid_o<=0;out_pcm_o<=0;out_frame_o<=0;out_epoch_o<=0;fault_o<=0;
        end else begin
            out_valid_o<=0;fault_o<=0;
            if(flush_i) begin
                wr_ptr<=0;valid_history<=0;phase<=0;state<=0;have_frame<=0;acc<=0;
            end else if(in_valid_i) begin
                have_frame<=1;last_frame<=in_frame_i;last_epoch<=in_epoch_i;
                if(restart) begin
                    wr_ptr<=1;valid_history<=1;phase<=1;state<=0;
                    if(state!=0) fault_o<=1;
                end else begin
                    wr_ptr<=wr_ptr+1'b1;
                    if(valid_history<127) valid_history<=valid_history+1'b1;
                    if(phase==2) begin
                        phase<=0;
                        // Discard 42 padded outputs, exactly like the C session.
                        if(valid_history>=126) begin
                            newest<=wr_ptr;tap<=0;acc<=0;state<=1;
                            out_frame_o<=in_frame_i;out_epoch_o<=in_epoch_i;
                        end
                    end else phase<=phase+1'b1;
                end
            end else if(state==1) state<=2;
            else if(state==2) begin
                acc<=total[31:0];
                if(tap==126) begin
                    out_pcm_o<=result_value>32767 ? 16'sh7fff : result_value< -32768 ? 16'sh8000 : result_value[15:0];
                    out_valid_o<=1;state<=0;
                end else begin tap<=tap+1'b1;state<=1;end
            end
        end
    end
endmodule
`default_nettype wire
