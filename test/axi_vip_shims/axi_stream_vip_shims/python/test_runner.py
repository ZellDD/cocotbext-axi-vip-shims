"""cocotb runner for test_axis_vip.py on Questa / ModelSim.

Compiles the Xilinx VIP sources from the Vivado install, the VIP components you
generated in Vivado, the DPI shims and tb_top.sv into one library, builds
libaxis_dpi.so, and runs the cocotb tests with that same .so loaded by vsim
(-sv_lib) and by cocotb (AXIS_DPI_LIB).

    python3 test_runner.py            (or: pytest test_runner.py)

Environment:
    XILINX_VIVADO    Vivado install, e.g. /tools/xilinx/2025.2/Vivado
                     (set by Vivado's settings64.sh)
    AXIS_VIP_IP_DIRS ':'-separated dirs holding the generated VIP components
                     (axi4stream_vip_mst.sv + axi4stream_vip_mst_pkg.sv, and the
                     same for _slv), i.e. <project>.gen/.../ip/<name>/sim
    AXIS_DPI_LIB     use this prebuilt libaxis_dpi.so instead of building one
    WAVES=1          log all signals to vsim.wlf
    GUI=1            open the Questa GUI
    TESTCASE         run only these tests (comma-separated)
"""
import glob
import os
import subprocess
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

HERE = Path(__file__).resolve().parent
TEST_ROOT = HERE.parent                                    # .../axi_stream_vip_shims
REPO = HERE.parents[3]
SHIM = REPO / "src" / "axi_vip_shims" / "axi_stream_vip_shim" / "src"
BUILD_DIR = REPO / "build" / "sim_build_questa"

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
    if not path or not Path(path).is_dir():
        raise SystemExit("XILINX_VIVADO is not set (source Vivado's settings64.sh)")
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


def _generated_sources():
    """The VIP components generated in Vivado; packages before wrappers."""
    dirs = [d for d in os.environ.get("AXIS_VIP_IP_DIRS", "").split(":") if d]
    if not dirs:
        raise SystemExit(
            "AXIS_VIP_IP_DIRS is not set: point it at the sim/ dirs of the "
            "generated axi4stream_vip_mst and axi4stream_vip_slv IPs")
    files = [Path(f) for d in dirs for f in sorted(glob.glob(os.path.join(d, "*.sv")))]
    if not files:
        raise SystemExit(f"no .sv files in AXIS_VIP_IP_DIRS={dirs}")
    return sorted(files, key=lambda f: not f.stem.endswith("_pkg"))


def _dpi_lib():
    """Same file for vsim and cocotb, or the shims never meet cocotb."""
    prebuilt = os.environ.get("AXIS_DPI_LIB")
    if prebuilt:
        return Path(prebuilt).resolve()
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    lib = BUILD_DIR / "libaxis_dpi.so"
    subprocess.check_call(["cc", "-shared", "-fPIC", "-O2", "-Wall", "-Wextra",
                           "-o", str(lib), str(SHIM / "dpi_c" / "axis_dpi.c")])
    return lib


def test_axis_vip_questa():
    vivado = _vivado()
    lib = _dpi_lib()
    waves = os.environ.get("WAVES") == "1"

    sources = (_vip_sources(vivado) + _generated_sources() +
               [SHIM / "hdl" / "axis_vip_dpi.sv",
                TEST_ROOT / "hdl" / "tb_top.sv"])

    runner = get_runner("questa")
    runner.build(
        sources=sources,
        includes=[vivado / "data" / "xilinx_vip" / "include"],
        hdl_toplevel=TOPLEVEL,
        build_dir=BUILD_DIR,
        build_args=["-timescale", "1ns/1ps"],
        waves=waves,
        always=True,
    )

    testcase = os.environ.get("TESTCASE")
    runner.test(
        test_module="test_axis_vip",
        hdl_toplevel=TOPLEVEL,
        testcase=testcase.split(",") if testcase else None,
        test_dir=HERE,
        build_dir=BUILD_DIR,
        # -sv_lib takes the path without the .so extension
        test_args=["-sv_lib", str(lib.with_suffix("")),
                   "-voptargs=+acc", "-t", "1ps"],
        extra_env={
            "AXIS_DPI_LIB": str(lib),
            "PYTHONPATH": os.pathsep.join(
                [str(HERE), str(SHIM / "pyhon"), os.environ.get("PYTHONPATH", "")]),
        },
        waves=waves,
        gui=os.environ.get("GUI") == "1",
    )


if __name__ == "__main__":
    test_axis_vip_questa()
    sys.exit(0)
