#!/usr/bin/env bash
# Set up the Python venv and pre-commit hooks, build the DPI-C libraries into
# lib/, run the tests.
#
#   ./install.sh                    venv + build + tests
#   ./install.sh --no-tests         venv + build only
#   ./install.sh --vip-src PATH     use these AXI4-Stream VIP sources instead of
#                                   Vivado: a directory or a .f file list (see
#                                   AXIS_VIP_SRC in questa_common.py)
#
# The simulation tests of each simulator run only when it is on PATH and VIP
# sources are available: --vip-src, or Vivado (XILINX_VIVADO, or vivado on
# PATH), from which they generate the VIP cores into build/ip.
#   Questa      vsim        test_axis_vip_questa_runner.py
#   Verilator   verilator   test_axis_vip_verilator_runner.py (5.052 or newer)
# The benchmarks run only with BENCH=1 (see README.md).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

RUN_TESTS=1
while [ $# -gt 0 ]; do
    case "$1" in
        --no-tests)  RUN_TESTS=0 ;;
        --vip-src)   [ $# -ge 2 ] || { echo "--vip-src needs a path" >&2; exit 2; }
                     AXIS_VIP_SRC=$2; shift ;;
        --vip-src=*) AXIS_VIP_SRC=${1#--vip-src=} ;;
        -h|--help)   sed -n '2,/^set /{/^#/p}' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)           echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done
if [ -n "${AXIS_VIP_SRC:-}" ]; then
    [ -e "$AXIS_VIP_SRC" ] || { echo "--vip-src: $AXIS_VIP_SRC does not exist" >&2; exit 2; }
    export AXIS_VIP_SRC=$(realpath "$AXIS_VIP_SRC")
fi

step() { printf '\n==> %s\n' "$*"; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- tools
step "Checking tools"
for tool in cmake c++ python3; do
    command -v "$tool" >/dev/null || die "$tool not found on PATH"
    printf '  %-8s %s\n' "$tool" "$(command -v "$tool")"
done

# ---------------------------------------------------------------- python
step "Python venv (.venv)"
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
PY=.venv/bin/python
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r requirements.txt
"$PY" -c 'import cocotb; print("  cocotb", cocotb.__version__)'

# ---------------------------------------------------------------- git hooks
step "pre-commit hooks"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    .venv/bin/pre-commit install
else
    echo "  not a git checkout: skipped"
fi

# ---------------------------------------------------------------- build
step "Building DPI-C libraries"
cmake -B build
cmake --build build -j
ls lib/*.so | sed 's/^/  /'

[ "$RUN_TESTS" = 1 ] || { step "Done (tests skipped)"; exit 0; }

# ---------------------------------------------------------------- tests
step "Running tests"
RUNNERS=test/axi_vip_shims/axi_stream_vip_shims/python
PYTEST_ARGS=(-s)
if [ -n "${AXIS_VIP_SRC:-}" ]; then
    VIP="VIP sources from $AXIS_VIP_SRC"
elif [ -n "${XILINX_VIVADO:-}" ] || command -v vivado >/dev/null; then
    VIP="VIP sources from Vivado"
else
    VIP=
fi

# simulator NAME EXE RUNNER...: keep its runners when EXE is on PATH and VIP
# sources are available, else deselect them
simulator() {
    local name=$1 exe=$2 why= r
    shift 2
    if ! command -v "$exe" >/dev/null; then
        why="$exe not on PATH"
    elif [ -z "$VIP" ]; then
        why="no VIP sources (pass --vip-src, or put vivado on PATH)"
    fi
    if [ -z "$why" ]; then
        echo "  $name ($(command -v "$exe")), $VIP: including its simulation tests"
    else
        echo "  skipping the $name simulation tests: $why"
        for r in "$@"; do
            PYTEST_ARGS+=(--deselect "$RUNNERS/$r")
        done
    fi
}
simulator Questa vsim \
    test_axis_vip_questa_runner.py test_axis_benchmark_runner.py
simulator Verilator verilator \
    test_axis_vip_verilator_runner.py test_axis_benchmark_verilator_runner.py

"$PY" -m pytest "${PYTEST_ARGS[@]}"

step "Done"
