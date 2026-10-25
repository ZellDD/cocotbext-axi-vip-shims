import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(
    HERE, "src", "axi_vip_shims", "axi_stream_vip_shim", "src", "pyhon"))

# cocotb test modules: run inside the simulator, started by the runners in
# test/**/python/ (test_axis_vip_questa_runner.py, test_axis_benchmark_runner.py)
collect_ignore_glob = ["test/**/test_cases/*",
                       # standalone projects: run pytest from inside them
                       "examples/*"]


# ------------------------------------------------------------- C coverage
# Python coverage comes from pytest-cov (pytest.ini, .coveragerc). For
# axis_dpi.cpp, lib/libaxis_dpi_test.so is built with --coverage
# (AXI_VIP_SHIMS_TEST_COVERAGE): its counters are reset here at the start and
# reported with gcovr at the end, when test_c_side.py has loaded it.
BUILD = os.path.join(HERE, "build")
C_REPORT = os.path.join(BUILD, "coverage", "c", "index.html")
C_XML = os.path.join(BUILD, "coverage", "c", "coverage.xml")       # Cobertura


def _gcda_files():
    return glob.glob(os.path.join(BUILD, "**", "axis_dpi_test.dir", "**", "*.gcda"),
                     recursive=True)


def pytest_sessionstart(session):
    for f in _gcda_files():
        os.remove(f)


def pytest_terminal_summary(terminalreporter):
    axis_vip_dpi = sys.modules.get("axis_vip_dpi")
    dumps = [lib.mock_gcov_dump for lib in getattr(axis_vip_dpi, "_libs", {}).values()
             if hasattr(lib, "mock_gcov_dump")]
    if not dumps:
        return                            # test library not loaded, or no --coverage
    for dump in dumps:
        dump()                            # else written only at process exit

    tr = terminalreporter
    tr.section("C coverage (gcovr)")
    os.makedirs(os.path.dirname(C_REPORT), exist_ok=True)
    res = subprocess.run(
        [sys.executable, "-m", "gcovr", "--root", HERE, "--filter", "src/",
         "--txt", "-", "--html-details", C_REPORT, "--cobertura", C_XML, BUILD],
        capture_output=True, text=True)
    if res.returncode:
        tr.write_line(f"gcovr failed ({res.returncode}): {res.stderr.strip()}")
        return
    tr.write(res.stdout)
    tr.write_line(f"HTML: {C_REPORT}")
