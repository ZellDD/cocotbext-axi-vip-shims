// Example top: VIP master -> (your DUT goes here) -> VIP slave, plus DPI shims.
// VIP config assumed (Vivado IP customisation), both VIPs:
//   TDATA 4 bytes, HAS_TKEEP=1, HAS_TSTRB=1, HAS_TLAST=1,
//   TID/TDEST/TUSER width 4 bits
// The shim parameters below must match this AND the cocotb constructors; a
// mismatch with cocotb is reported on the first call.
`timescale 1ns/1ps

module tb_top;
  localparam int DATA_BYTES = 4;
  localparam int ID_W = 4, DEST_W = 4, USER_W = 4;

  logic aclk    = 1'b0;
  logic aresetn = 1'b0;          // cocotb releases reset
  always #5 aclk = ~aclk;

  logic                    tvalid, tready, tlast;
  logic [DATA_BYTES*8-1:0] tdata;
  logic [DATA_BYTES-1:0]   tkeep;
  logic [DATA_BYTES-1:0]   tstrb;
  logic [ID_W-1:0]         tid;
  logic [DEST_W-1:0]       tdest;
  logic [USER_W-1:0]       tuser;

  axi4stream_vip_mst mst_vip (
    .aclk(aclk), .aresetn(aresetn),
    .m_axis_tvalid(tvalid), .m_axis_tready(tready),
    .m_axis_tdata(tdata),   .m_axis_tkeep(tkeep),  .m_axis_tstrb(tstrb),
    .m_axis_tlast(tlast),
    .m_axis_tid(tid),       .m_axis_tdest(tdest),  .m_axis_tuser(tuser)
  );

  // Loopback for now. To test a DUT, use separate wires:
  //   mst_vip.m_axis_* -> dut.s_axis_*   and   dut.m_axis_* -> slv_vip.s_axis_*
  axi4stream_vip_slv slv_vip (
    .aclk(aclk), .aresetn(aresetn),
    .s_axis_tvalid(tvalid), .s_axis_tready(tready),
    .s_axis_tdata(tdata),   .s_axis_tkeep(tkeep),  .s_axis_tstrb(tstrb),
    .s_axis_tlast(tlast),
    .s_axis_tid(tid),       .s_axis_tdest(tdest),  .s_axis_tuser(tuser)
  );

  axis_vip_dpi_mst #(.NAME("mst0"), .DATA_BYTES(DATA_BYTES),
                     .HAS_TKEEP(1), .HAS_TSTRB(1), .HAS_TLAST(1),
                     .ID_W(ID_W), .DEST_W(DEST_W), .USER_W(USER_W))
                     mst_shim (.aclk(aclk));
  axis_vip_dpi_slv #(.NAME("slv0"), .DATA_BYTES(DATA_BYTES),
                     .HAS_TKEEP(1), .HAS_TSTRB(1), .HAS_TLAST(1),
                     .ID_W(ID_W), .DEST_W(DEST_W), .USER_W(USER_W))
                     slv_shim ();
endmodule
