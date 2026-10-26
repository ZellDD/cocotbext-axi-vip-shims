# Verilator notes

[← README](../README.md)

Tested with Verilator 5.052. The Verilator flow
([verilator_common.py](../test/axi_vip_shims/axi_stream_vip_shims/python/verilator_common.py))
uses the same sources, include dirs, defines and timescale as the Questa flow,
with these differences:

- **The DPI library is linked, not loaded.** Verilator resolves DPI imports
  when the model is linked, so `lib/libaxis_dpi.so` goes on the Verilator
  command line, with an rpath to `lib/`. cocotb then opens the same file
  through `AXIS_DPI_LIB` and gets the copy that is already loaded, so both
  sides still share one instance table. Rebuild the model after rebuilding
  the library.
- **The protocol checker is patched and built with `--assert`.** Xilinx's
  `axi4stream_vip_axi4streampc.sv` crashes Verilator as shipped: its
  `tri0`/`tri1` input ports, on an interface nested in the parameterized
  `axi4stream_vip_if` and fed from tri-stated nets, cause an internal fault.
  At build time the flow writes a copy to
  `build/sim_build_verilator/patched_vip/` with those 6 ports declared as
  `wire` and compiles that copy instead. The pull-up/pull-down only matters for
  an undriven input, and the VIP top drives all of them. Every check is left as
  it is, and line numbers in checker messages match the original file.
  Verilator ignores assertions unless `--assert` is given, so the flow sets it.
  A violation stops the run with the checker's message, for example:

  ```
  %Fatal: axi4stream_vip_axi4streampc.sv:311: Assertion failed in tb_top.slv_vip.inst.IF.PC...:
  AXI4STREAM_ERRM_TKEEP_TSTRB. If TKEEP is deasserted then TSTRB must also be deasserted ...
  ```

  One limit: Verilator is 2-state, so the `*_X` checks (built on
  `$isunknown`) can never fail there. The stability, reset, TKEEP/TSTRB,
  tie-off and max-wait checks all work.
- **Unused VIP files are left out.** `axi_vip_pkg.sv`, `axi_vip_if.sv`,
  `axi_vip_axi4pc.sv`, `clk_vip_if.sv` and `rst_vip_if.sv` are compiled by
  Questa but not needed for AXI4-Stream, and Verilator rejects
  `axi_vip_pkg.sv`.
- **Build flags:** `--timing` (the VIP drivers use `#` and `@` delays),
  `--assert` (see above) and `-Wno-fatal -Wno-lint -Wno-style`. The build log shows `MULTIDRIVEN`
  warnings from `axi4stream_vip_if.sv` (clocking block and continuous
  assignment on the same signals); they come from the Xilinx code and don't
  stop the build.
- `+define+XIL_DO_NOT_USE_ADV_RANDOMIZATION` is set as on Questa. Here it
  also keeps the VIP off Verilator's limited `randomize()` support.

Verilator 5.032 can't build the bench: it rejects VIP methods called without
parentheses (`this.get_data_width`) and crashes on the VIP's interface reached
by hierarchical path (`tb_top.mst_vip.inst.IF`).
