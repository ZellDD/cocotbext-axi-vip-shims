"""Build and run tests/test_byteswap.py on Verilator (default) or Questa.

    pytest                                  (from examples/axis_byteswap/)
    python3 tests/test_byteswap_runner.py   same, without pytest (and coverage)

Everything this project takes from cocotbext-axi-vip-shims is referenced
from AXI_VIP_SHIMS_DIR:

    src/axi_vip_shims/axi_stream_vip_shim/
      CMakeLists.txt            -> libaxis_dpi.so, via ../CMakeLists.txt
      src/hdl/axis_vip_dpi.sv   -> compiled with the bench
      src/python/axis_vip_dpi.py -> imported by the cocotb tests

Environment:
    AXI_VIP_SHIMS_DIR  the shims checkout (default: this repo, two levels up;
                       in your project typically a git submodule)
    XILINX_VIVADO      Vivado install (default: found from `vivado` on PATH),
                       for the Xilinx VIP sources and to generate the cores
    SIM                verilator (default) or questa
    WAVES=1            record waves into build/sim_<SIM>/
    DUT_COVERAGE=0     no code coverage under pytest (default: on, for the DUT
                       in rtl/ only, report in build/sim_<SIM>/coverage/;
                       never when run as a script)
    TESTCASE           run only these cocotb tests (comma-separated)

build/ holds everything generated: build/lib/libaxis_dpi.so, the VIP cores in
build/ip/, the simulator build in build/sim_<SIM>/.
"""
import functools
import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from cocotb_tools.runner import get_runner

PROJECT = Path(__file__).resolve().parents[1]
TESTS = PROJECT / "tests"
SHIMS = Path(os.environ.get("AXI_VIP_SHIMS_DIR", PROJECT.parents[1])).resolve()
SHIM_SRC = SHIMS / "src" / "axi_vip_shims" / "axi_stream_vip_shim" / "src"

SIM = os.environ.get("SIM", "verilator")
BUILD = PROJECT / "build"
LIB = BUILD / "lib" / "libaxis_dpi.so"
IP_DIR = BUILD / "ip"
SIM_BUILD = BUILD / f"sim_{SIM}"
GEN_VIP_TCL = PROJECT / "tb" / "gen_vip.tcl"
COV_DIR = SIM_BUILD / "coverage"
COV_SUMMARY = COV_DIR / "summary.txt"        # printed by ../conftest.py
# what the simulator writes at the end of the run
COV_DATA = (SIM_BUILD / "coverage.dat" if SIM == "verilator"
            else COV_DIR / "coverage.ucdb")
DUT_SOURCES = [PROJECT / "rtl" / "axis_byteswap.sv"]   # what coverage measures

TOPLEVEL = "tb_byteswap"
MODULE = "test_byteswap"
VIP_CORES = ("ex_src_vip", "ex_sink_vip")          # module names in gen_vip.tcl

DEFINES = {
    # VIP uses $urandom instead of randomize(): no SV-verification licence
    # needed on Questa, and Verilator's randomize() support is limited
    "XIL_DO_NOT_USE_ADV_RANDOMIZATION": 1,
    # where the shims find the VIP agents: our component and instance names
    "AXIS_MST_VIP_PKG": "ex_src_vip_pkg",
    "AXIS_MST_AGENT_T": "ex_src_vip_mst_t",
    "AXIS_MST_VIF": f"{TOPLEVEL}.u_src.inst.IF",
    "AXIS_SLV_VIP_PKG": "ex_sink_vip_pkg",
    "AXIS_SLV_AGENT_T": "ex_sink_vip_slv_t",
    "AXIS_SLV_VIF": f"{TOPLEVEL}.u_sink.inst.IF",
}

# The part of the Xilinx VIP library an AXI4-Stream bench needs, in order
XILINX_VIP_FILES = ["axi4stream_vip_axi4streampc.sv", "xil_common_vip_pkg.sv",
                    "axi4stream_vip_pkg.sv", "axi4stream_vip_if.sv"]


# ------------------------------------------------------------- VIP sources
def vivado():
    path = os.environ.get("XILINX_VIVADO")
    if not path and shutil.which("vivado"):
        path = Path(shutil.which("vivado")).resolve().parents[1]
    return Path(path) if path else None


