# Example: using the shims in your own project

A small, self-contained project that verifies its own DUT with the AXI4-Stream
VIP shims. It is laid out the way a separate repository would be: it only
*references* the shim sources, it doesn't copy them.

```
VIP master ──► axis_byteswap (DUT) ──► VIP slave
    ▲                                     │
 src_shim                             sink_shim
    ▲                                     ▼
       cocotb: tests/test_byteswap.py
```

The DUT reverses the byte order of every beat (TDATA and TKEEP), and passes
TLAST and TUSER through. The interface is deliberately different from the
shims' own test bench: TDATA 4 bytes, TKEEP, TLAST, 1-bit TUSER, with no TSTRB,
TID or TDEST. The VIP component and instance names are also this project's own,
so the shim macros have to be overridden (see [step 3](#3-the-test-bench)).

## Layout

```
CMakeLists.txt             builds libaxis_dpi.so from the shims into build/lib/
pytest.ini                 collects only *_runner.py
conftest.py                prints the coverage summary at the end of pytest
rtl/axis_byteswap.sv       the DUT
tb/gen_vip.tcl             generates the two VIP cores (ex_src_vip, ex_sink_vip)
tb/tb_byteswap.sv          VIPs + DUT + shims
tests/test_byteswap.py     cocotb tests, with a Python golden model
tests/test_byteswap_runner.py  builds everything and runs the tests (pytest)
build/                     generated: lib/, ip/, sim_<SIM>/
```

## Running it

You need Vivado (for the Xilinx VIP sources), CMake, a C/C++ compiler, cocotb
≥ 2.0 with pytest, and Verilator ≥ 5.052 or Questa.

```sh
cd examples/axis_byteswap
pytest                                   # Verilator
SIM=questa pytest                        # Questa
python3 tests/test_byteswap_runner.py    # without pytest or coverage, prints a summary
TESTCASE=test_partial_last_beat WAVES=1 pytest
```

On the first run, the runner builds `libaxis_dpi.so` with CMake, generates the
VIP cores with Vivado into `build/ip/`, then builds the simulation and runs all
cocotb tests in a single simulation.

## Coverage

Every `pytest` (or `pytest -s`) run measures code coverage of the DUT
(`rtl/`) and prints a summary at the end. `DUT_COVERAGE=0` turns it off.
`python3 tests/test_byteswap_runner.py` runs without coverage. (Not `COVERAGE`: cocotb reads that
one as its own switch for coverage of the Python test code.)

```
=========================== DUT coverage (verilator) ===========================
Annotation Summary:
  lines with all attached points covered : 100.00%  (25/25)
```

| | Verilator | Questa |
|---|---|---|
| Measured | line, branch, toggle (`--coverage`) | statement, branch, condition, expression, FSM, toggle (`+cover=bcesft`) |
| Only the DUT, by | a generated `coverage.vlt` with `coverage_off` for every other source | `+cover=bcesft+axis_byteswap` (the DUT's module name) |
| Raw data | `build/sim_verilator/coverage.dat` | `build/sim_questa/coverage/coverage.ucdb` |
| Report | `coverage/annotated/` (annotated source), `coverage/coverage.info` (lcov, e.g. for `genhtml`) | `coverage/html/` |

The report goes to `build/sim_<SIM>/coverage/`, together with `summary.txt`,
which [conftest.py](conftest.py) prints under pytest. The Questa report counts
code coverage only (`vcover report -code bcesft`). If it didn't, the VIP's
protocol-checker assertions would be in the total as well.

To measure more of your design, add files to `DUT_SOURCES` (Verilator) and
modules to the `+cover=...+<module>` selection (Questa) in
[test_byteswap_runner.py](tests/test_byteswap_runner.py).

## Setting this up in your own repository

### 1. Get the shims

Use a git submodule (or any checkout, really):

```sh
git submodule add https://github.com/ZellDD/cocotbext-axi-vip-shims third_party/cocotbext-axi-vip-shims
```

Then point the example at it. In `CMakeLists.txt`, change the
`AXI_VIP_SHIMS_DIR` default (or pass `-DAXI_VIP_SHIMS_DIR=...`). In the runner,
set `AXI_VIP_SHIMS_DIR` (or change its default). Inside this repo, both default
to the repo root, two levels up.

You use three files from the shims, all under
`src/axi_vip_shims/axi_stream_vip_shim/`:

| File | Used by | How |
|------|---------|-----|
| `CMakeLists.txt` (via `src/CMakeLists.txt`) | CMake | `add_subdirectory` builds `libaxis_dpi.so` |
| `src/hdl/axis_vip_dpi.sv` | simulator | compiled with your bench, after the VIP sources |
| `src/python/axis_vip_dpi.py` | cocotb | put its directory on the Python path |

### 2. Build the DPI library from your CMake

```cmake
set(AXI_VIP_SHIMS_LIB_DIR "${CMAKE_BINARY_DIR}/lib" CACHE PATH "")
set(AXI_VIP_SHIMS_BUILD_TEST_LIBS OFF CACHE BOOL "")
add_subdirectory("${AXI_VIP_SHIMS_DIR}/src" axi_vip_shims)
```

Set them as cache entries *before* `add_subdirectory`. If you don't, the
library lands in the shims checkout's `lib/`, and the mock-simulator test
library is built as well.

### 3. The test bench

Generate one master and one slave VIP with your interface
([gen_vip.tcl](tb/gen_vip.tcl)), and instantiate a shim next to each, with
parameters that match ([tb_byteswap.sv](tb/tb_byteswap.sv)):

```systemverilog
axis_vip_dpi_mst #(.NAME("src"), .DATA_BYTES(4),
                   .HAS_TKEEP(1), .HAS_TLAST(1), .USER_W(1))
                   src_shim (.aclk(aclk));
axis_vip_dpi_slv #(.NAME("sink"), .DATA_BYTES(4),
                   .HAS_TKEEP(1), .HAS_TLAST(1), .USER_W(1))
                   sink_shim ();
```

The shims find the VIP agents through macros. The VIP package is
`<component>_pkg`, and the agent type is `<component>_mst_t` or
`<component>_slv_t`. The virtual interface is `<top>.<vip instance>.inst.IF`.
For this bench, the runner defines:

| Macro              | Value                          |
|--------------------|--------------------------------|
| `AXIS_MST_VIP_PKG` | `ex_src_vip_pkg`               |
| `AXIS_MST_AGENT_T` | `ex_src_vip_mst_t`             |
| `AXIS_MST_VIF`     | `tb_byteswap.u_src.inst.IF`    |
| `AXIS_SLV_VIP_PKG` | `ex_sink_vip_pkg`              |
| `AXIS_SLV_AGENT_T` | `ex_sink_vip_slv_t`            |
| `AXIS_SLV_VIF`     | `tb_byteswap.u_sink.inst.IF`   |

The macros are global, so all master shims in one build use the same VIP
component (and likewise for slaves).

### 4. Compile order and simulator flags

See `build()` and `run()` in
[test_byteswap_runner.py](tests/test_byteswap_runner.py):

1. Xilinx VIP library from `$XILINX_VIVADO/data/xilinx_vip/hdl/`:
   `axi4stream_vip_axi4streampc.sv`, `xil_common_vip_pkg.sv`,
   `axi4stream_vip_pkg.sv`, `axi4stream_vip_if.sv`, with
   `data/xilinx_vip/include` as the include dir.
2. The VIP top `data/ip/xilinx/axi4stream_vip_v1_1*/hdl/axi4stream_vip_v1_1_vl_rfs.sv`.
3. Your generated cores, each `_pkg.sv` before its wrapper (skip `_stub.sv`).
4. `axis_vip_dpi.sv`, then your DUT and bench.

Plus, on every simulator, the define `XIL_DO_NOT_USE_ADV_RANDOMIZATION`, the
macros above, and `AXIS_DPI_LIB=<the .so>` in cocotb's environment. Simulator
and cocotb must use **the same file**.

| | Verilator | Questa |
|---|---|---|
| DPI library | linked into the model: `<lib>.so -LDFLAGS -Wl,-rpath,<lib dir>` | `-sv_lib <lib without .so>` at run time |
| Other flags | `--timing --assert -Wno-fatal -Wno-lint -Wno-style` | `-dpioutoftheblue 1 -voptargs=+acc` |
| Extra step | patched copy of the protocol checker (`tri0`/`tri1` → `wire`) | — |

The shims' main [README](../../README.md#verilator-notes) explains each of
these.

### 5. The cocotb side

```python
from axis_vip_dpi import AXIS_MASTER_VIP, AXIS_SLAVE_VIP

IFACE = dict(tdata_width=32, has_tkeep=True, has_tlast=True, tuser_width=1)
src  = AXIS_MASTER_VIP("src",  dut.src_shim,  **IFACE)   # name == shim NAME
sink = AXIS_SLAVE_VIP("sink", dut.sink_shim, **IFACE)
```

The cocotb runner passes its own `sys.path` to the simulator as `PYTHONPATH`.
So add the shims' `src/python` directory and your test directory to `sys.path`
before you call `runner.test()`; setting `PYTHONPATH` in `extra_env` gets
overwritten.

The VIP drives noise on null bytes, so the tests compare only the bytes that
TKEEP marks (`data_bytes()` in [test_byteswap.py](tests/test_byteswap.py)).
