// 32 位小端访存对齐/字节通道模块。
// addr_i 是字节地址，mem_rdata_i 是 addr_i 所在的对齐 32 位字；
// lsu_type_i 沿用指令 funct3：Load 为 LB/LH/LW/LBU/LHU，Store 为 SB/SH/SW。
module rv32_lsu(
    input  [31:0] addr_i,
    input  [31:0] store_data_i,
    input  [31:0] mem_rdata_i,
    input  [2:0]  lsu_type_i,
    input         load_i,
    input         store_i,

    output [31:0] mem_addr_o,
    output [31:0] mem_wdata_o,
    output [3:0]  mem_wstrb_o,
    output        mem_read_o,
    output        mem_write_o,
    output [31:0] load_data_o,
    output        misaligned_o
);


// Store 类型与 funct3 对应。
parameter SB = 3'b000;
parameter SH = 3'b001;
parameter SW = 3'b010;




reg  [31:0] mem_addr ;
reg  [31:0] mem_wdata;
reg  [3:0]  mem_wstrb;
reg         mem_read ;
reg         mem_write;
reg  [31:0] load_data;
reg         misaligned;
                       
assign mem_addr_o  = mem_addr  ;
assign mem_wdata_o = mem_wdata ;
assign mem_wstrb_o = mem_wstrb ;
assign mem_read_o  = mem_read  ;
assign mem_write_o = mem_write ;
// 提前译码字节通道控制，并行选择晚到的 RAM 数据。
// 下方过程逻辑继续定义请求、对齐和 Store 输出；未消费的旧 Load 数据选择逻辑可由综合裁剪。
wire [31:0] lane_load_data;
rv32_load_lane_select u_load_lanes(
    .offset_i(addr_i[1:0]), .type_i(lsu_type_i),
    .load_i(load_i), .store_i(store_i), .data_i(mem_rdata_i),
    .data_o(lane_load_data));
assign load_data_o = lane_load_data;
assign misaligned_o= misaligned;


