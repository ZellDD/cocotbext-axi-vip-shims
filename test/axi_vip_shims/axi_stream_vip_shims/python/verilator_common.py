"""Shared Verilator flow for test_axis_vip_verilator_runner.py, the Verilator
counterpart of questa_common.py.

Same sources, include dirs, defines and timescale as the Questa flow (the VIP
source discovery is imported from there), built into one Verilator model.
Verilator resolves DPI at link time, so lib/libaxis_dpi.so is linked into the
model (with an rpath) instead of loaded with -sv_lib; cocotb then opens the
same file through AXIS_DPI_LIB and gets the already-loaded copy.


With coverage (the functional tests, see coverage_common.py), the model is
instrumented and linked against lib/libaxis_dpi_cov.so, so it is built in its
own directory, build/sim_build_verilator_cov; the benchmark's model in
build/sim_build_verilator stays uninstrumented.

Environment: AXIS_VIP_SRC, XILINX_VIVADO, AXIS_VIP_IP_DIRS, AXIS_DPI_LIB,
EXT_SHIMS_COVERAGE as in questa_common.py, and
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

import coverage_common
from questa_common import (REPO, SHIM, TEST_CASES, TEST_ROOT, TOPLEVEL, _dpi_lib,
                           _generated_sources, _user_vip_sources, _vip_sources,
                           _vivado, artifact_dir, cocotb_tests,  # noqa: F401  (re-exported)
                           copy_build_log)

def _build_dir(coverage):
    return REPO / "build" / ("sim_build_verilator_cov" if coverage else "sim_build_verilator")


# Compiled by Questa but not used by the AXI4-Stream bench; Verilator also
# rejects axi_vip_pkg.sv (%b with a string argument)
UNUSED_VIP_FILES = {"axi_vip_axi4pc.sv", "axi_vip_pkg.sv", "axi_vip_if.sv",
                    "clk_vip_if.sv", "rst_vip_if.sv"}
# The protocol checker, built from a patched copy (see _patch_checker)
CHECKER = "axi4stream_vip_axi4streampc.sv"


def _patch_checker(src, build_dir):
    """Copy of the Xilinx protocol checker that Verilator can build.

    Its tri0/tri1 input ports, on an interface nested in the parameterized
    axi4stream_vip_if and fed from tri-stated nets, crash Verilator (internal
    fault in V3LinkDot). They become plain wires: the pull-up/-down only
    matters for an undriven input, and the VIP top drives all of them. The
    checks themselves are unchanged."""
    text, n = re.subn(r"^(\s*input\s+)tri[01]\b", r"\1wire", src.read_text(),
                      flags=re.MULTILINE)
    dst = build_dir / "patched_vip" / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    # note at the end, so line numbers in checker messages match the original
    dst.write_text(text + f"\n// {src} with {n} tri0/tri1 ports as wire, "
                   "made by verilator_common.py\n")
    return dst


def _waves():
    return os.environ.get("WAVES", "1") != "0"


@functools.cache
def build(coverage=False):
    """Compile everything once per Python process (and coverage setting);
    returns the runner."""
    build_dir = _build_dir(coverage)
    user_src = os.environ.get("AXIS_VIP_SRC")
    if user_src:
        vip, includes = _user_vip_sources(user_src)
    else:
        vivado = _vivado()
        vip = _vip_sources(vivado) + _generated_sources(vivado)
        includes = [vivado / "data" / "xilinx_vip" / "include"]
    vip = [_patch_checker(f, build_dir) if f.name == CHECKER else f
           for f in vip if f.name not in UNUSED_VIP_FILES]
    sources = vip + [SHIM / "hdl" / "axis_vip_dpi.sv", TEST_ROOT / "hdl" / "tb_top.sv"]
    lib = _dpi_lib(coverage)
    cov_args = (coverage_common.verilator_args(build_dir, sources, includes)
                if coverage else [])

    runner = get_runner("verilator")
    runner.build(
        sources=sources,
        includes=includes,
        # same as Questa; here it also keeps the VIP off Verilator's limited
        # randomize() support
        defines={"XIL_DO_NOT_USE_ADV_RANDOMIZATION": 1},
        hdl_toplevel=TOPLEVEL,
        build_dir=build_dir,
        build_args=[
            "--timing",                    # the VIP's drivers use # and @ delays
            "--assert",                    # else the protocol checker's SVA is ignored
            "-Wno-fatal", "-Wno-lint", "-Wno-style",
            # the DPI imports are resolved when the model is linked
            str(lib), "-LDFLAGS", f"-Wl,-rpath,{lib.parent}",
            *cov_args,
        ],
        timescale=("1ns", "1ps"),
        waves=_waves(),
        always=True,
        log_file=build_dir / "build.log",
    )

    # The runner hands the model its own sys.path as PYTHONPATH (extra_env's
    # is overwritten), so the test modules and axis_vip_dpi must be on it.
    for p in (str(SHIM / "python"), str(TEST_CASES)):
        if p not in sys.path:
            sys.path.insert(0, p)
    return runner


def run_verilator(testcase, module, out, waves=None, env=None, coverage=None):
    """Run one cocotb test (or a list, in one model run). out is the runner's
    artifact_dir(): the log goes to out/logs/<name>.log, the waves to
    out/vcd/<name>.vcd, the build log to out/build.log. env is added to the
    test's environment. coverage: collect it (default: under pytest, see
    coverage_common.py). Returns the results.xml path."""
    if coverage is None:
        coverage = coverage_common.enabled()
    runner = build(coverage)
    build_dir = _build_dir(coverage)
    copy_build_log(build_dir / "build.log", out)
    log_dir, wave_dir = out / "logs", out / "vcd"
    lib = _dpi_lib(coverage)
    if coverage:
        env = {**coverage_common.cocotb_env(), **(env or {})}
    names = [testcase] if isinstance(testcase, str) else list(testcase)
    name = names[0] if len(names) == 1 else "tests"
    if waves is None:
        waves = _waves()
    waves = waves and _waves()                 # can't trace a model built without
    log_dir.mkdir(parents=True, exist_ok=True)

    # each run in its own dir: the model writes dump.vcd into its cwd
    test_dir = build_dir / "runs" / name
    test_dir.mkdir(parents=True, exist_ok=True)
    try:
        return runner.test(
            test_module=module,
            hdl_toplevel=TOPLEVEL,
            testcase=names,
            build_dir=build_dir,
            test_dir=test_dir,
            # BENCH_NAME: the benchmark names its result file after the test
            extra_env={"AXIS_DPI_LIB": str(lib), "BENCH_NAME": name, **(env or {})},
            results_xml=str(build_dir / f"{name}.results.xml"),   # pytest wants absolute
            log_file=log_dir / f"{name}.log",
            plusargs=coverage_common.verilator_plusargs(name) if coverage else [],
            waves=waves,
            gui=os.environ.get("GUI") == "1",
        )
    finally:
        vcd = test_dir / "dump.vcd"
        if waves and vcd.is_file():
            wave_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(vcd, wave_dir / f"{name}.vcd")
