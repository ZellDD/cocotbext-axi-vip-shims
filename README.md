# cocotbext-axi-vip-shims

Drive the Xilinx AXI VIPs from [cocotb](https://www.cocotb.org/) through thin
SystemVerilog + DPI-C shims.

The shims add no behaviour of their own. One `put_transaction()` call in Python
is one AXI4-Stream transfer on the bus, with exactly the TDATA / TKEEP / TSTRB /
TLAST / TID / TDEST / TUSER values you passed. On the receive side, every beat
the VIP collects is handed back unchanged. Packet framing, TLAST handling and
TKEEP masking are up to the testbench.

Currently supported:

| VIP                 | Shim modules                           | Python classes                      |
|---------------------|----------------------------------------|-------------------------------------|
| AXI4-Stream (PG277) | `axis_vip_dpi_mst`, `axis_vip_dpi_slv` | `AXIS_MASTER_VIP`, `AXIS_SLAVE_VIP` |

## How it works

```
master:  cocotb ──ctypes──> libaxis_dpi.so ──DPI export──> tx_q ──> VIP master driver ──> DUT
slave :  DUT ──> VIP slave monitor ──> rx_q ──DPI export──> libaxis_dpi.so ──ctypes──> cocotb
```

- **SystemVerilog shim** ([axis_vip_dpi.sv](src/axi_vip_shims/axi_stream_vip_shim/src/hdl/axis_vip_dpi.sv)):
  holds the queues and talks to the VIP agent. All VIP API calls live in
  `vip_*` tasks, so they are easy to adapt to a different Vivado version.
- **DPI-C trampoline** ([axis_dpi.c](src/axi_vip_shims/axi_stream_vip_shim/src/dpi_c/axis_dpi.c)):
  a name → shim-instance table. It packs bytes into 4096-bit DPI vectors and
  calls the shim's exported functions with `svSetScope()` set to the right
  instance.
- **cocotb front end** ([axis_vip_dpi.py](src/axi_vip_shims/axi_stream_vip_shim/src/pyhon/axis_vip_dpi.py)):
  loads the same `.so` with `ctypes` and checks every call against the
  interface you declared.

DPI is crossed only when cocotb pushes, pops, flushes or changes backpressure.
The shims never poll C, and cocotb waits on status signals the shims drive
(`outstanding` on the master, `rx_count` on the slave). An idle interface
therefore costs no DPI calls and no clock-edge wakeups.

The simulator (`-sv_lib`) and cocotb (`ctypes.CDLL`) must load **the same
file**, so that they share one instance table. If they don't, the first call
fails with "has not registered".

## Layout

```
src/
  CMakeLists.txt
  axi_vip_shims/axi_stream_vip_shim/
    CMakeLists.txt
    src/hdl/axis_vip_dpi.sv      SV shims (master + slave)
    src/dpi_c/axis_dpi.c         DPI-C trampoline
    src/pyhon/axis_vip_dpi.py    cocotb classes
test/axi_vip_shims/axi_stream_vip_shims/
  hdl/tb_top.sv                  example top: VIP master -> VIP slave loopback
  python/test_axis_vip.py        cocotb tests (needs Vivado simulator + VIPs)
  python/test_c_side.py          simulator-free test of the C + Python layers
  dpi_c/test_c_side_mock.c       mock simulator/SV side for test_c_side.py
lib/                             built shared libraries
```

## Requirements

- Vivado with the AXI4-Stream VIP. You need a master and a slave VIP
  customised for your interface. For the test bench,
  [gen_vip.tcl](test/axi_vip_shims/axi_stream_vip_shims/hdl/gen_vip.tcl)
  generates them in Vivado batch mode.
- A simulator that supports DPI-C and that cocotb can drive. Tested with
  Questa Altera Starter FPGA Edition 2025.2 (see [Questa notes](#questa-notes)).
  The simulator must handle the Xilinx VIP's SystemVerilog classes, so GHDL
  (VHDL only) and Verilator don't work (see
  [Other simulators](#other-simulators)).
- Python 3 and cocotb.
- A C99 compiler and CMake ≥ 3.16.

## Building the DPI library

```sh
cmake -B build
cmake --build build
```

This produces:

- `libaxis_dpi.so`: load it in the simulator and in cocotb.
- `libaxis_dpi_test.so`: `axis_dpi.c` linked with the mock. Used only by the
  simulator-free test. Disable it with `-DAXI_VIP_SHIMS_BUILD_TEST_LIBS=OFF`.

Both land in the top-level `lib/` (set by `AXI_VIP_SHIMS_LIB_DIR`).

The library is deliberately linked **without** `-Wl,--no-undefined`.
`svGetScope` / `svSetScope` and the SV exports are resolved by the simulator at
load time. The exports are weak, so a design that only has masters (or only
slaves) still loads.

## Using it in a testbench

### 1. Instantiate the shims next to the VIPs

The shim parameters must match the VIP customisation:

```systemverilog
axis_vip_dpi_mst #(.NAME("mst0"), .DATA_BYTES(4),
                   .HAS_TKEEP(1), .HAS_TSTRB(1), .HAS_TLAST(1),
                   .ID_W(4), .DEST_W(4), .USER_W(4))
                   mst_shim (.aclk(aclk));

axis_vip_dpi_slv #(.NAME("slv0"), .DATA_BYTES(4),
                   .HAS_TKEEP(1), .HAS_TSTRB(1), .HAS_TLAST(1),
                   .ID_W(4), .DEST_W(4), .USER_W(4))
                   slv_shim ();
```

The shims find the VIP package, agent type and virtual interface through
macros. Their defaults match [tb_top.sv](test/axi_vip_shims/axi_stream_vip_shims/hdl/tb_top.sv);
override them at compile time to match your component and instance names:

| Macro              | Default                         |
|--------------------|---------------------------------|
| `AXIS_MST_VIP_PKG` | `axi4stream_vip_mst_pkg`        |
| `AXIS_MST_AGENT_T` | `axi4stream_vip_mst_mst_t`      |
| `AXIS_MST_VIF`     | `tb_top.mst_vip.inst.IF`        |
| `AXIS_SLV_VIP_PKG` | `axi4stream_vip_slv_pkg`        |
| `AXIS_SLV_AGENT_T` | `axi4stream_vip_slv_slv_t`      |
| `AXIS_SLV_VIF`     | `tb_top.slv_vip.inst.IF`        |

Slave-only parameters:

- `HARD_RESTART_ON_BP` (default 1): restarts the slave driver for every new
  TREADY pattern, so the pattern takes effect at once. Without it, the VIP
  queues the new pattern behind the running one. It then starts only after the
  current low/high period, after the next handshake for `"none"`, and never
  after a `"stall"`.
- `STALL_CYCLES`: the low time used for `"stall"`.

### 2. Load the library in both places

- Simulator: `-sv_lib /path/to/lib/libaxis_dpi` (or your simulator's
  equivalent).
- cocotb: `AXIS_DPI_LIB=/path/to/lib/libaxis_dpi.so`, or pass
  `lib_path=` to the constructor.

### 3. Drive it from cocotb

```python
from cocotb.triggers import with_timeout
from axis_vip_dpi import AXIS_MASTER_VIP, AXIS_SLAVE_VIP

IFACE = dict(tdata_width=32, has_tkeep=True, has_tstrb=True, has_tlast=True,
             tid_width=4, tdest_width=4, tuser_width=4)

mst = AXIS_MASTER_VIP("mst0", dut.mst_shim, **IFACE)   # name == shim NAME
slv = AXIS_SLAVE_VIP("slv0", dut.slv_shim, **IFACE)

mst.put_transaction(tdata=0x11111111, tlast=False, tid=2)
mst.put_transaction(tdata=0x22222222, tlast=True, tkeep=0b0011, tstrb=0b0011)

slv.set_backpressure("random", low=(0, 8), high=(1, 4))
t = await with_timeout(slv.get_transaction(), 100, "us")
# Transaction(tdata, tkeep, tstrb, tlast, tid, tdest, tuser)

await mst.wait_idle()
```

The constructor describes the interface. On first use, the description is
checked against the shim's parameters and any mismatch is reported field by
field. You can call `check()` to run that check early.

## API summary

**Common**

| Call      | Effect |
|-----------|--------|
| `check()` | Verify the Python description against the SV shim now. |
| `flush()` | Drop transfers queued in the shim. Transfers already handed to the VIP are not affected. |

**`AXIS_MASTER_VIP`**

| Call / attribute | Effect |
|------------------|--------|
| `put_transaction(tdata, *, tkeep, tstrb, tlast, tid, tdest, tuser, delay=0)` | Queue one transfer. Returns immediately and consumes no sim time. `tdata` is an int or exactly `tdata_width/8` little-endian bytes. `tlast` is required when the interface has TLAST. Passing a signal the interface lacks raises `TypeError`. A value too wide for its signal raises `ValueError`. |
| `outstanding` | Transfers queued but not yet on the bus. |
| `await wait_idle()` | Wait until everything queued so far has been sent. Don't call it while the sink is stalled. |

**`AXIS_SLAVE_VIP`**

| Call / attribute | Effect |
|------------------|--------|
| `set_backpressure(mode, low, high)` | Takes effect immediately. `mode` is one of `"none"`, `"osc"`, `"random"` (with `(min, max)` ranges) or `"stall"`. |
| `stall()` / `release()` | Shorthand for `"stall"` / `"none"`. |
| `pending` | Beats received and not yet read. |
| `try_get_transaction()` | Return the oldest `Transaction`, or `None` if nothing has arrived. |
| `await get_transaction()` | Wait for the next `Transaction`. |

Signals that the interface doesn't have come back as `None`. There is no
built-in timeout; wrap waits in `cocotb.triggers.with_timeout()`.

The VIP drives noise on null bytes (TKEEP=0) and position bytes (TSTRB=0), as
AXI4-Stream allows. Compare only data bytes (TKEEP=1 and TSTRB=1). If you pass
`tkeep` without `tstrb`, TSTRB defaults to all ones, which is not allowed where
TKEEP=0. Pass both.

## Tests

- **Simulator-free** ([test_c_side.py](test/axi_vip_shims/axi_stream_vip_shims/python/test_c_side.py)):
  loads `lib/libaxis_dpi_test.so` (`axis_dpi.c` linked with a mock of the
  simulator and SV side), then checks the C trampoline, scope handling, vector
  packing and the Python API. Build the library with CMake first. The test
  needs cocotb importable, but no simulator.

  ```sh
  cmake -B build && cmake --build build
  pytest      # from the repo root
  ```

- **Full simulation** ([test_axis_vip.py](test/axi_vip_shims/axi_stream_vip_shims/python/test_axis_vip.py)):
  runs against [tb_top.sv](test/axi_vip_shims/axi_stream_vip_shims/hdl/tb_top.sv),
  a VIP master → VIP slave loopback. It covers round-trips of every signal,
  packets, runtime backpressure changes, stall/release, and constructor
  mismatch detection. It is started by
  [test_runner.py](test/axi_vip_shims/axi_stream_vip_shims/python/test_runner.py),
  which generates the VIP cores into `build/ip/` on first run:

  ```sh
  python3 test/axi_vip_shims/axi_stream_vip_shims/python/test_runner.py
  ```

  It finds Vivado through `XILINX_VIVADO` or `vivado` on `PATH`. Each cocotb
  test runs in its own `vsim`. It writes its log to
  `test/axi_vip_shims/axi_stream_vip_shims/artifacts/logs/<test>.log` and
  records all signals to `.../artifacts/wlf/<test>.wlf`. The build log is
  `artifacts/logs/build.log`. Open a waveform with
  `vsim -view <path>/artifacts/wlf/test_beats_and_backpressure.wlf`. Set `WAVES=0` to
  skip recording, `GUI=1` to open the GUI, or `TESTCASE=<name>` to run one
  test. `./install.sh` builds everything and runs both test sets.

### Using your own VIP sources

Without Vivado, or with a different Vivado version, pass the VIP sources
yourself:

```sh
./install.sh --vip-src /path/to/vip_sources      # a directory ...
./install.sh --vip-src /path/to/vip.f            # ... or a file list
```

For the runner on its own, set `AXIS_VIP_SRC` to the same path. Vivado is then
not used at all.

- **A directory** must contain the Xilinx VIP packages and interfaces
  (`xil_common_vip_pkg.sv`, `axi4stream_vip_pkg.sv`, `axi4stream_vip_if.sv`,
  ...), the `axi4stream_vip_*_vl_rfs.sv` top, and the generated
  `axi4stream_vip_mst` / `axi4stream_vip_slv` (`.sv` and `_pkg.sv`). It is
  searched recursively and the compile order is worked out for you. Duplicates
  and `_stub.sv` files are skipped, and folders with `.svh` files become
  include dirs.
- **A `.f` file** is compiled exactly in the order given. Use one path per
  line, relative to the `.f` file or absolute, with `$VARS` allowed. Add
  `+incdir+<dir>` lines for include folders, and `//` or `#` for comments.

The generated cores must match `tb_top.sv` (4-byte TDATA, TKEEP, TSTRB, TLAST,
and 4-bit TID/TDEST/TUSER).

**Older versions:** the AXI4-Stream VIP exists only in Vivado (since about
2017), so there is nothing to use from ISE. Older Vivado releases may name
VIP API calls differently. The shim keeps every VIP call in a `vip_*` task in
[axis_vip_dpi.sv](src/axi_vip_shims/axi_stream_vip_shim/src/hdl/axis_vip_dpi.sv)
so they can be adapted. Only Vivado 2025.2 has been tested.

## Questa notes

The runner sets three things that the shims need on Questa:

- `+define+XIL_DO_NOT_USE_ADV_RANDOMIZATION`: the VIP uses `$urandom` instead
  of `randomize()` / `randcase`. Those need a SystemVerilog verification
  licence, which the Starter edition doesn't have.
- `-dpioutoftheblue 1`: cocotb calls the shims' DPI exports from VPI callbacks,
  not from inside an SV import call chain.
- `-voptargs=+acc`: keeps the shims' status signals visible to cocotb.

The shims drive their status signals (`outstanding`, `rx_count`) with
nonblocking assignments, so cocotb never wakes up in the middle of a shim
process or of a DPI call. The Python side also leaves each status-signal
callback (through `ReadWrite`) before waiting again. On Questa, a value-change
wait re-armed from inside its own callback is never woken.

## Other simulators

- **GHDL** simulates VHDL only. The Xilinx VIP and the shims are
  SystemVerilog, and the C side is reached through SystemVerilog DPI-C.
- **Verilator** (tried with 5.032) can't build the test bench:
  - The VIP's protocol checker (`axi4stream_vip_axi4streampc`) is written in
    SVA with `##[m:n]` sequences, which Verilator doesn't support. The VIP
    interface instantiates the checker unconditionally.
  - The VIP package calls some methods without parentheses
    (`this.get_data_width`, `this.get_verbosity`), which Verilator rejects.
  - The blocker: the shims hand the VIP its interface through a hierarchical
    path (`tb_top.mst_vip.inst.IF`), as Xilinx's examples do. Verilator 5.032
    crashes with an internal fault on a parameterized interface passed to a
    virtual interface by hierarchical path. A 30-line example reproduces it.
    The first two problems can be worked around (a stub checker, a patched
    copy of the package); this one would need changes to Xilinx's code.

  A newer Verilator might get further, but even then it is untested whether
  the VIP's class-based driver, monitor and mailboxes, and the DPI calls from
  cocotb callbacks, work there.
