"""Runner for the functional tests in ../test_cases/test_axis_vip.py on Questa.

Every cocotb test runs in its own vsim, with its own log and waveform, all in
artifacts/test_axis_vip_runner/ (build.log, logs/<test>.log, wlf/<test>.wlf):

    python3 test_axis_vip_runner.py     (or: pytest test_axis_vip_runner.py)

Environment (plus the shared ones in questa_common.py):
    WAVES=0          don't record waves (default: every test is recorded into
                     wlf/<test>.wlf; view with `vsim -view <file>`)
    GUI=1            open the Questa GUI (all selected tests in one session)
    TESTCASE         run only these tests (comma-separated)
"""
import os
import sys

import pytest
from cocotb_tools.check_results import get_results

from questa_common import artifact_dir, cocotb_tests, run_questa

MODULE = "test_axis_vip"
OUT = artifact_dir(__file__)


def _selected():
    testcase = os.environ.get("TESTCASE")
    return testcase.split(",") if testcase else cocotb_tests(MODULE)


# One pytest case per cocotb test, each in its own vsim with its own .wlf.
# Under pytest the runner fails the case itself when the cocotb test fails.
@pytest.mark.parametrize("testcase", _selected())
def test_axis_vip_questa(testcase):
    run_questa(testcase, MODULE, OUT)


if __name__ == "__main__":
    # standalone, the runner does not check results.xml: do it here
    if os.environ.get("GUI") == "1":
        results = [run_questa(_selected(), MODULE, OUT)]      # one interactive session
    else:
        results = [run_questa(t, MODULE, OUT) for t in _selected()]
    n_tests = n_failed = 0
    for r in results:
        t, f = get_results(r)
        n_tests, n_failed = n_tests + t, n_failed + f
    print(f"{n_tests - n_failed}/{n_tests} cocotb tests passed")
    if os.environ.get("WAVES", "1") != "0":
        print(f"waves: {OUT / 'wlf'}/<test>.wlf   (view: vsim -view <file>)")
    print(f"logs:  {OUT / 'logs'}/<test>.log")
    sys.exit(1 if n_failed or not n_tests else 0)
