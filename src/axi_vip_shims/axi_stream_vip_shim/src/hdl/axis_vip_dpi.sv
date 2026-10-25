// ============================================================================
// axis_vip_dpi.sv  --  cocotb <-> Xilinx AXI4-Stream VIP shim, DPI-C version
//
// The shim has NO behaviour of its own. One cocotb put_transaction() call is
// one AXI4-Stream transfer, with exactly the TDATA/TKEEP/TSTRB/TLAST/TID/
// TDEST/TUSER values (and idle delay) cocotb passed. The slave side hands
// every received beat to cocotb unchanged.
//
// The queues are plain SV queues inside the shims:
//
//   master:  cocotb --DPI export--> tx_q --> VIP master driver --> DUT
//   slave :  DUT --> VIP slave monitor --> rx_q --DPI export--> cocotb
//
// DPI is crossed only when cocotb puts/gets a transaction or changes
// backpressure. The SV side never calls into C while it runs: the feeder just
// waits on its own queue, and received beats go straight into rx_q. A quiet
// interface costs no DPI calls and no clock-edge polling.
//
// cocotb learns about changes through two status signals the shims drive
// (never written by cocotb): `outstanding` (master) and `rx_count` (slave).
// They are written with NONBLOCKING assignments on purpose: cocotb resumes
// coroutines from the value-change callback, and a coroutine that calls back
// into a shim (DPI export) must not run in the middle of a shim process or of
// another export call. In the NBA region neither is running.
//
// Every call into the Xilinx VIP API sits in a task whose name starts with
// vip_ so it is easy to adjust per Vivado version (PG277).
// ============================================================================
`timescale 1ns/1ps

// ---- Edit to match the component names you gave the VIPs in Vivado ---------
`ifndef AXIS_MST_VIP_PKG
  `define AXIS_MST_VIP_PKG axi4stream_vip_mst_pkg
`endif
`ifndef AXIS_MST_AGENT_T
  `define AXIS_MST_AGENT_T axi4stream_vip_mst_mst_t
`endif
`ifndef AXIS_MST_VIF
  `define AXIS_MST_VIF tb_top.mst_vip.inst.IF
`endif
`ifndef AXIS_SLV_VIP_PKG
  `define AXIS_SLV_VIP_PKG axi4stream_vip_slv_pkg
`endif
`ifndef AXIS_SLV_AGENT_T
  `define AXIS_SLV_AGENT_T axi4stream_vip_slv_slv_t
`endif
`ifndef AXIS_SLV_VIF
  `define AXIS_SLV_VIF tb_top.slv_vip.inst.IF