def generate_vip(viv):
    """tb/gen_vip.tcl into build/ip, again whenever the script changed."""
    outs = [IP_DIR / n / "sim" / f"{n}.sv" for n in VIP_CORES]
    if all(f.is_file() and f.stat().st_mtime >= GEN_VIP_TCL.stat().st_mtime
           for f in outs):
        return
    BUILD.mkdir(exist_ok=True)
    subprocess.check_call(
        [str(viv / "bin" / "vivado"), "-mode", "batch", "-nojournal", "-nolog",
         "-notrace", "-source", str(GEN_VIP_TCL), "-tclargs", str(IP_DIR)],
        cwd=BUILD)


def vip_sources(viv):
    """(sources, include dirs): Xilinx VIP library, VIP top, our two cores."""
    hdl = viv / "data" / "xilinx_vip" / "hdl"
    top = sorted(glob.glob(str(viv / "data" / "ip" / "xilinx" / "axi4stream_vip_v1_1*"
                               / "hdl" / "axi4stream_vip_v1_1_vl_rfs.sv")))
    if not top:
        raise SystemExit(f"axi4stream_vip IP sources not found under {viv}")
    generate_vip(viv)
    cores = [IP_DIR / n / "sim" / f"{n}{suffix}.sv"
             for n in VIP_CORES for suffix in ("_pkg", "")]    # package first
    return ([hdl / f for f in XILINX_VIP_FILES] + [Path(top[-1])] + cores,
            [viv / "data" / "xilinx_vip" / "include"])


def patch_checker_for_verilator(src):
    """Verilator crashes on the protocol checker's tri0/tri1 input ports; a
    copy with them as plain wires behaves the same (the VIP drives them all).
    """
    text = re.sub(r"^(\s*input\s+)tri[01]\b", r"\1wire", src.read_text(),
                  flags=re.MULTILINE)
    dst = SIM_BUILD / "patched_vip" / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text)
    return dst


# ---------------------------------------------------------------- coverage
def coverage():
    """Under pytest (PYTEST_CURRENT_TEST is set while a test runs), unless
    DUT_COVERAGE=0."""
    return ("PYTEST_CURRENT_TEST" in os.environ
            and os.environ.get("DUT_COVERAGE", "1") != "0")


def verilator_coverage_config(sources, includes):
    """Verilator config file that turns coverage off for everything but the
    DUT: the VIP, the shims and the bench aren't what we're measuring, and
    leaving them out keeps the model fast."""
    vlt = SIM_BUILD / "coverage.vlt"
    vlt.parent.mkdir(parents=True, exist_ok=True)
    # resolved: Verilator records the real path (Vivado installs use symlinks)
    off = [str(f.resolve()) for f in sources if f not in DUT_SOURCES]
    off += [f"{d.resolve()}/*" for d in includes]
    vlt.write_text("`verilator_config\n" +
                   "".join(f'coverage_off -file "{f}"\n' for f in off))
    return vlt


def coverage_report():
    """Report on the run's COV_DATA into COV_DIR; the totals go to
    COV_SUMMARY."""
    if not COV_DATA.is_file():
        return                                # the run didn't get that far
    if SIM == "verilator":
        cmds = [["verilator_coverage", "--annotate", str(COV_DIR / "annotated"),
                 "--annotate-all", "--annotate-min", "1",
                 "--write-info", str(COV_DIR / "coverage.info"),
                 str(COV_DATA)]]
    else:
        # code coverage only: the VIP's protocol-checker assertions are
        # counted too otherwise
        report = ["vcover", "report", "-code", "bcesft"]
        cmds = [report + ["-html", "-output", str(COV_DIR / "html"), str(COV_DATA)],
                report + ["-summary", str(COV_DATA)]]
    out = ""
    for cmd in cmds:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode:
            raise RuntimeError(f"{cmd[0]} failed:\n{res.stdout}{res.stderr}")
        out = res.stdout                      # the last command's is the summary
    banner = ("Questa", "Start time", "vcover report", "End time", "Errors:")
    out = "\n".join(l for l in out.strip().splitlines() if not l.startswith(banner))
    COV_SUMMARY.write_text(out.strip() + f"\nDetails: {COV_DIR}\n")


