"""Coverage of this repo's own code, everything except the Xilinx VIP:

    C       axis_dpi.cpp             gcov: lib/libaxis_dpi_test.so (test_c_side.py)
                                     and lib/libaxis_dpi_cov.so (simulation tests)
    SV      axis_vip_dpi.sv,         Verilator --coverage-line/-expr, or
            tb_top.sv                Questa +cover=bcse, per run into raw/<sim>/
    Python  axis_vip_dpi.py          in-process: pytest-cov (pytest.ini,
                                     .coveragerc); in the simulator: cocotb's
                                     own (COCOTB_USER_COVERAGE, see cocotb_env)

Collected only under pytest (`pytest -s` from the repo root), by the
functional runners; benchmarks never (instrumentation would skew the
timings), nor a runner started as a script. conftest.py calls reset() at the
start of the session and report() at the end, which merges the C and SV data
into build/coverage/{c,sv_verilator,sv_questa}/; pytest-cov reports Python.

No toggle coverage on the SV side: the shims pass 4096-bit DPI vectors, and
their unused upper bits would drown the number.

Environment:
    EXT_SHIMS_COVERAGE=0  collect nothing under pytest either (default: on).
                          Not COVERAGE: cocotb reads that one (deprecated)
                          as COCOTB_USER_COVERAGE.
"""
import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEST_ROOT = HERE.parent
REPO = HERE.parents[3]
SHIM = REPO / "src" / "axi_vip_shims" / "axi_stream_vip_shim" / "src"
BUILD = REPO / "build"
COV = BUILD / "coverage"
RAW = COV / "raw"                                          # one file per run
COV_LIB = REPO / "lib" / "libaxis_dpi_cov.so"
PY_DATA = BUILD / ".coverage"                              # = data_file in .coveragerc

# What is measured on the SV side; every other source is Xilinx VIP
SV_SOURCES = [SHIM / "hdl" / "axis_vip_dpi.sv", TEST_ROOT / "hdl" / "tb_top.sv"]
SV_UNITS = ["axis_dpi_pkg", "axis_vip_dpi_mst", "axis_vip_dpi_slv", "tb_top"]


def enabled():
    """Under pytest (PYTEST_CURRENT_TEST is set while a test runs), unless
    EXT_SHIMS_COVERAGE=0."""
    return ("PYTEST_CURRENT_TEST" in os.environ
            and os.environ.get("EXT_SHIMS_COVERAGE", "1") != "0")


def cocotb_env():
    """Environment for a run with coverage of axis_vip_dpi.py inside the
    simulator. cocotb starts it and saves it at its shutdown (Questa never
    runs Python's atexit, so coverage's own subprocess support doesn't save
    there). Parallel data files next to PY_DATA, which pytest-cov combines;
    absolute paths, since the simulator runs in another directory."""
    rc = COV / "cocotb.coveragerc"
    rc.parent.mkdir(parents=True, exist_ok=True)
    rc.write_text(f"[run]\nsource = {SHIM / 'python'}\ndata_file = {PY_DATA}\n"
                  "parallel = true\n")
    return {"COCOTB_USER_COVERAGE": "1", "COVERAGE_RCFILE": str(rc)}


# ------------------------------------------------------- simulator options
def verilator_args(build_dir, sources, includes):
    """Build args for a model with coverage of SV_SOURCES only: a config
    file turns it off for everything else."""
    vlt = build_dir / "coverage.vlt"
    vlt.parent.mkdir(parents=True, exist_ok=True)
    # resolved: Verilator records the real path (Vivado installs use symlinks)
    keep = {f.resolve() for f in SV_SOURCES}
    off = [str(f.resolve()) for f in sources if f.resolve() not in keep]
    off += [f"{Path(d).resolve()}/*" for d in includes]
    vlt.write_text("`verilator_config\n" +
                   "".join(f'coverage_off -file "{f}"\n' for f in off))
    return ["--coverage-line", "--coverage-expr", str(vlt)]


def verilator_plusargs(name):
    out = RAW / "verilator" / f"{name}.dat"
    out.parent.mkdir(parents=True, exist_ok=True)
    return [f"+verilator+coverage+file+{out}"]


def questa_args(name):
    """(voptargs, vsim args, pre_cmd) for one run with coverage of SV_UNITS."""
    out = RAW / "questa" / f"{name}.ucdb"
    out.parent.mkdir(parents=True, exist_ok=True)
    return ("+cover=bcse+" + "+".join(SV_UNITS), ["-coverage"],
            [f"coverage save -onexit {out}"])


# ---------------------------------------------------------------- reports
def _gcda_files():
    return glob.glob(str(BUILD / "src" / "**" / "*.gcda"), recursive=True)


def reset():
    """Clear the C and SV data (pytest-cov clears the Python data)."""
    for f in _gcda_files():
        os.remove(f)
    shutil.rmtree(RAW, ignore_errors=True)


def _run(cmd):
    res = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                         cwd=REPO)
    if res.returncode:
        raise RuntimeError(f"{cmd[0]} failed ({res.returncode}):\n"
                           f"{res.stdout}{res.stderr}")
    return res.stdout


def _c_report():
    out = COV / "c"
    out.mkdir(parents=True, exist_ok=True)
    text = _run([sys.executable, "-m", "gcovr", "--root", REPO, "--filter", "src/",
                 "--txt", "-", "--html-details", out / "index.html",
                 "--cobertura", out / "coverage.xml", BUILD])
    return text + f"HTML: {out / 'index.html'}\n"


def _verilator_report(dats):
    out = COV / "sv_verilator"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    merged = out / "coverage.dat"
    _run(["verilator_coverage", "--write", merged, *dats])
    text = _run(["verilator_coverage", "--annotate", out / "annotated",
                 "--annotate-all", "--annotate-min", "1",
                 "--write-info", out / "coverage.info", merged])
    return text.strip() + f"\nDetails: {out}\n"


def _questa_report(ucdbs):
    out = COV / "sv_questa"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    merged = out / "coverage.ucdb"
    _run(["vcover", "merge", "-out", merged, *ucdbs])
    report = ["vcover", "report", "-code", "bcse"]
    _run(report + ["-html", "-output", out / "html", merged])
    text = _run(report + ["-summary", merged])
    banner = ("Questa", "Start time", "vcover report", "End time", "Errors:")
    text = "\n".join(l for l in text.strip().splitlines() if not l.startswith(banner))
    return text.strip() + f"\nDetails: {out}\n"


def report():
    """[(title, text)] for the C and SV data present. Call after the runs;
    an in-process test library must have flushed its counters (see
    conftest.py)."""
    sections = []
    if _gcda_files():
        sections.append(("C coverage (gcovr)", _c_report()))
    dats = sorted((RAW / "verilator").glob("*.dat"))
    if dats:
        sections.append((f"SV coverage, Verilator ({len(dats)} runs)",
                         _verilator_report(dats)))
    ucdbs = sorted((RAW / "questa").glob("*.ucdb"))
    if ucdbs:
        sections.append((f"SV coverage, Questa ({len(ucdbs)} runs)",
                         _questa_report(ucdbs)))
    return sections
