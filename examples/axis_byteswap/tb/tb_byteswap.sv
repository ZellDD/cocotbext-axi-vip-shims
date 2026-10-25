// tb_byteswap -- VIP master -> axis_byteswap -> VIP slave, plus the DPI shims.
//
// VIP cores (tb/gen_vip.tcl), both:
//   TDATA 4 bytes, HAS_TKEEP=1, HAS_TSTRB=0, HAS_TLAST=1, TUSER 1 bit
// The shim parameters below must match this AND IFACE in test_byteswap.py.
//
// The component and instance names differ from the shims' defaults, so the
// runner overrides the shim macros:
//   AXIS_MST_VIP_PKG=ex_src_vip_pkg    AXIS_MST_AGENT_T=ex_src_vip_mst_t
//   AXIS_MST_VIF=tb_byteswap.u_src.inst.IF
//   AXIS_SLV_VIP_PKG=ex_sink_vip_pkg   AXIS_SLV_AGENT_T=ex_sink_vip_slv_t
//   AXIS_SLV_VIF=tb_byteswap.u_sink.inst.IF
`timescale 1ns/1ps

module tb_byteswap;
  localparam int DATA_BYTES = 4;
  localparam int USER_W     = 1;

  logic aclk    = 1'b0;
  logic aresetn = 1'b0;          // cocotb releases reset
  always #5 aclk = ~aclk;

  // VIP master -> DUT
  logic                    in_tvalid, in_tready, in_tlast;
  logic [DATA_BYTES*8-1:0] in_tdata;
  logic [DATA_BYTES-1:0]   in_tkeep;
  logic [USER_W-1:0]       in_tuser;

  // DUT -> VIP slave
  logic                    out_tvalid, out_tready, out_tlast;
  logic [DATA_BYTES*8-1:0] out_tdata;
  logic [DATA_BYTES-1:0]   out_tkeep;
  logic [USER_W-1:0]       out_tuser;

  ex_src_vip u_src (
    .aclk(aclk), .aresetn(aresetn),
    .m_axis_tvalid(in_tvalid), .m_axis_tready(in_tready),
    .m_axis_tdata(in_tdata),   .m_axis_tkeep(in_tkeep),
    .m_axis_tlast(in_tlast),   .m_axis_tuser(in_tuser)
  );

  axis_byteswap #(.DATA_BYTES(DATA_BYTES), .USER_W(USER_W)) u_dut (
    .aclk(aclk), .aresetn(aresetn),
    .s_axis_tvalid(in_tvalid),  .s_axis_tready(in_tready),
    .s_axis_tdata(in_tdata),    .s_axis_tkeep(in_tkeep),
    .s_axis_tlast(in_tlast),    .s_axis_tuser(in_tuser),
    .m_axis_tvalid(out_tvalid), .m_axis_tready(out_tready),
    .m_axis_tdata(out_tdata),   .m_axis_tkeep(out_tkeep),
    .m_axis_tlast(out_tlast),   .m_axis_tuser(out_tuser)
  );

  ex_sink_vip u_sink (
    .aclk(aclk), .aresetn(aresetn),
    .s_axis_tvalid(out_tvalid), .s_axis_tready(out_tready),
    .s_axis_tdata(out_tdata),   .s_axis_tkeep(out_tkeep),
    .s_axis_tlast(out_tlast),   .s_axis_tuser(out_tuser)
  );

  // NAME is what cocotb passes to AXIS_MASTER_VIP / AXIS_SLAVE_VIP.
  axis_vip_dpi_mst #(.NAME("src"), .DATA_BYTES(DATA_BYTES),
                     .HAS_TKEEP(1), .HAS_TLAST(1), .USER_W(USER_W))
                     src_shim (.aclk(aclk));
  axis_vip_dpi_slv #(.NAME("sink"), .DATA_BYTES(DATA_BYTES),
                     .HAS_TKEEP(1), .HAS_TLAST(1), .USER_W(USER_W))
                     sink_shim ();
endmodule
