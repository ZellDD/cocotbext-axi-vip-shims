"""Shared Verilator flow for test_axis_vip_verilator_runner.py, the Verilator
counterpart of questa_common.py.

Same sources, include dirs, defines and timescale as the Questa flow (the VIP
source discovery is imported from there), built into one Verilator model.
Verilator resolves DPI at link time, so lib/libaxis_dpi.so is linked into the
model (with an rpath) instead of loaded with -sv_lib; cocotb then opens the
same file through AXIS_DPI_LIB and gets the already-loaded copy.

The Xilinx protocol checker is compiled with --assert, from a patched copy
(see _patch_checker). Known not to build with Verilator 5.032, see "Verilator
notes" in README.md.

Environment: AXIS_VIP_SRC, XILINX_VIVADO, AXIS_VIP_IP_DIRS, AXIS_DPI_LIB as in
questa_common.py, and
    WAVES=0          build without --trace (waves are a build-time option in
                     Verilator; default: on, artifacts/<runner>/vcd/<test>.vcd)

Artifacts: artifacts/<runner>/ as in questa_common.py, with vcd/<test>.vcd
instead of wlf/.
"""
import functools
import os
import re
import shutil
import sys

from cocotb_tools.runner import get_runner

from questa_common import (REPO, SHIM, TEST_CASES, TEST_ROOT, TOPLEVEL, _dpi_lib,
                           _generated_sources, _user_vip_sources, _vip_sources,
                           _vivado, artifact_dir, cocotb_tests,  # noqa: F401  (re-exported)
                           copy_build_log)

BUILD_DIR = REPO / "build" / "sim_build_verilator"
BUILD_LOG = BUILD_DIR / "build.log"                        # copied into each runner's dir


# Compiled by Questa but not used by the AXI4-Stream bench; Verilator also
# rejects axi_vip_pkg.sv (%b with a string argument)
UNUSED_VIP_FILES = {"axi_vip_axi4pc.sv", "axi_vip_pkg.sv", "axi_vip_if.sv",
                    "clk_vip_if.sv", "rst_vip_if.sv"}
# The protocol checker, built from a patched copy (see _patch_checker)
CHECKER = "axi4stream_vip_axi4streampc.sv"
PATCHED_DIR = BUILD_DIR / "patched_vip"


def _patch_checker(src):
    """Copy of the Xilinx protocol checker that Verilator can build.

    Its tri0/tri1 input ports, on an interface nested in the parameterized
    axi4stream_vip_if and fed from tri-stated nets, crash Verilator (internal
    fault in V3LinkDot). They become plain wires: the pull-up/-down only
    matters for an undriven input, and the VIP top drives all of them. The
    checks themselves are unchanged."""
    text, n = re.subn(r"^(\s*input\s+)tri[01]\b", r"\1wire", src.read_text(),
                      flags=re.MULTILINE)
    PATCHED_DIR.mkdir(parents=True, exist_ok=True)
    dst = PATCHED_DIR / src.name
    # note at the end, so line numbers in checker messages match the original
    dst.write_text(text + f"\n// {src} with {n} tri0/tri1 ports as wire, "
                   "made by verilator_common.py\n")
    return dst


def _waves():
    return os.environ.get("WAVES", "1") != "0"


@functools.cache
def build():
    """Compile everything once per Python process; returns the runner."""
    user_src = os.environ.get("AXIS_VIP_SRC")
    if user_src:
        vip, includes = _user_vip_sources(user_src)
    else:
        vivado = _vivado()
        vip = _vip_sources(vivado) + _generated_sources(vivado)
        includes = [vivado / "data" / "xilinx_vip" / "include"]
    vip = [_patch_checker(f) if f.name == CHECKER else f
           for f in vip if f.name not in UNUSED_VIP_FILES]
    sources = vip + [SHIM / "hdl" / "axis_vip_dpi.sv", TEST_ROOT / "hdl" / "tb_top.sv"]
    lib = _dpi_lib()

    runner = get_runner("verilator")
    runner.build(
        sources=sources,
        includes=includes,
        # same as Questa; here it also keeps the VIP off Verilator's limited
        # randomize() support
        defines={"XIL_DO_NOT_USE_ADV_RANDOMIZATION": 1},
        hdl_toplevel=TOPLEVEL,
        build_dir=BUILD_DIR,
        build_args=[
            "--timing",                    # the VIP's drivers use # and @ delays
            "--assert",                    # else the protocol checker's SVA is ignored
            "-Wno-fatal", "-Wno-lint", "-Wno-style",
            # the DPI imports are resolved when the model is linked
            str(lib), "-LDFLAGS", f"-Wl,-rpath,{lib.parent}",
        ],
        timescale=("1ns", "1ps"),
        waves=_waves(),
        always=True,
        log_file=BUILD_LOG,
    )

    # The runner hands the model its own sys.path as PYTHONPATH (extra_env's
    # is overwritten), so the test modules and axis_vip_dpi must be on it.
    for p in (str(SHIM / "pyhon"), str(TEST_CASES)):
        if p not in sys.path:
            sys.path.insert(0, p)
    return runner


def run_verilator(testcase, module, out, waves=None, env=None):
    """Run one cocotb test (or a list, in one model run). out is the runner's
    artifact_dir(): the log goes to out/logs/<name>.log, the waves to
    out/vcd/<name>.vcd, the build log to out/build.log. env is added to the
    test's environment. Returns the results.xml path."""
    runner = build()
    copy_build_log(BUILD_LOG, out)
    log_dir, wave_dir = out / "logs", out / "vcd"
    lib = _dpi_lib()
    names = [testcase] if isinstance(testcase, str) else list(testcase)
    name = names[0] if len(names) == 1 else "tests"
    if waves is None:
        waves = _waves()
    waves = waves and _waves()                 # can't trace a model built without
    log_dir.mkdir(parents=True, exist_ok=True)

    # each run in its own dir: the model writes dump.vcd into its cwd
    test_dir = BUILD_DIR / "runs" / name
    test_dir.mkdir(parents=True, exist_ok=True)
    try:
        return runner.test(
            test_module=module,
            hdl_toplevel=TOPLEVEL,
            testcase=names,
            build_dir=BUILD_DIR,
            test_dir=test_dir,
            # BENCH_NAME: the benchmark names its result file after the test
            extra_env={"AXIS_DPI_LIB": str(lib), "BENCH_NAME": name, **(env or {})},
            results_xml=str(BUILD_DIR / f"{name}.results.xml"),   # pytest wants absolute
            log_file=log_dir / f"{name}.log",
            waves=waves,
            gui=os.environ.get("GUI") == "1",
        )
    finally:
        vcd = test_dir / "dump.vcd"
        if waves and vcd.is_file():
            wave_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(vcd, wave_dir / f"{name}.vcd")