`endif


package axis_dpi_pkg;
  // Wide signals cross DPI as fixed 4096-bit vectors; unused upper bits are 0.
  // Keep in sync with VEC_WORDS in axis_dpi.cpp.
  localparam int AXIS_VEC_BITS = 4096;
  typedef bit [AXIS_VEC_BITS-1:0] axis_vec_t;
endpackage


// ============================================================================
// Master shim
//   ID_W / DEST_W / USER_W = 0 means that signal does not exist.
// ============================================================================
module axis_vip_dpi_mst #(
  parameter string NAME       = "mst0",   // cocotb finds the instance by this
  parameter int    DATA_BYTES = 4,        // must equal the VIP's TDATA width
  parameter bit    HAS_TKEEP  = 1'b0,
  parameter bit    HAS_TSTRB  = 1'b0,
  parameter bit    HAS_TLAST  = 1'b0,
  parameter int    ID_W       = 0,
  parameter int    DEST_W     = 0,
  parameter int    USER_W     = 0
)(
  input logic aclk
);
  import axi4stream_vip_pkg::*;
  import `AXIS_MST_VIP_PKG::*;
  import axis_dpi_pkg::*;

  // Declared inside the module on purpose: a `context` import's scope is then
  // this instance, which the C side remembers to select it for the exports.
  import "DPI-C" context function int axis_dpi_sv_register(
    input string name, input int role, input int data_bytes,
    input int has_tkeep, input int has_tstrb, input int has_tlast,
    input int id_w, input int dest_w, input int user_w);

  export "DPI-C" function axis_mst_push;
  export "DPI-C" function axis_mst_flush;

  localparam int UW = (USER_W > 0) ? USER_W : 1;

  typedef struct {
    bit [DATA_BYTES*8-1:0] data;
    bit [DATA_BYTES-1:0]   keep;
    bit [DATA_BYTES-1:0]   strb;
    bit [UW-1:0]           user;
    bit                    last;
    int                    id;
    int                    dest;
    int                    delay;
  } beat_t;

  int inst;
  `AXIS_MST_AGENT_T agent;

  beat_t       tx_q[$];
  int unsigned pushed = 0;     // accepted from cocotb
  int unsigned sent   = 0;     // handed to the VIP driver
  int unsigned done   = 0;     // VIP has put them on the bus

  // Status for cocotb (read-only there): transfers put but not yet on the bus.
  logic [31:0] outstanding = '0;

  // ---------------- DPI exports (called from cocotb via axis_dpi.cpp) ---------
  function int axis_mst_push(input axis_vec_t data, keep, strb, user,
                             input int last, id, dest, delay);
    beat_t b;
    b.data  = data[DATA_BYTES*8-1:0];
    b.keep  = keep[DATA_BYTES-1:0];
    b.strb  = strb[DATA_BYTES-1:0];
    b.user  = user[UW-1:0];
    b.last  = (last != 0);
    b.id    = id;
    b.dest  = dest;
    b.delay = delay;
    tx_q.push_back(b);
    pushed++;
    outstanding <= pushed - done;
    return 0;
  endfunction

  function int axis_mst_flush();
    pushed -= tx_q.size();
    tx_q.delete();
    outstanding <= pushed - done;
    return 0;
  endfunction

  // ---------------- VIP API ----------------
  task automatic vip_send_beat(input beat_t b);
    axi4stream_transaction t;
    t = agent.driver.create_transaction("dpi_beat");
    t.set_data_beat(b.data);
    if (HAS_TKEEP)  t.set_keep_beat(b.keep);
    if (HAS_TSTRB)  t.set_strb_beat(b.strb);
    if (HAS_TLAST)  t.set_last(b.last);
    if (ID_W   > 0) t.set_id(b.id);
    if (DEST_W > 0) t.set_dest(b.dest);
    if (USER_W > 0) t.set_user_beat(b.user);
    t.set_delay(b.delay);
    agent.driver.send(t);               // queued in the VIP, returns at once
  endtask

  // The AXI4-Stream driver has no blocking "wait idle"; poll is_driver_idle()
  // until it holds for two cycles (it can flicker between queued transfers).
  task automatic vip_wait_idle();
    int quiet = 0;
    while (quiet < 2) begin
      @(posedge aclk);
      quiet = agent.driver.is_driver_idle() ? quiet + 1 : 0;
    end
  endtask
  // -----------------------------------------

  // Forward every queued beat to the VIP, unchanged. Sleeps while tx_q is empty.
  task automatic feeder();
    beat_t b;
    forever begin
      wait (tx_q.size() > 0);
      b = tx_q.pop_front();
      vip_send_beat(b);
      sent++;
    end
  endtask

  // Tracks when the VIP has really put the data on the bus (what cocotb's
  // wait_idle() waits for). Sleeps while nothing is in flight.
  task automatic idle_monitor();
    int unsigned snap;
    forever begin
      wait (sent > done);
      snap = sent;
      repeat (2) @(posedge aclk);       // let the driver pick the beats up
      vip_wait_idle();
      done = snap;
      outstanding <= pushed - done;
    end
  endtask

  initial begin
    inst = axis_dpi_sv_register(NAME, 0, DATA_BYTES, int'(HAS_TKEEP),
                                int'(HAS_TSTRB), int'(HAS_TLAST),
                                ID_W, DEST_W, USER_W);
    if (inst < 0) $fatal(1, "axis_vip_dpi_mst %s: register failed", NAME);
    agent = new(NAME, `AXIS_MST_VIF);
    agent.set_agent_tag(NAME);
    agent.start_master();
    fork
      feeder();
      idle_monitor();
    join_none
  end
