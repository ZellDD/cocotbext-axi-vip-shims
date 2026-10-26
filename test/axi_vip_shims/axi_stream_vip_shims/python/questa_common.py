"""Shared Questa / ModelSim flow for the cocotb runners next to this file:

    test_axis_vip_questa_runner.py  functional tests  (../test_cases/test_axis_vip.py)
    test_axis_benchmark_runner.py   benchmark         (../test_cases/test_axis_benchmark.py)

Compiles the Xilinx VIP sources from the Vivado install, the generated VIP
cores (made by scripts/gen_vip.tcl on first run), the DPI shims and tb_top.sv into
one library, and runs cocotb tests with lib/libaxis_dpi.so loaded by vsim
(-sv_lib) and by cocotb (AXIS_DPI_LIB). With AXIS_VIP_SRC set, all VIP sources
come from there instead and Vivado is not needed.

Environment (both runners):
    AXIS_VIP_SRC     use your own VIP sources instead of Vivado:
                     - a directory holding the Xilinx VIP packages/interfaces
                       (xil_common_vip_pkg.sv, axi4stream_vip_pkg.sv, ...), the
                       axi4stream_vip_*_vl_rfs.sv top and the generated
                       axi4stream_vip_mst/_slv(_pkg).sv; searched recursively,
                       compile order worked out here, *.svh dirs are includes
                     - or a .f file list: one source per line, in compile
                       order, +incdir+<dir> lines, $VARS and paths relative to
                       the .f file allowed
    XILINX_VIVADO    Vivado install, e.g. /tools/xilinx/2025.2/Vivado
                     (default: found from `vivado` on PATH)
    AXIS_VIP_IP_DIRS ':'-separated sim/ dirs of your own generated VIP cores
                     (default: build/ip/*/sim, generated with scripts/gen_vip.tcl)
    AXIS_DPI_LIB     DPI library (default: lib/libaxis_dpi.so from CMake, or
                     lib/libaxis_dpi_cov.so for a run with coverage)
    EXT_SHIMS_COVERAGE=0  no coverage in the functional tests under pytest
                     (see coverage_common.py)

Artifacts: each runner writes into its own artifacts/<runner>/ (named after the
runner script, see artifact_dir): build.log, logs/<test>.log, wlf/<test>.wlf.
"""
import functools
import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

import coverage_common

HERE = Path(__file__).resolve().parent
TEST_ROOT = HERE.parent                                    # .../axi_stream_vip_shims
TEST_CASES = TEST_ROOT / "test_cases"                      # the cocotb test modules
REPO = HERE.parents[3]
SHIM = REPO / "src" / "axi_vip_shims" / "axi_stream_vip_shim" / "src"
BUILD_DIR = REPO / "build" / "sim_build_questa"
IP_DIR = REPO / "build" / "ip"
BUILD_LOG = BUILD_DIR / "build.log"                        # copied into each runner's dir
ARTIFACTS = TEST_ROOT / "artifacts"
GEN_VIP_TCL = REPO / "scripts" / "gen_vip.tcl"

TOPLEVEL = "tb_top"

# xilinx_vip_pkg.list.f order: checkers, packages, interfaces
XILINX_VIP_FILES = [
    "axi4stream_vip_axi4streampc.sv",
    "axi_vip_axi4pc.sv",
    "xil_common_vip_pkg.sv",
    "axi4stream_vip_pkg.sv",
    "axi_vip_pkg.sv",
    "axi4stream_vip_if.sv",
    "axi_vip_if.sv",
    "clk_vip_if.sv",
    "rst_vip_if.sv",
]


def _vivado():
    path = os.environ.get("XILINX_VIVADO")
    if not path:
        exe = shutil.which("vivado")
        if not exe:
            raise SystemExit("XILINX_VIVADO is not set and vivado is not on PATH")
        path = Path(exe).resolve().parents[1]              # <install>/bin/vivado
    if not Path(path).is_dir():
        raise SystemExit(f"XILINX_VIVADO={path} is not a directory")
    return Path(path)


def _vip_sources(vivado):
    """Xilinx VIP packages/interfaces + the axi4stream_vip IP top module."""
    hdl = vivado / "data" / "xilinx_vip" / "hdl"
    srcs = [hdl / f for f in XILINX_VIP_FILES]
    ip = sorted(glob.glob(str(vivado / "data" / "ip" / "xilinx" /
                              "axi4stream_vip_v1_1*" / "hdl" /
                              "axi4stream_vip_v1_1_vl_rfs.sv")))
    if not ip:
        raise SystemExit(f"axi4stream_vip IP sources not found under {vivado}")
    return srcs + [Path(ip[-1])]


