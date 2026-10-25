"""Runner for the benchmark in ../test_cases/test_axis_benchmark.py on Verilator.

Same benchmark as test_axis_benchmark_runner.py (Questa): every bench_* test
BENCH_REPEATS times, each run a fresh model run without waves, tabulated into
artifacts/logs/verilator/benchmark.txt:

    python3 test_axis_benchmark_verilator_runner.py
    BENCH=1 pytest test_axis_benchmark_verilator_runner.py -s
                     (skipped without BENCH=1 or without verilator on PATH)

The model is built without --trace (WAVES=0) so tracing support doesn't slow
it down, unless the functional Verilator runner built it first in the same
process.

Environment (plus the shared ones in verilator_common.py):
    BENCH_BEATS      beats per case (default 10240)
    BENCH_REPEATS    runs per case (default 3)
"""
import os
import shutil
import sys

import pytest
from cocotb_tools.check_results import get_results

from test_axis_benchmark_runner import benchmark
from verilator_common import LOG_DIR, run_verilator


def verilator_benchmark():
    os.environ["WAVES"] = "0"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return benchmark(run=run_verilator, out=LOG_DIR / "benchmark.txt",
                     sim="Verilator model")


# Opt-in under pytest: takes a while and measures speed rather than correctness.
@pytest.mark.skipif(os.environ.get("BENCH") != "1", reason="set BENCH=1 to run")
@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_benchmark_verilator():
    table, _ = verilator_benchmark()
    print("\n" + table)


if __name__ == "__main__":
    table, xmls = verilator_benchmark()
    print(table)
    print(f"\nwritten to {LOG_DIR / 'benchmark.txt'}")
    failed = sum(get_results(x)[1] for x in xmls)
    sys.exit(1 if failed else 0)