always@(*)begin
    // 外部存储器按 32 位字寻址；低两位只用于字节/半字选择和对齐检查。
    // 默认不访问存储器，也不报告异常，确保非法/空操作无写入副作用。
    mem_addr   = {addr_i[31:2],2'b00};
    mem_wdata  = 32'b0;
    mem_wstrb  = 4'b0000;  
    mem_read   = 1'b0;
    mem_write   = 1'b0;
    misaligned = 1'b0;
    load_data  = 32'b0;
    
    // Load 与 Store 互斥；若同时置位，本模块保持默认的“不访问”输出。
    if(load_i && !store_i)begin
        case(lsu_type_i)
        // LB：从四个字节通道中选一个，并把 bit7 符号扩展到 32 位。
        3'b000:begin
            mem_read = 1'b1;
            misaligned = 1'b0;// 字节访问无需对齐
            case(addr_i[1:0])
                2'b00:load_data = {{24{mem_rdata_i[7] }},mem_rdata_i[7:0]  };
                2'b01:load_data = {{24{mem_rdata_i[15]}},mem_rdata_i[15:8] };
                2'b10:load_data = {{24{mem_rdata_i[23]}},mem_rdata_i[23:16]};
                2'b11:load_data = {{24{mem_rdata_i[31]}},mem_rdata_i[31:24]};
                default:;
            endcase
        end
        // LH：只允许字内偏移 0 或 2；奇地址不发读请求并报告未对齐。
        3'b001:begin
            case(addr_i[1:0])
                2'b00:begin
                    mem_read = 1'b1;
                    misaligned = 1'b0;
                    load_data = {{16{mem_rdata_i[15] }},mem_rdata_i[15:0]  };               
                end
                2'b01:begin
                    mem_read = 1'b0;
                    misaligned = 1'b1;
                    load_data = 32'b0;
                end
                2'b10:begin
                    mem_read = 1'b1;
                    misaligned = 1'b0;
                    load_data = {{16{mem_rdata_i[31] }},mem_rdata_i[31:16]  };
                end
                2'b11:begin
                    mem_read = 1'b0;
                    misaligned = 1'b1;
                    load_data = 32'b0;
                end
                default:;
            endcase
        end
        // LW：地址低两位必须为 00，否则禁止读并报告未对齐。
        3'b010:begin
            if(addr_i[1:0] == 2'b00)begin
                mem_read = 1'b1;
                misaligned = 1'b0;
                load_data = mem_rdata_i;
            end
            else begin
                mem_read = 1'b0;
                misaligned = 1'b1;
                load_data = 32'b0;
            end
        end
        // LBU：与 LB 选同一字节，但高 24 位补零。
        3'b100:begin
            mem_read = 1'b1;
            misaligned = 1'b0;// 字节访问无需对齐
            case(addr_i[1:0])
                2'b00:load_data = {24'b0,mem_rdata_i[7:0]  };
                2'b01:load_data = {24'b0,mem_rdata_i[15:8] };
                2'b10:load_data = {24'b0,mem_rdata_i[23:16]};
                2'b11:load_data = {24'b0,mem_rdata_i[31:24]};
                default:;
            endcase
        end
        // LHU：对齐规则与 LH 相同，高 16 位补零。
        3'b101:begin
            case(addr_i[1:0])
                2'b00:begin
                    mem_read = 1'b1;
                    misaligned = 1'b0;
                    load_data = {16'b0,mem_rdata_i[15:0]  };               
                end
                2'b01:begin
                    mem_read = 1'b0;
                    misaligned = 1'b1;
                    load_data = 32'b0;
                end
                2'b10:begin
                    mem_read = 1'b1;
                    misaligned = 1'b0;
                    load_data = {16'b0,mem_rdata_i[31:16]  };
                end
                2'b11:begin
                    mem_read = 1'b0;
                    misaligned = 1'b1;
                    load_data = 32'b0;
                end
                default:begin
                    mem_read = 1'b0;
                    misaligned = 1'b0;
                    load_data = 32'b0;
                end
            endcase
        end
        default:;
    endcase
    end
    else if(store_i && !load_i)begin
        case(lsu_type_i)
        // SB：低 8 位移入地址选中的字节通道，wstrb 只使能该字节。
        SB:begin
            mem_write = 1'b1;
            misaligned = 1'b0;// 字节访问无需对齐
            case(addr_i[1:0])
                2'b00:begin
                    mem_wstrb = 4'b0001;
                    mem_wdata = {24'b0,store_data_i[7:0]  };
                end
                2'b01:begin
                    mem_wstrb = 4'b0010;
                    mem_wdata = {16'b0,store_data_i[7:0],8'b0 };
                end
                2'b10:begin
                    mem_wstrb = 4'b0100;
                    mem_wdata = {8'b0,store_data_i[7:0],16'b0};
                end
                2'b11:begin
                    mem_wstrb = 4'b1000;
                    mem_wdata = {store_data_i[7:0],24'b0};
                end
                default:;
            endcase
        end
        // SH：仅偏移 0/2 合法，低 16 位写入相邻两个字节通道。
        SH:begin   
            case(addr_i[1:0])
                2'b00:begin
                    mem_wstrb = 4'b0011;
                    mem_write = 1'b1;
                    misaligned = 1'b0;
                    mem_wdata = {16'b0,store_data_i[15:0]  };               
                end
                2'b01:begin
                    mem_write = 1'b0;
                    misaligned = 1'b1;
                    mem_wdata = 32'b0;
                end
                2'b10:begin
                    mem_wstrb = 4'b1100;
                    mem_write = 1'b1;
                    misaligned = 1'b0;
                    mem_wdata = {store_data_i[15:0] ,16'b0 };
                end
                2'b11:begin
                    mem_write = 1'b0;
                    misaligned = 1'b1;
                    mem_wdata = 32'b0;
                end
                default:;
            endcase
        end
        // SW：仅字对齐地址合法，四个字节通道全部使能。
        SW:begin
            if(addr_i[1:0] == 2'b00)begin
                mem_wstrb = 4'b1111;
                mem_write = 1'b1;
                misaligned = 1'b0;
                mem_wdata = store_data_i;
            end
            else begin
                mem_write = 1'b0;
                misaligned = 1'b1;
                mem_wdata = 32'b0;
            end
        end
        default:;
    endcase
    
    end
    
    


end











endmodule

module rv32_load_lane_select(
    input [1:0] offset_i,
    input [2:0] type_i,
    input load_i, store_i,
    input [31:0] data_i,
    output [31:0] data_o
);
    reg [3:0] byte_lane, signed_byte;
    reg [1:0] half_lane, signed_half;
    reg word_lane;
    // case/default 与 if 保持 LSU 对未知值和无效控制的处理，不凭空产生有效通道。
    always @* begin
        byte_lane=0; signed_byte=0;
        half_lane=0; signed_half=0; word_lane=0;
        if(load_i && !store_i) begin
            case(type_i)
                3'b000,3'b100: begin
                    case(offset_i)
                        2'b00:byte_lane=4'b0001;
                        2'b01:byte_lane=4'b0010;
                        2'b10:byte_lane=4'b0100;
                        2'b11:byte_lane=4'b1000;
                        default:byte_lane=0;
                    endcase
                    if(type_i==3'b000) signed_byte=byte_lane;
                end
                3'b001,3'b101: begin
                    case(offset_i)
                        2'b00:half_lane=2'b01;
                        2'b10:half_lane=2'b10;
                        default:half_lane=0;
                    endcase
                    if(type_i==3'b001) signed_half=half_lane;
                end
                3'b010:if(offset_i==2'b00) word_lane=1;
                default:begin end
            endcase
        end
    end
    wire sign_byte=|(signed_byte & {data_i[31],data_i[23],data_i[15],data_i[7]});
    wire sign_half=|(signed_half & {data_i[31],data_i[15]});
    assign data_o[7:0]=({8{byte_lane[0]|half_lane[0]|word_lane}} & data_i[7:0]) |
                      ({8{byte_lane[1]}} & data_i[15:8]) |
                      ({8{byte_lane[2]|half_lane[1]}} & data_i[23:16]) |
                      ({8{byte_lane[3]}} & data_i[31:24]);
    assign data_o[15:8]= {8{sign_byte}} |
                      ({8{half_lane[0]|word_lane}} & data_i[15:8]) |
                      ({8{half_lane[1]}} & data_i[31:24]);
    assign data_o[31:16]= {16{sign_byte|sign_half}} |
                       ({16{word_lane}} & data_i[31:16]);
endmodule



