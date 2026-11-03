`default_nettype none
module meeting_video_subsystem(
    input wire cpu_clk,reset_n,stream_enable,input wire [63:0] source_frame,
    output wire [511:0] status,
    input wire raw_enable,raw_pcm_valid,input wire signed [23:0] raw_pcm_data,input wire [6:0] raw_pcm_channel,input wire [3:0] raw_source_status,
    input wire clk_p,clk_n,cam_pclk,cam_vsync,cam_href,input wire [7:0] cam_data,
    input wire PHY0_RXC,PHY0_RXCTRL,input wire [3:0] PHY0_RXD,
    output wire PHY0_TXC,PHY0_TXCTRL,output wire [3:0] PHY0_TXD,
    output wire PHY0_MDC,inout wire PHY0_MDIO,output wire RSTN_OUT,
    output wire mem_rst_n,mem_ck,mem_ck_n,mem_cke,mem_cs_n,mem_ras_n,mem_cas_n,mem_we_n,mem_odt,
    output wire [14:0] mem_a,output wire [2:0] mem_ba,
    inout wire [3:0] mem_dqs,mem_dqs_n,inout wire [31:0] mem_dq,output wire [3:0] mem_dm
);
    wire ref125,ddr_ref_clk,eth_clk,eth_lock;
    GTP_INBUFDS #(.IOSTANDARD("SSTL15D_I"),.TERM_DIFF("ON")) ref_input(.I(clk_p),.IB(clk_n),.O(ref125));
    GTP_CLKBUFG ref_global(.CLKIN(ref125),.CLKOUT(ddr_ref_clk));
    meeting_video_clocks clocks(.ref125(ref125),.reset_n(reset_n),.eth_clk(eth_clk),.locked(eth_lock));
    wire video_reset_n=reset_n && eth_lock;
    wire eth_rst_n,mem_rst,cam_rst,cpu_video_rst,ddr_init_done,core_clk,pll_lock,phy_pll_lock,gpll_lock,rst_gpll_lock,ddrphy_cpd_lock;
    reset_release re(.clk(eth_clk),.arst_n(video_reset_n),.rst_n(eth_rst_n));
    reset_release rm(.clk(core_clk),.arst_n(video_reset_n && ddr_init_done),.rst_n(mem_rst));
    reset_release rc(.clk(cam_pclk),.arst_n(video_reset_n && ddr_init_done),.rst_n(cam_rst));
    reset_release rcv(.clk(cpu_clk),.arst_n(video_reset_n && ddr_init_done),.rst_n(cpu_video_rst));
    wire eth_cpu_rst;
    reset_release recpu(.clk(cpu_clk),.arst_n(video_reset_n),.rst_n(eth_cpu_rst));
    wire [27:0] axi_awaddr,axi_araddr;
    wire [3:0] axi_awid=0,axi_arid=0,axi_awlen=0,axi_arlen=0;wire [3:0] axi_rid;
    wire axi_awvalid,axi_awready,axi_wready,axi_arvalid,axi_arready,axi_rlast,axi_rvalid;
    wire [255:0] axi_wdata,axi_rdata;wire [31:0] axi_wstrb;
    `include "meeting_ddr_instance.vh"
    wire mdio_out,mdio_oe,phy_ready,link_up,phy_fault,peer_ready;
    wire [4:0] phy_address;
    wire [287:0] phy_debug;
    assign PHY0_MDIO=mdio_oe ? mdio_out : 1'bz;
    rtl8211f_manager phy(.clk(eth_clk),.rst_n(eth_rst_n),.phy_rst_n(RSTN_OUT),
        .mdc(PHY0_MDC),.mdio_out(mdio_out),.mdio_oe(mdio_oe),.mdio_in(PHY0_MDIO),
        .ready(phy_ready),.link_up(link_up),.fault(phy_fault),.phy_address(phy_address),.debug_status(phy_debug));
    wire rx_clk,rx_dv,rx_er,tx_en,tx_er;wire [7:0] rxd,txd;
    pango_rgmii_io #(.TX_ASYNC_RESET(0)) rgmii(.tx_clk(eth_clk),.rst_n(eth_rst_n),.tx_data(txd),.tx_en(tx_en),.tx_er(tx_er),
        .rgmii_rxc(PHY0_RXC),.rgmii_rx_ctl(PHY0_RXCTRL),.rgmii_rxd(PHY0_RXD),.rx_clk(rx_clk),.rx_data(rxd),.rx_dv(rx_dv),.rx_er(rx_er),
        .rgmii_txc(PHY0_TXC),.rgmii_tx_ctl(PHY0_TXCTRL),.rgmii_txd(PHY0_TXD));
    wire [7:0] payload;wire pv,pr,pl,packet_done,tx_error;wire [15:0] plen;
    wire [255:0] net_debug;wire [127:0] rx_debug;
    wire [7:0] vp,ap;wire vv,vr,vl,av,ar,al;wire [15:0] vlen,alen;
    wire raw_framing_error,raw_overflow;wire [31:0] raw_drops,raw_epoch,a_packets,v_packets;
    meeting_raw_pcm_transport raw_transport(.src_clk(cpu_clk),.eth_clk(eth_clk),.reset_n(reset_n),
        .stream_enable(raw_enable),.network_ready(peer_ready && link_up),
        .pcm_valid(raw_pcm_valid),.pcm_data(raw_pcm_data),.pcm_channel(raw_pcm_channel),.pcm_frame(source_frame),.source_status(raw_source_status),
        .payload_data(ap),.payload_valid(av),.payload_ready(ar),.payload_last(al),.payload_length(alen),
        .framing_error(raw_framing_error),.overflow_sticky(raw_overflow),.dropped_samples(raw_drops),.stream_epoch(raw_epoch));
    meeting_packet_arbiter share_mac(.clk(eth_clk),.rst_n(eth_rst_n && peer_ready),
        .a_data(ap),.a_valid(av),.a_ready(ar),.a_last(al),.a_length(alen),
        .b_data(vp),.b_valid(vv),.b_ready(vr),.b_last(vl),.b_length(vlen),
        .out_data(payload),.out_valid(pv),.out_ready(pr),.out_last(pl),.out_length(plen),
        .a_packets(a_packets),.b_packets(v_packets));
    ethernet_udp_engine #(.LOCAL_PORT(5001),.PEER_PORT(5001)) network(
        .clk(eth_clk),.rst_n(eth_rst_n),.link_up(link_up),.stream_enable(1'b1),.peer_ready(peer_ready),
        .tx_payload_data(payload),.tx_payload_valid(pv),.tx_payload_ready(pr),.tx_payload_last(pl),.tx_payload_length(plen),
        .tx_packet_done(packet_done),.tx_error(tx_error),.rx_clk(rx_clk),.gmii_rxd(rxd),.gmii_rx_dv(rx_dv),.gmii_rx_er(rx_er),
        .gmii_txd(txd),.gmii_tx_en(tx_en),.gmii_tx_er(tx_er),.debug_status(net_debug),.debug_rx(rx_debug));
    wire [64:0] camera_bundle;
    // Common resets for mailbox are system reset, not late DDR calibration.
    wire cam_system_rst;
    reset_release rcs(.clk(cam_pclk),.arst_n(reset_n),.rst_n(cam_system_rst));
    meeting_cdc_snapshot #(.WIDTH(65)) source_time(.src_clk(cpu_clk),.src_rst_n(reset_n),.src_data({stream_enable,source_frame}),
        .dst_clk(cam_pclk),.dst_rst_n(cam_system_rst),.dst_data(camera_bundle));
    wire [257:0] cap_data,write_data,read_data,send_data;
    wire cap_valid,cap_ready,write_valid,write_ready,read_valid,read_ready,send_valid,send_ready;
    wire [31:0] sensor_frames,bad_frames,overflow_frames,completed,rejected,superseded,read_frames,sent_frames,sent_packets,aborted;
    wire mem_fault;
    meeting_video_capture capture(.pclk(cam_pclk),.rst_n(cam_rst),.enable(camera_bundle[64]),.vsync(cam_vsync),.href(cam_href),.data(cam_data),
        .source_frame(camera_bundle[63:0]),.out_data(cap_data),.out_valid(cap_valid),.out_ready(cap_ready),
        .sensor_frames(sensor_frames),.bad_frames(bad_frames),.overflow_frames(overflow_frames));
    async_sample_fifo #(.WIDTH(258),.ADDRESS_BITS(10)) capture_fifo(
        .wr_clk(cam_pclk),.wr_rst_n(cam_rst),.wr_data(cap_data),.wr_valid(cap_valid),.wr_ready(cap_ready),
        .rd_clk(core_clk),.rd_rst_n(mem_rst),.rd_data(write_data),.rd_valid(write_valid),.rd_ready(write_ready));
    (* async_reg="true" *) reg [1:0] peer_mem;
    always @(posedge core_clk or negedge mem_rst)
        if(!mem_rst)peer_mem<=0;else peer_mem<={peer_mem[0],peer_ready};
    meeting_video_frame_store store(.clk(core_clk),.rst_n(mem_rst),.send_enable(peer_mem[1]),
        .in_data(write_data),.in_valid(write_valid),.in_ready(write_ready),.out_data(read_data),.out_valid(read_valid),.out_ready(read_ready),
        .awaddr(axi_awaddr),.awvalid(axi_awvalid),.awready(axi_awready),.wdata(axi_wdata),.wstrb(axi_wstrb),.wready(axi_wready),
        .araddr(axi_araddr),.arvalid(axi_arvalid),.arready(axi_arready),.rdata(axi_rdata),.rvalid(axi_rvalid),.rlast(axi_rlast),
        .completed(completed),.rejected(rejected),.superseded(superseded),.read_frames(read_frames),.protocol_fault(mem_fault));
    wire stream_eth_rst;
    reset_release res(.clk(eth_clk),.arst_n(video_reset_n && ddr_init_done),.rst_n(stream_eth_rst));
    async_sample_fifo #(.WIDTH(258),.ADDRESS_BITS(8)) network_fifo(
        .wr_clk(core_clk),.wr_rst_n(mem_rst),.wr_data(read_data),.wr_valid(read_valid),.wr_ready(read_ready),
        .rd_clk(eth_clk),.rd_rst_n(stream_eth_rst),.rd_data(send_data),.rd_valid(send_valid),.rd_ready(send_ready));
    meeting_video_packetizer packetizer(.clk(eth_clk),.rst_n(stream_eth_rst),.enable(peer_ready),
        .in_data(send_data),.in_valid(send_valid),.in_ready(send_ready),.payload_data(vp),.payload_valid(vv),.payload_ready(vr),
        .payload_last(vl),.payload_length(vlen),.frames(sent_frames),.packets(sent_packets),.aborted(aborted));
    wire [95:0] cs;
    wire [159:0] ms;
    wire [255:0] es;
    meeting_cdc_snapshot #(.WIDTH(96)) cam_status(.src_clk(cam_pclk),.src_rst_n(cam_rst),.src_data({overflow_frames,bad_frames,sensor_frames}),
        .dst_clk(cpu_clk),.dst_rst_n(cpu_video_rst),.dst_data(cs));
    meeting_cdc_snapshot #(.WIDTH(160)) mem_status(.src_clk(core_clk),.src_rst_n(mem_rst),.src_data({31'b0,mem_fault,read_frames,superseded,rejected,completed}),
        .dst_clk(cpu_clk),.dst_rst_n(cpu_video_rst),.dst_data(ms));
    meeting_cdc_snapshot #(.WIDTH(256)) eth_status(.src_clk(eth_clk),.src_rst_n(eth_rst_n),
        .src_data({phy_debug[255:224],phy_debug[63:32],phy_debug[31:0],net_debug[159:128],aborted,sent_packets,sent_frames,
            24'b0,tx_error,phy_fault,peer_ready,link_up,phy_ready,ddr_init_done,gpll_lock,pll_lock}),
        .dst_clk(cpu_clk),.dst_rst_n(eth_cpu_rst),.dst_data(es));
    // Word0 completed;1 rejected;2 superseded;3 read;4 memfault;
    // 5 sensor;6 malformed;7 overflow;8 flags;9 sent;10 packets;11 aborted;
    // 12 UDP packets completed;13 PHY state;14 PHY ID;15 PHY failure.
    assign status={es,cs,ms};
endmodule
`default_nettype wire
