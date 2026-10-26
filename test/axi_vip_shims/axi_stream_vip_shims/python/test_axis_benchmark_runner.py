"""Runner for the benchmark in ../test_cases/test_axis_benchmark.py on Questa.

Runs every bench_* test BENCH_REPEATS times, each run in a fresh vsim and
without waves (logging would dominate the timing), and tabulates median wall
time and spread into benchmark.txt. Everything goes to
artifacts/test_axis_benchmark_runner/: build.log, logs/<test>.log,
results/<test>.json (one per run, written by the test) and benchmark.txt:

    python3 test_axis_benchmark_runner.py
    BENCH=1 pytest test_axis_benchmark_runner.py -s    (skipped without BENCH=1)

Environment (plus the shared ones in questa_common.py):
    BENCH_BEATS      beats per case (default 10240)
    BENCH_REPEATS    runs per case (default 3)
"""
import json
import os
import shutil
import statistics
import sys

import pytest
from cocotb_tools.check_results import get_results

from questa_common import artifact_dir, cocotb_tests, run_questa

MODULE = "test_axis_benchmark"
OUT = artifact_dir(__file__)


def benchmark(run=run_questa, out=OUT, sim="vsim"):
    """Runs the cases with run(testcase, module, out, waves, env, coverage),
    never with coverage (it would skew the timings), and writes the
    table to out/benchmark.txt; sim names the simulator in the note. Returns
    (table, results.xml paths)."""
    repeats = int(os.environ.get("BENCH_REPEATS", 3))
    result_dir = out / "results"         # one <test>.json per run, from the test
    shutil.rmtree(result_dir, ignore_errors=True)
    xmls, runs = [], {}
    for _ in range(repeats):             # interleaved, so drift hits every case
        for t in cocotb_tests(MODULE):
            xml = run(t, MODULE, out, waves=False,
                      env={"BENCH_RESULT_DIR": str(result_dir)}, coverage=False)
            xmls.append(xml)
            f = result_dir / f"{t}.json"
            if f.is_file():
                runs.setdefault(t, []).append(json.loads(f.read_text()))
                f.unlink()
            elif get_results(xml)[1] == 0:       # no result and no failure either
                raise RuntimeError(f"{t} passed but wrote no {f.name}")

    head = ("test", "flavour", "reads", "beats", "beats/pkt", "runs",
            "wall [s]", "spread", "beats/s", "sim [us]", "beats/cycle")
    body = []
    for t in sorted(runs):
        rs = runs[t]
        walls = [r["wall_s"] for r in rs]
        med = statistics.median(walls)
        r = rs[0]
        body.append((t, r["flavour"], r["reads"], r["beats"], r["beats_per_packet"],
                     len(rs), f"{med:.2f}", f"{(max(walls) - min(walls)) / med:.0%}",
                     f"{round(r['beats'] / med):,}", f'{r["sim_us"]:.1f}',
                     f'{r["beats_per_cycle"]:.3f}'))
    widths = [max(len(str(c)) for c in col) for col in zip(head, *body)]
    line = lambda cells: "  ".join(str(c).rjust(w) for c, w in zip(cells, widths))  # noqa: E731
    table = "\n".join([line(head), line("-" * w for w in widths)] + [line(b) for b in body])
    note = (f"\nmedian of {repeats} runs per case, each in a fresh {sim}; "
            "spread = (max - min) / median of the wall times\n")
    (out / "benchmark.txt").write_text(table + "\n" + note)
    return table + "\n" + note, xmls


# Opt-in under pytest: takes a while and measures speed rather than correctness.
@pytest.mark.skipif(os.environ.get("BENCH") != "1", reason="set BENCH=1 to run")
def test_benchmark():
    table, _ = benchmark()
    print("\n" + table)


if __name__ == "__main__":
    table, xmls = benchmark()
    print(table)
    print(f"\nwritten to {OUT / 'benchmark.txt'}")
    failed = sum(get_results(x)[1] for x in xmls)
    sys.exit(1 if failed else 0)
