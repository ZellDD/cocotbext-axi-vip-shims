# Tests

[← README](../README.md)

- **Simulator-free** ([test_c_side.py](../test/axi_vip_shims/axi_stream_vip_shims/python/test_c_side.py)):
  loads `lib/libaxis_dpi_test.so` (`axis_dpi.cpp` linked with a mock of the
  simulator and SV side), then checks the C trampoline, scope handling, vector
  packing and the Python API. Build the library with CMake first. The test
  needs cocotb importable, but no simulator.

  ```sh
  cmake -B build && cmake --build build
  pytest      # from the repo root
  ```

- **Full simulation** ([test_axis_vip.py](../test/axi_vip_shims/axi_stream_vip_shims/test_cases/test_axis_vip.py)):
  runs against [tb_top.sv](../test/axi_vip_shims/axi_stream_vip_shims/hdl/tb_top.sv),
  a VIP master → VIP slave loopback. There is one test per AXI4-Stream
  option: TDATA, TKEEP, TSTRB, TLAST, TID, TDEST, TUSER, `delay`, each
  backpressure mode (none, osc, random, stall/release, change in flight),
  `flush()`, `wait_idle()`, argument checking and constructor mismatch
  detection. A monitor on the bus wires checks delays and backpressure by the
  cycle. The tests are started by
  [test_axis_vip_questa_runner.py](../test/axi_vip_shims/axi_stream_vip_shims/python/test_axis_vip_questa_runner.py),
  which generates the VIP cores into `build/ip/` on first run:

  ```sh
  python3 test/axi_vip_shims/axi_stream_vip_shims/python/test_axis_vip_questa_runner.py
  ```

  It finds Vivado through `XILINX_VIVADO` or `vivado` on `PATH`. Each cocotb
  test runs in its own `vsim`, with its log in
  `artifacts/test_axis_vip_questa_runner/logs/<test>.log` and all signals recorded
  to `artifacts/test_axis_vip_questa_runner/wlf/<test>.wlf` (see
  [Test artifacts](#test-artifacts)). Open a waveform with
  `vsim -view <path>/wlf/test_tdata.wlf`. Set `WAVES=0` to
  skip recording, `GUI=1` to open the GUI, or `TESTCASE=<name>` to run one
  test. `./install.sh` builds everything and runs the simulator-free test,
  plus the simulation tests of each simulator (Questa, Verilator) that is on
  `PATH`, provided VIP sources are available.

- **Full simulation on Verilator**
  ([test_axis_vip_verilator_runner.py](../test/axi_vip_shims/axi_stream_vip_shims/python/test_axis_vip_verilator_runner.py)):
  the same tests and VIP sources, built into one Verilator model under
  `build/sim_build_verilator_cov/` under pytest (instrumented for
  [Coverage](#coverage)), or `build/sim_build_verilator/` when run as a
  script, with `EXT_SHIMS_COVERAGE=0`, and for the benchmark:

  ```sh
  python3 test/axi_vip_shims/axi_stream_vip_shims/python/test_axis_vip_verilator_runner.py
  pytest test/axi_vip_shims/axi_stream_vip_shims/python/test_axis_vip_verilator_runner.py
  ```

  Under pytest it is skipped when `verilator` is not on `PATH`. Each cocotb
  test runs in its own model run. Logs go to
  `artifacts/test_axis_vip_verilator_runner/logs/<test>.log` and waveforms
  to `.../vcd/<test>.vcd` (view with GTKWave or Surfer). `WAVES=0`, `GUI=1` (opens GTKWave after one
  run of all selected tests) and `TESTCASE=<a>,<b>` work as on Questa. Waves
  are a build-time option in Verilator, so `WAVES=0` builds the model without
  tracing.

  With Verilator 5.052, 18 of the 19 tests pass. `test_flush_slave` fails:
  five cycles after `wait_idle()`, `pending` shows 4 beats instead of 5.

## Coverage

Every `pytest` (or `pytest -s`) run from the repo root reports coverage of
all the shim code at the end of the run. The Xilinx VIP and any DUT are left
out.

| Layer | Measured with | Where it runs | Report |
|-------|---------------|---------------|--------|
| Python `axis_vip_dpi.py` | pytest-cov ([pytest.ini](../pytest.ini), [.coveragerc](../.coveragerc)) in-process; cocotb's own coverage (`COCOTB_USER_COVERAGE`) inside the simulator; combined | `test_c_side.py`, functional simulation tests | `artifacts/coverage/python/` |
| C `axis_dpi.cpp` | gcov + gcovr: `libaxis_dpi_test.so` in-process, `libaxis_dpi_cov.so` in the simulator; combined | both | `artifacts/coverage/c/` |
| SV `axis_vip_dpi.sv`, `tb_top.sv` | Verilator `--coverage-line --coverage-expr`; Questa `+cover=bcse` | functional simulation tests | `artifacts/coverage/sv_verilator/`, `artifacts/coverage/sv_questa/` |

- Each simulation run saves its SV data into `build/coverage/raw/<sim>/<test>`.
  At the end, the runs are merged (`verilator_coverage --write`,
  `vcover merge`) and reported. Verilator gives an annotated source and an
  lcov `coverage.info`; Questa gives an HTML report.
- Only the shims and the bench are instrumented. Verilator gets a generated
  `coverage.vlt` that switches coverage off for every Xilinx VIP file. Questa
  selects the design units `axis_dpi_pkg`, `axis_vip_dpi_mst`,
  `axis_vip_dpi_slv` and `tb_top`.
- There is no toggle coverage. The shims pass 4096-bit DPI vectors, and their
  unused upper bits would swamp the number.
- The benchmarks never collect coverage, so it can't skew their timings. They
  keep the uninstrumented `libaxis_dpi.so` and their own Verilator model.
- Coverage is collected only under pytest (`pytest -s` from the repo root).
  A runner started as a script (`python3 test_axis_vip_*_runner.py`) runs
  without it. [conftest.py](../conftest.py) resets the C and SV data when the
  session starts. `EXT_SHIMS_COVERAGE=0` turns coverage off under pytest too.
  The switch isn't called `COVERAGE`, because cocotb reads that name as a
  (deprecated) switch for its own coverage.
- The Python and C reports are also written as Cobertura XML
  (`artifacts/coverage/{python,c}/coverage.xml`), for tools such as Codecov or an
  IDE coverage view.

CI uploads the whole `artifacts/` directory (see
[Test artifacts](#test-artifacts)) once per job, coverage included:
`unit-py<version>-artifacts` (Python and C, simulator-free) and
`verilator-artifacts` (all three layers, with that job's logs and JUnit XML).

The shared code is in
[coverage_common.py](../test/axi_vip_shims/axi_stream_vip_shims/python/coverage_common.py).
For HDL coverage of your own DUT, see the
[example](../examples/axis_byteswap/README.md#coverage).

## Test artifacts

Everything a test run produces for people to look at goes under
`test/axi_vip_shims/axi_stream_vip_shims/artifacts/`, so CI uploads that one
directory. Each runner writes into its own directory, named after the runner
script, and only what applies to it. The coverage reports sit next to them:

```
artifacts/
  test_axis_vip_questa_runner/           build.log  logs/<test>.log  wlf/<test>.wlf
  test_axis_vip_verilator_runner/        build.log  logs/<test>.log  vcd/<test>.vcd
  test_axis_benchmark_runner/            build.log  logs/<test>.log  results/  benchmark.txt
  test_axis_benchmark_verilator_runner/  build.log  logs/<test>.log  results/  benchmark.txt
  coverage/                              python/  c/  sv_verilator/  sv_questa/
  junit-verilator.xml                    (CI only)
```

- `build.log` is the log of the build this run used. The Questa runners share
  one build in `build/sim_build_questa/`, so the same log can show up in two
  directories. The Verilator runners build into `build/sim_build_verilator_cov/`
  (functional tests under pytest, with coverage) and `build/sim_build_verilator/`
  (benchmark, script runs, or `EXT_SHIMS_COVERAGE=0`).
- `results/` holds each benchmark case's `<test>.json` while the runner
  collects it into `benchmark.txt`.
- `coverage/` holds the reports from the last `pytest` run (see
  [Coverage](#coverage)). The raw data they're made from stays in
  `build/coverage/` and `build/.coverage`.

The whole tree is generated and ignored by git; `make clean` empties it.

## Using your own VIP sources

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
[axis_vip_dpi.sv](../src/axi_vip_shims/axi_stream_vip_shim/src/hdl/axis_vip_dpi.sv)
so they can be adapted. Only Vivado 2025.2 has been tested.

## Benchmark

[test_axis_benchmark.py](../test/axi_vip_shims/axi_stream_vip_shims/test_cases/test_axis_benchmark.py)
pushes 10,240 beats (TKEEP, TLAST, TID, TDEST, TUSER; every byte checked)
through the VIP shims and, for comparison, through
[cocotbext-axi](https://github.com/alexforencich/cocotbext-axi)'s
`AxiStreamSource`/`AxiStreamSink` on a plain loopback (`bm_*` in `tb_top.sv`).
It has its own runner,
[test_axis_benchmark_runner.py](../test/axi_vip_shims/axi_stream_vip_shims/python/test_axis_benchmark_runner.py),
and runs without waveform recording. Under pytest it is skipped unless
`BENCH=1` is set:

```sh
python3 test/axi_vip_shims/axi_stream_vip_shims/python/test_axis_benchmark_runner.py
BENCH=1 pytest -k benchmark -s           # same, through pytest
BENCH_BEATS=50000 python3 .../test_axis_benchmark_runner.py   # more beats
BENCH_REPEATS=5 python3 .../test_axis_benchmark_runner.py     # runs per case (default 3)
```

Every run of every case is a fresh `vsim`. The table, with the median wall
time and the spread across runs, goes to
`artifacts/test_axis_benchmark_runner/benchmark.txt`.
cocotbext-axi's per-frame INFO logging is switched off during the benchmark,
because the shims log nothing per beat.

Two things are varied. The packet size (16 beats or 1) matters for
cocotbext-axi, which builds a frame per packet. The other is *when the
receiver reads* (the `reads` column): `as it comes` takes each beat the moment
it arrives, which is all cocotbext-axi's sink does and what `get_transaction()`
does. `every B` lets the shim queue the beats while cocotb sleeps on a timer
(no per-beat wakeups) until B are waiting, then drains those B
(`bench_vip_packets_batch_*`). The packet size doesn't matter to the shims.

Taking every beat as it arrives, the shims are slower than cocotbext-axi
(about 17 s against 14 s for 16-beat packets on Questa Starter), and batched
reads are about 1.6× faster than cocotbext-axi. Batching costs read latency: a
beat can be read up to B cycles late. Most of the shim time is the Xilinx VIP
itself, which cocotbext-axi's plain bus doesn't have, so this is a
comparison of the two setups and not a measure of the shims' own overhead.

The same benchmark runs on Verilator through
[test_axis_benchmark_verilator_runner.py](../test/axi_vip_shims/axi_stream_vip_shims/python/test_axis_benchmark_verilator_runner.py).
It takes the same `BENCH_BEATS` / `BENCH_REPEATS`, always builds without
waves, and writes its table to
`artifacts/test_axis_benchmark_verilator_runner/benchmark.txt`:

```sh
python3 test/axi_vip_shims/axi_stream_vip_shims/python/test_axis_benchmark_verilator_runner.py
BENCH=1 pytest test/axi_vip_shims/axi_stream_vip_shims/python/test_axis_benchmark_verilator_runner.py -s
```

Verilator is about 10× faster in wall time than Questa Starter, and the
pattern is the same. For 10,240 beats in 16-beat packets, cocotbext-axi takes
about 1.55 s, the shims about 2.0 s when reading each beat as it arrives, and
about 1.1 s with batched reads (about 1.4× faster than cocotbext-axi).
Because the model is so fast, a fixed part of each run (start-up, cocotb)
is a larger share: an idle run with no traffic already takes about 0.45 s.
These numbers include the protocol checkers (`--assert`). They cost about 10%
on every case, including cocotbext-axi's, because the checkers on the VIP
interfaces run on every clock either way.

`pending` is updated in the NBA region, so right after `try_get_transaction()`
it still shows the old count until the time step settles. Wait a simulator
step (or any delay) before relying on it after draining.