def _generate_ip(vivado):
    """Run scripts/gen_vip.tcl in Vivado batch mode into build/ip, again whenever
    the script is newer than its output (i.e. the interface changed)."""
    outs = [IP_DIR / n / "sim" / f"{n}.sv"
            for n in ("axi4stream_vip_mst", "axi4stream_vip_slv")]
    if all(f.is_file() and f.stat().st_mtime >= GEN_VIP_TCL.stat().st_mtime
           for f in outs):
        return
    IP_DIR.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [str(vivado / "bin" / "vivado"), "-mode", "batch", "-nojournal",
         "-nolog", "-notrace", "-source", str(GEN_VIP_TCL),
         "-tclargs", str(IP_DIR)], cwd=IP_DIR.parent)


def _generated_sources(vivado):
    """The generated VIP cores; packages before wrappers.

    *_stub.sv is the synthesis black box (same module name), and the IP's own
    hdl/ copy of the VIP top duplicates the one from the install: both skipped.
    """
    dirs = [d for d in os.environ.get("AXIS_VIP_IP_DIRS", "").split(":") if d]
    if not dirs:
        _generate_ip(vivado)
        dirs = sorted(glob.glob(str(IP_DIR / "*" / "sim")))
    files = [Path(f) for d in dirs for f in sorted(glob.glob(os.path.join(d, "*.sv")))
             if not f.endswith("_stub.sv")]
    if not files:
        raise SystemExit(f"no .sv files in AXIS_VIP_IP_DIRS={dirs}")
    return sorted(files, key=lambda f: not f.stem.endswith("_pkg"))


def _user_vip_sources(path):
    """AXIS_VIP_SRC: (sources, include dirs) from a directory or a .f list."""
    path = Path(path).expanduser().resolve()
    if path.is_file():
        srcs, incs = [], []
        for line in path.read_text().splitlines():
            line = os.path.expandvars(line.split("//")[0].split("#")[0]).strip()
            if not line:
                continue
            if line.startswith("+incdir+"):
                incs += [path.parent / d for d in line[len("+incdir+"):].split("+") if d]
            else:
                srcs.append(path.parent / line)          # absolute stays absolute
        missing = [str(f) for f in srcs + incs if not f.exists()]
        if missing:
            raise SystemExit(f"AXIS_VIP_SRC={path}: not found:\n  " + "\n  ".join(missing))
        return srcs, incs

    if not path.is_dir():
        raise SystemExit(f"AXIS_VIP_SRC={path} is neither a directory nor a file list")
    # One file per name: the IP's hdl/ copies duplicate the VIP top, and the
    # *_stub.sv synthesis black boxes reuse the wrapper's module name.
    files = {}
    for f in sorted(path.rglob("*.sv")):
        if not f.name.endswith("_stub.sv"):
            files.setdefault(f.name, f)
    need = ["xil_common_vip_pkg.sv", "axi4stream_vip_pkg.sv", "axi4stream_vip_if.sv",
            "axi4stream_vip_mst.sv", "axi4stream_vip_slv.sv"]
    missing = [n for n in need if n not in files]
    if not any(n.endswith("_vl_rfs.sv") for n in files):
        missing.append("axi4stream_vip_*_vl_rfs.sv")
    if missing:
        raise SystemExit(f"AXIS_VIP_SRC={path} is missing: {', '.join(missing)}")

    def rank(f):
        if f.name in XILINX_VIP_FILES:                  # VIP packages, in .f order
            return (0, XILINX_VIP_FILES.index(f.name), f.name)
        if f.name.endswith("_vl_rfs.sv"):               # axi4stream_vip top
            return (1, 0, f.name)
        if f.stem.endswith("_pkg"):                     # generated core packages
            return (2, 0, f.name)
        return (3, 0, f.name)                           # generated wrappers, rest
    incs = sorted({f.parent for f in path.rglob("*.svh")})
    return sorted(files.values(), key=rank), incs


