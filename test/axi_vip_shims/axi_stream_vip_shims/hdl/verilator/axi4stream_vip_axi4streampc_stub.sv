// Stand-in, for Verilator builds only, for the Xilinx AXI4-Stream protocol checker
// (axi4stream_vip_axi4streampc.sv), which axi4stream_vip_if instantiates as PC.
//
// The real checker can't be used with Verilator 5.052: its tri0/tri1 ports,
// on an interface nested in the parameterized axi4stream_vip_if and fed from
// tri-stated nets, crash Verilator (internal fault in V3LinkDot), and its SVA
// uses ##[m:n] sequences. Same parameters and ports, plain wires, no checks;
// only the two variables the VIP top writes (IF.PC.*) are kept.
interface axi4stream_vip_axi4streampc #(
  int DATA_WIDTH_BYTES   = 4,
      USER_BITS_PER_BYTE = 0,
      DEST_WIDTH         = 4,
      ID_WIDTH           = 4,
      USER_WIDTH         = 32,
      MAXWAITS           = 16000,
      RecommendOn        = 1,
      RecMaxWaitOn       = 1,
      HAS_ARESETN        = 1
) (
  input bit  ACLK,
  input bit  ACLKEN,
  input bit  ARESETn,
  input wire [(DATA_WIDTH_BYTES ? DATA_WIDTH_BYTES*8 - 1 : 0):0] TDATA,
  input wire [(DATA_WIDTH_BYTES ? DATA_WIDTH_BYTES - 1 : 0):0]   TSTRB,
  input wire [(DATA_WIDTH_BYTES ? DATA_WIDTH_BYTES - 1 : 0):0]   TKEEP,
  input wire                                                     TLAST,
  input wire [(ID_WIDTH ? ID_WIDTH - 1 : 0):0]                   TID,
  input wire [(DEST_WIDTH ? DEST_WIDTH - 1 : 0):0]               TDEST,
  input wire [(USER_WIDTH ? USER_WIDTH - 1 : 0):0]               TUSER,
  input wire                                                     TVALID,
  input wire                                                     TREADY
);
  logic            fatal_to_warnings = 0;
  integer unsigned max_wait_cycles   = MAXWAITS;
endinterface
