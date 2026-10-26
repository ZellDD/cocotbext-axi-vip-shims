# Prints the DUT coverage summary that tests/test_byteswap_runner.py writes
# (pytest captures the runner's own output).
import os
import time

_start = time.time()


def pytest_terminal_summary(terminalreporter):
    from test_byteswap_runner import COV_SUMMARY, SIM
    # written by this session's run (pytest has unset PYTEST_CURRENT_TEST by
    # now, so the runner's coverage() can't tell)
    if COV_SUMMARY.is_file() and os.path.getmtime(COV_SUMMARY) >= _start:
        terminalreporter.section(f"DUT coverage ({SIM})")
        terminalreporter.write(COV_SUMMARY.read_text())
