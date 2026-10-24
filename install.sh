#!/usr/bin/env bash
# Set up the Python venv, build the DPI-C libraries into lib/, run the tests.
#
#   ./install.sh                    venv + build + tests
#   ./install.sh --no-tests         venv + build only
#   ./install.sh --vip-src PATH     use these AXI4-Stream VIP sources instead of
#                                   Vivado: a directory or a .f file list (see
#                                   AXIS_VIP_SRC in test_questa_runner.py)
#
# The Questa simulation tests (test_questa_runner.py) runs only when vsim is on PATH
# and VIP sources are available: --vip-src, or Vivado (XILINX_VIVADO, or
# vivado on PATH), from which it generates the VIP cores into build/ip.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

RUN_TESTS=1
while [ $# -gt 0 ]; do
    case "$1" in
        --no-tests)  RUN_TESTS=0 ;;
        --vip-src)   [ $# -ge 2 ] || { echo "--vip-src needs a path" >&2; exit 2; }
                     AXIS_VIP_SRC=$2; shift ;;
        --vip-src=*) AXIS_VIP_SRC=${1#--vip-src=} ;;
        -h|--help)   sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
for tool in cmake cc python3; do
    command -v "$tool" >/dev/null || die "$tool not found on PATH"
    printf '  %-8s %s\n' "$tool" "$(command -v "$tool")"
done

# ---------------------------------------------------------------- python
step "Python venv (.venv)"
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
PY=.venv/bin/python
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r requirements.txt
"$PY" -c 'import cocotb; print("  cocotb", cocotb.__version__)'

# ---------------------------------------------------------------- build
step "Building DPI-C libraries"
cmake -B build
cmake --build build -j
ls lib/*.so | sed 's/^/  /'

[ "$RUN_TESTS" = 1 ] || { step "Done (tests skipped)"; exit 0; }

# ---------------------------------------------------------------- tests
step "Running tests"
PYTEST_ARGS=(-s)
if ! command -v vsim >/dev/null; then
    SKIP_WHY="vsim not on PATH"
elif [ -n "${AXIS_VIP_SRC:-}" ]; then
    echo "  Questa found, VIP sources from $AXIS_VIP_SRC: including the simulation test"
elif [ -n "${XILINX_VIVADO:-}" ] || command -v vivado >/dev/null; then
    echo "  Questa + Vivado found: including the simulation test"
else
    SKIP_WHY="no VIP sources (pass --vip-src, or put vivado on PATH)"
fi
if [ -n "${SKIP_WHY:-}" ]; then
    echo "  skipping the Questa simulation test: $SKIP_WHY"
    PYTEST_ARGS+=(--deselect
        test/axi_vip_shims/axi_stream_vip_shims/python/test_questa_runner.py::test_axis_vip_questa)
fi
"$PY" -m pytest "${PYTEST_ARGS[@]}"

step "Done"
