"""Runner for the functional tests in ../test_cases/test_axis_vip.py on Verilator.

Same set-up as test_axis_vip_questa_runner.py (Questa): every cocotb test runs in its
own model run, with its own log and waveform, all in
artifacts/test_axis_vip_verilator_runner/ (build.log, logs/<test>.log,
vcd/<test>.vcd):

    python3 test_axis_vip_verilator_runner.py
    pytest test_axis_vip_verilator_runner.py   (skipped without verilator on PATH)

Environment (plus the shared ones in verilator_common.py):
    WAVES=0          don't record waves (default: every test is recorded into
                     vcd/<test>.vcd; view with gtkwave or surfer)
    GUI=1            open the waves in gtkwave after the run (all selected
                     tests in one model run)
    TESTCASE         run only these tests (comma-separated)
    EXT_SHIMS_COVERAGE=0  no coverage under pytest (default: C, SV and Python,
                          reported at the end and into build/coverage/, see
                          coverage_common.py; never when run as a script)
"""
import os
import shutil
import sys

import pytest
from cocotb_tools.check_results import get_results

from verilator_common import artifact_dir, cocotb_tests, run_verilator

MODULE = "test_axis_vip"
OUT = artifact_dir(__file__)


def _selected():
    testcase = os.environ.get("TESTCASE")
    return testcase.split(",") if testcase else cocotb_tests(MODULE)


# One pytest case per cocotb test, each in its own model run with its own .vcd.
# Under pytest the runner fails the case itself when the cocotb test fails.
@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
@pytest.mark.parametrize("testcase", _selected())
def test_axis_vip_verilator(testcase):
    run_verilator(testcase, MODULE, OUT)


if __name__ == "__main__":
    # standalone, the runner does not check results.xml: do it here
    if os.environ.get("GUI") == "1":
        results = [run_verilator(_selected(), MODULE, OUT)]   # one run, one viewer
    else:
        results = [run_verilator(t, MODULE, OUT) for t in _selected()]
    n_tests = n_failed = 0
    for r in results:
        t, f = get_results(r)
        n_tests, n_failed = n_tests + t, n_failed + f
    print(f"{n_tests - n_failed}/{n_tests} cocotb tests passed")
    if os.environ.get("WAVES", "1") != "0":
        print(f"waves: {OUT / 'vcd'}/<test>.vcd   (view: gtkwave <file>)")
    print(f"logs:  {OUT / 'logs'}/<test>.log")
    sys.exit(1 if n_failed or not n_tests else 0)
