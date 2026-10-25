"""Runner for the functional tests in ../test_cases/test_axis_vip.py on Verilator.

Same set-up as test_axis_vip_runner.py (Questa): every cocotb test runs in its
own model run, with its own log and waveform:

    python3 test_axis_vip_verilator_runner.py
    pytest test_axis_vip_verilator_runner.py   (skipped without verilator on PATH)

Environment (plus the shared ones in verilator_common.py):
    WAVES=0          don't record waves (default: every test is recorded into
                     artifacts/vcd/<test>.vcd; view with gtkwave or surfer)
    GUI=1            open the waves in gtkwave after the run (all selected
                     tests in one model run)
    TESTCASE         run only these tests (comma-separated)
"""
import os
import shutil
import sys

import pytest
from cocotb_tools.check_results import get_results

from verilator_common import LOG_DIR, WAVE_DIR, cocotb_tests, run_verilator

MODULE = "test_axis_vip"


def _selected():
    testcase = os.environ.get("TESTCASE")
    return testcase.split(",") if testcase else cocotb_tests(MODULE)


# One pytest case per cocotb test, each in its own model run with its own .vcd.
# Under pytest the runner fails the case itself when the cocotb test fails.
@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
@pytest.mark.parametrize("testcase", _selected())
def test_axis_vip_verilator(testcase):
    run_verilator(testcase, MODULE)


if __name__ == "__main__":
    # standalone, the runner does not check results.xml: do it here
    if os.environ.get("GUI") == "1":
        results = [run_verilator(_selected(), MODULE)]   # one run, one viewer
    else:
        results = [run_verilator(t, MODULE) for t in _selected()]
    n_tests = n_failed = 0
    for r in results:
        t, f = get_results(r)
        n_tests, n_failed = n_tests + t, n_failed + f
    print(f"{n_tests - n_failed}/{n_tests} cocotb tests passed")
    if os.environ.get("WAVES", "1") != "0":
        print(f"waves: {WAVE_DIR}/<test>.vcd   (view: gtkwave <file>)")
    print(f"logs:  {LOG_DIR}/<test>.log")
    sys.exit(1 if n_failed or not n_tests else 0)