# ------------------------------------------------------------------- build
@functools.cache
def build():
    """DPI library (CMake) + simulator build, once per process."""
    viv = vivado()
    if viv is None:
        raise SystemExit("XILINX_VIVADO is not set and vivado is not on PATH")

    subprocess.check_call(["cmake", "-S", str(PROJECT), "-B", str(BUILD),
                           f"-DAXI_VIP_SHIMS_DIR={SHIMS}"])
    subprocess.check_call(["cmake", "--build", str(BUILD)])

    vip, includes = vip_sources(viv)
    sources = vip + [SHIM_SRC / "hdl" / "axis_vip_dpi.sv", *DUT_SOURCES,
                     PROJECT / "tb" / "tb_byteswap.sv"]
    if SIM == "verilator":
        sources = [patch_checker_for_verilator(f) if f.name == XILINX_VIP_FILES[0]
                   else f for f in sources]
        build_args = [
            "--timing",                     # the VIP drivers use # and @ delays
            "--assert",                     # else the protocol checker is ignored
            "-Wno-fatal", "-Wno-lint", "-Wno-style",
            # Verilator has no -sv_lib: the DPI library is linked into the model
            str(LIB), "-LDFLAGS", f"-Wl,-rpath,{LIB.parent}",
        ]
        if coverage():
            build_args += ["--coverage",
                           str(verilator_coverage_config(sources, includes))]
    else:
        build_args = ["-timescale", "1ns/1ps"]

    runner = get_runner(SIM)
    runner.build(
        sources=sources,
        includes=includes,
        defines=DEFINES,
        hdl_toplevel=TOPLEVEL,
        build_dir=SIM_BUILD,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=waves(),
        always=True,
    )
    return runner


def waves():
    return os.environ.get("WAVES") == "1"


def run(testcase=None):
    """Run the cocotb tests (all, or the comma-separated TESTCASE) in one
    simulation. Returns the results.xml path."""
    runner = build()
    # The runner hands the simulator its own sys.path as PYTHONPATH, so the
    # cocotb test module and axis_vip_dpi.py must be on it.
    for p in (str(SHIM_SRC / "python"), str(TESTS)):
        if p not in sys.path:
            sys.path.insert(0, p)

    test_args, pre_cmd = [], None
    if SIM == "questa":
        # -sv_lib takes the path without .so; -dpioutoftheblue: cocotb calls
        # the shims' DPI exports from VPI callbacks; +acc: cocotb sees the shims
        test_args = ["-sv_lib", str(LIB.with_suffix("")), "-dpioutoftheblue", "1",
                     "-t", "1ps"]
        voptargs = "+acc"
        if coverage():
            # statement, branch, condition, expression, FSM, toggle; DUT only
            voptargs += " +cover=bcesft+axis_byteswap"
            test_args.append("-coverage")
            pre_cmd = [f"coverage save -onexit {COV_DATA}"]
        test_args.append(f"-voptargs={voptargs}")
    if coverage():
        # no report from an older run
        COV_DATA.unlink(missing_ok=True)
        shutil.rmtree(COV_DIR, ignore_errors=True)
        COV_DIR.mkdir(parents=True)
    try:
        return runner.test(
            test_module=MODULE,
            hdl_toplevel=TOPLEVEL,
            testcase=testcase.split(",") if testcase else None,
            build_dir=SIM_BUILD,
            test_args=test_args,
            # cocotb must open the same file the simulator loaded
            extra_env={"AXIS_DPI_LIB": str(LIB)},
            pre_cmd=pre_cmd,
            waves=waves(),
        )
    finally:
        if coverage():
            coverage_report()


@pytest.mark.skipif(vivado() is None, reason="no Vivado (XILINX_VIVADO / PATH)")
@pytest.mark.skipif(shutil.which({"verilator": "verilator", "questa": "vsim"}
                                 .get(SIM, SIM)) is None, reason=f"{SIM} not on PATH")
def test_byteswap():
    run(os.environ.get("TESTCASE"))


if __name__ == "__main__":
    from cocotb_tools.check_results import get_results

    n_tests, n_failed = get_results(run(os.environ.get("TESTCASE")))
    print(f"{n_tests - n_failed}/{n_tests} cocotb tests passed")
    sys.exit(1 if n_failed or not n_tests else 0)