def _dpi_lib(coverage=False):
    """Same file for vsim and cocotb, or the shims never meet cocotb. With
    coverage, the gcov-instrumented build of the same code."""
    default = coverage_common.COV_LIB if coverage else REPO / "lib" / "libaxis_dpi.so"
    lib = Path(os.environ.get("AXIS_DPI_LIB", default))
    if not lib.is_file():
        raise SystemExit(f"{lib} not found -- build it with "
                         "'cmake -B build && cmake --build build'")
    return lib.resolve()


def artifact_dir(runner_file):
    """artifacts/<runner>/ for the runner script runner_file (pass __file__)."""
    return ARTIFACTS / Path(runner_file).stem


def copy_build_log(build_log, out):
    """Put the build log of this process's build into out/build.log."""
    out.mkdir(parents=True, exist_ok=True)
    if build_log.is_file():
        shutil.copyfile(build_log, out / "build.log")


def cocotb_tests(module):
    """Names of the @cocotb.test()s in test_cases/<module>.py, in file order."""
    src = (TEST_CASES / f"{module}.py").read_text()
    return re.findall(r"@cocotb\.test\([^)]*\)\s*\n\s*async def (\w+)", src)


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
    sources = vip + [SHIM / "hdl" / "axis_vip_dpi.sv", TEST_ROOT / "hdl" / "tb_top.sv"]

    runner = get_runner("questa")
    runner.build(
        sources=sources,
        includes=includes,
        # VIP falls back to $urandom instead of randomize()/randcase, which
        # need a Questa SV-verification licence (the Starter edition has none)
        defines={"XIL_DO_NOT_USE_ADV_RANDOMIZATION": 1},
        hdl_toplevel=TOPLEVEL,
        build_dir=BUILD_DIR,
        build_args=["-timescale", "1ns/1ps"],
        always=True,
        log_file=BUILD_LOG,
    )

    # The runner hands vsim its own sys.path as PYTHONPATH (extra_env's is
    # overwritten), so the test modules and axis_vip_dpi must be on it.
    for p in (str(SHIM / "python"), str(TEST_CASES)):
        if p not in sys.path:
            sys.path.insert(0, p)
    return runner


def run_questa(testcase, module, out, waves=None, env=None, coverage=None):
    """Run one cocotb test (or a list, in one vsim). out is the runner's
    artifact_dir(): the log goes to out/logs/<name>.log, the waves to
    out/wlf/<name>.wlf, the build log to out/build.log. env is added to the
    test's environment. coverage: collect it (default: under pytest, see
    coverage_common.py). Returns the results.xml path."""
    runner = build()
    copy_build_log(BUILD_LOG, out)
    log_dir, wave_dir = out / "logs", out / "wlf"
    if coverage is None:
        coverage = coverage_common.enabled()
    lib = _dpi_lib(coverage)
    names = [testcase] if isinstance(testcase, str) else list(testcase)
    name = names[0] if len(names) == 1 else "tests"
    if waves is None:
        waves = os.environ.get("WAVES", "1") != "0"

    # -sv_lib takes the path without the .so extension.
    # -dpioutoftheblue: cocotb calls the shims' DPI exports from VPI
    # callbacks, i.e. not from inside an SV import call chain.
    args = ["-sv_lib", str(lib.with_suffix("")), "-dpioutoftheblue", "1",
            "-t", "1ps"]
    voptargs, pre_cmd = "+acc", None
    if coverage:
        cover, cov_args, pre_cmd = coverage_common.questa_args(name)
        voptargs += " " + cover
        args += cov_args
        env = {**coverage_common.cocotb_env(), **(env or {})}
    args.append(f"-voptargs={voptargs}")
    if waves:
        wave_dir.mkdir(parents=True, exist_ok=True)
        args += ["-wlf", str(wave_dir / f"{name}.wlf")]
    log_dir.mkdir(parents=True, exist_ok=True)

    return runner.test(
        test_module=module,
        hdl_toplevel=TOPLEVEL,
        testcase=names,
        build_dir=BUILD_DIR,
        test_args=args,
        # BENCH_NAME: the benchmark names its result file after the test
        extra_env={"AXIS_DPI_LIB": str(lib), "BENCH_NAME": name, **(env or {})},
        results_xml=str(BUILD_DIR / f"{name}.results.xml"),   # pytest wants absolute
        log_file=log_dir / f"{name}.log",
        pre_cmd=pre_cmd,
        waves=waves,
        gui=os.environ.get("GUI") == "1",
    )