endmodule


// ============================================================================
// Slave shim
//   * TREADY behaviour is set from cocotb; a change is applied immediately
//     while the simulation keeps running
//   * every received beat is queued for cocotb as is (no packet assembly, no
//     dropping of TKEEP=0 bytes)
// ============================================================================
module axis_vip_dpi_slv #(
  parameter string NAME       = "slv0",
  parameter int    DATA_BYTES = 4,
  parameter bit    HAS_TKEEP  = 1'b0,
  parameter bit    HAS_TSTRB  = 1'b0,
  parameter bit    HAS_TLAST  = 1'b0,
  parameter int    ID_W       = 0,
  parameter int    DEST_W     = 0,
  parameter int    USER_W     = 0,
  // 1: restart the slave driver for every new TREADY pattern, so it replaces
  //    the running one at once. The VIP otherwise queues it behind the
  //    running one: it starts after the current low+high period, after the
  //    next handshake for "none", and never after a stall.
  parameter bit    HARD_RESTART_ON_BP = 1'b1,
  parameter int    STALL_CYCLES       = 1 << 30
);
  import axi4stream_vip_pkg::*;
  import `AXIS_SLV_VIP_PKG::*;
  import axis_dpi_pkg::*;

  import "DPI-C" context function int axis_dpi_sv_register(
    input string name, input int role, input int data_bytes,
    input int has_tkeep, input int has_tstrb, input int has_tlast,
    input int id_w, input int dest_w, input int user_w);

  export "DPI-C" function axis_slv_pop;
  export "DPI-C" function axis_slv_set_bp;
  export "DPI-C" function axis_slv_flush;

  localparam int BP_NONE = 0, BP_OSC = 1, BP_RANDOM = 2, BP_STALL = 3; // = axis_dpi.cpp
  localparam int UW = (USER_W > 0) ? USER_W : 1;

  typedef struct {
    bit [DATA_BYTES*8-1:0] data;
    bit [DATA_BYTES-1:0]   keep;
    bit [DATA_BYTES-1:0]   strb;
    bit [UW-1:0]           user;
    bit                    last;
    int                    id;
    int                    dest;
  } beat_t;

  int inst;
  `AXIS_SLV_AGENT_T agent;

  beat_t rx_q[$];

  // Status for cocotb (read-only there): beats waiting in rx_q.
  logic [31:0] rx_count = '0;

  // backpressure request from cocotb; bp_version bumps on every change
  int bp_version = 0;
  int bp_mode = BP_NONE, bp_low_min = 0, bp_low_max = 0,
      bp_high_min = 1, bp_high_max = 1;

  // ---------------- DPI exports (called from cocotb via axis_dpi.cpp) ---------
  function int axis_slv_pop(output axis_vec_t data, keep, strb, user,
                            output int last, id, dest);
    beat_t b;
    data = '0; keep = '0; strb = '0; user = '0;
    last = 0; id = 0; dest = 0;
    if (rx_q.size() == 0) return 0;
    b = rx_q.pop_front();
    data[DATA_BYTES*8-1:0] = b.data;
    keep[DATA_BYTES-1:0]   = b.keep;
    strb[DATA_BYTES-1:0]   = b.strb;
    user[UW-1:0]           = b.user;
    last = int'(b.last);
    id   = b.id;
    dest = b.dest;
    rx_count <= rx_q.size();
    return 1;
  endfunction

  function int axis_slv_set_bp(input int mode, low_min, low_max,
                               high_min, high_max);
    bp_mode = mode;
    bp_low_min = low_min;   bp_low_max = low_max;
    bp_high_min = high_min; bp_high_max = high_max;
    bp_version++;
    return 0;
  endfunction

  function int axis_slv_flush();
    rx_q.delete();
    rx_count <= 0;
    return 0;
  endfunction

  // ---------------- VIP API ----------------
  task automatic vip_apply_bp(input bit restart,
                              input int mode, input int low_min, input int low_max,
                              input int high_min, input int high_max);
    axi4stream_ready_gen rg;
    rg = agent.driver.create_ready("dpi_bp");
    case (mode)
      BP_OSC: begin
        rg.set_ready_policy(XIL_AXI4STREAM_READY_GEN_OSC);
        rg.set_low_time(low_min);
        rg.set_high_time(high_min);
      end
      BP_RANDOM: begin
        rg.set_ready_policy(XIL_AXI4STREAM_READY_GEN_RANDOM);
        rg.set_low_time_range(low_min, low_max);
        rg.set_high_time_range(high_min, high_max);
      end
      BP_STALL: begin                    // TREADY low until the next config
        rg.set_ready_policy(XIL_AXI4STREAM_READY_GEN_OSC);
        rg.set_low_time(STALL_CYCLES);
        rg.set_high_time(1);
      end
      default: begin                     // BP_NONE
        rg.set_ready_policy(XIL_AXI4STREAM_READY_GEN_NO_BACKPRESSURE);
      end
    endcase
    if (restart) begin
      // Driver only: agent.start_slave() would start a second monitor.
      agent.driver.stop_phase();
      while (agent.driver.get_is_active()) agent.vif_proxy.wait_posedge_aclk();
      fork agent.driver.run_phase(); join_none
      // send_tready() needs an active driver; run_phase() sets that first thing
      while (!agent.driver.get_is_active()) #0;
    end
    agent.driver.send_tready(rg);
    // The driver's pattern queue cannot be cleared from here: let it take rg
    // off the queue before the next change, so no stale pattern is left.
    if (restart) agent.vif_proxy.wait_aclks(3);
  endtask

  // Signals that do not exist in this interface come back as 0.
  task automatic vip_get_beat(output beat_t b);
    axi4stream_monitor_transaction mt;
    xil_axi4stream_data_beat d;
    xil_axi4stream_strb_beat k;          // the VIP types TKEEP like TSTRB
    xil_axi4stream_strb_beat s;
    xil_axi4stream_user_beat u;
    agent.monitor.item_collected_port.get(mt);
    b = '{default: '0};
    d = mt.get_data_beat();
    b.data = d[DATA_BYTES*8-1:0];
    if (HAS_TKEEP) begin k = mt.get_keep_beat(); b.keep = k[DATA_BYTES-1:0]; end
    if (HAS_TSTRB) begin s = mt.get_strb_beat(); b.strb = s[DATA_BYTES-1:0]; end
    if (HAS_TLAST) b.last = mt.get_last();
    if (ID_W   > 0) b.id   = int'(mt.get_id());
    if (DEST_W > 0) b.dest = int'(mt.get_dest());
    if (USER_W > 0) begin u = mt.get_user_beat(); b.user = u[UW-1:0]; end
  endtask
  // -----------------------------------------

  // Sleeps until cocotb changes the backpressure request.
  task automatic bp_watcher();
    int seen;
    seen = -1;                           // applies the initial config at once
    forever begin
      wait (bp_version != seen);
      seen = bp_version;
      vip_apply_bp(HARD_RESTART_ON_BP && seen > 0, bp_mode, bp_low_min, bp_low_max,
                   bp_high_min, bp_high_max);
    end
  endtask

  task automatic collector();
    beat_t b;
    forever begin
      vip_get_beat(b);
      rx_q.push_back(b);
      rx_count <= rx_q.size();
    end
  endtask

  initial begin
    inst = axis_dpi_sv_register(NAME, 1, DATA_BYTES, int'(HAS_TKEEP),
                                int'(HAS_TSTRB), int'(HAS_TLAST),
                                ID_W, DEST_W, USER_W);
    if (inst < 0) $fatal(1, "axis_vip_dpi_slv %s: register failed", NAME);
    agent = new(NAME, `AXIS_SLV_VIF);
    agent.set_agent_tag(NAME);
    agent.start_slave();
    fork
      bp_watcher();
      collector();
    join_none
  end
endmodule
