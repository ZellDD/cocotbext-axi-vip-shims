// axis_byteswap -- example DUT: reverses the byte order of every beat.
//
// TDATA and TKEEP are mirrored byte by byte; TLAST and TUSER pass through.
// One register stage, full throughput, TREADY back-propagates combinationally.
`timescale 1ns/1ps

module axis_byteswap #(
  parameter int DATA_BYTES = 4,
  parameter int USER_W     = 1
) (
  input  logic                    aclk,
  input  logic                    aresetn,

  input  logic                    s_axis_tvalid,
  output logic                    s_axis_tready,
  input  logic [DATA_BYTES*8-1:0] s_axis_tdata,
  input  logic [DATA_BYTES-1:0]   s_axis_tkeep,
  input  logic                    s_axis_tlast,
  input  logic [USER_W-1:0]       s_axis_tuser,

  output logic                    m_axis_tvalid,
  input  logic                    m_axis_tready,
  output logic [DATA_BYTES*8-1:0] m_axis_tdata,
  output logic [DATA_BYTES-1:0]   m_axis_tkeep,
  output logic                    m_axis_tlast,
  output logic [USER_W-1:0]       m_axis_tuser
);
  assign s_axis_tready = !m_axis_tvalid || m_axis_tready;

  always_ff @(posedge aclk) begin
    if (!aresetn) begin
      m_axis_tvalid <= 1'b0;
    end else if (s_axis_tready) begin
      m_axis_tvalid <= s_axis_tvalid;
      for (int i = 0; i < DATA_BYTES; i++) begin
        m_axis_tdata[8*i +: 8] <= s_axis_tdata[8*(DATA_BYTES-1-i) +: 8];
        m_axis_tkeep[i]        <= s_axis_tkeep[DATA_BYTES-1-i];
      end
      m_axis_tlast <= s_axis_tlast;
      m_axis_tuser <= s_axis_tuser;
    end
  end
endmodule
