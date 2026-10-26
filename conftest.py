import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(
    HERE, "src", "axi_vip_shims", "axi_stream_vip_shim", "src", "python"))

# cocotb test modules: run inside the simulator, started by the runners in
# test/**/python/ (test_axis_vip_questa_runner.py, test_axis_benchmark_runner.py)
collect_ignore_glob = ["test/**/test_cases/*",
                       # standalone projects: run pytest from inside them
                       "examples/*"]


# --------------------------------------------------------------- coverage
# Python coverage comes from pytest-cov (pytest.ini, .coveragerc), which also
# combines the simulators' data. C and SV coverage (coverage_common.py): reset
# at the start, reported at the end.
sys.path.insert(0, os.path.join(HERE, "test", "axi_vip_shims", "axi_stream_vip_shims",
                                "python"))
import coverage_common  # noqa: E402


def _off():
    return os.environ.get("EXT_SHIMS_COVERAGE", "1") == "0"


def pytest_configure(config):
    # EXT_SHIMS_COVERAGE=0: also pytest-cov (switched on in pytest.ini), as
    # --no-cov would. It reads its options from the early-parsed namespace,
    # at session start.
    if _off():
        config.known_args_namespace.no_cov = True


def pytest_sessionstart(session):
    coverage_common.reset()


def pytest_terminal_summary(terminalreporter):
    if _off():
        return
    # test_c_side.py's library is loaded in this process, so its counters
    # would only be written at exit, after this report: write them now.
    axis_vip_dpi = sys.modules.get("axis_vip_dpi")
    for lib in getattr(axis_vip_dpi, "_libs", {}).values():
        if hasattr(lib, "mock_gcov_dump"):
            lib.mock_gcov_dump()
    try:
        sections = coverage_common.report()
    except RuntimeError as e:
        terminalreporter.section("coverage")
        terminalreporter.write_line(f"coverage report failed: {e}")
        return
    for title, text in sections:
        terminalreporter.section(title)
        terminalreporter.write(text)
