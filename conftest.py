import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(
    HERE, "src", "axi_vip_shims", "axi_stream_vip_shim", "src", "pyhon"))

# cocotb test modules: run inside the simulator, started by the runners in
# test/**/python/ (test_axis_vip_runner.py, test_axis_benchmark_runner.py)
collect_ignore_glob = ["test/**/test_cases/*"]
