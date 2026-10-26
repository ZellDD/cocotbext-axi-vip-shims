#!/usr/bin/env bash
# Pack the AXI4-Stream VIP sources the Verilator flow needs into one base64
# string, for the CI secret AXIS_VIP_SRC_TXZ_B64 (see .github/workflows/ci.yml).

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
OUT=${1:-build/axis_vip_src.txz.b64}
LIMIT=49152                                   # GitHub: secrets are at most 48 KB

PY=python3
[ -x .venv/bin/python ] && PY=$PWD/.venv/bin/python

# Vivado install and generated cores, through the runners' own code
VIVADO=$(cd test/axi_vip_shims/axi_stream_vip_shims/python && "$PY" -c '
import questa_common as q
v = q._vivado()
q._generated_sources(v)          # generates build/ip on first use
print(v)')
HDL=$VIVADO/data/xilinx_vip/hdl
TOP=$(ls "$VIVADO"/data/ip/xilinx/axi4stream_vip_v1_1*/hdl/axi4stream_vip_v1_1_vl_rfs.sv | tail -1)

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
DIR=$STAGE/axis_vip_src
mkdir -p "$DIR/include"

# What the Verilator flow compiles (verilator_common.py leaves the AXI4 /
# clock / reset VIP files out)
cp "$HDL"/{xil_common_vip_pkg,axi4stream_vip_pkg,axi4stream_vip_if,axi4stream_vip_axi4streampc}.sv "$DIR/"
cp "$TOP" "$DIR/"
for c in mst slv; do
    cp build/ip/axi4stream_vip_${c}/sim/axi4stream_vip_${c}{,_pkg}.sv "$DIR/"
done
cp "$VIVADO"/data/xilinx_vip/include/*.svh "$DIR/include/"

mkdir -p "$(dirname "$OUT")"
tar -C "$STAGE" -cJf - axis_vip_src | base64 -w0 > "$OUT"

SIZE=$(wc -c < "$OUT")
echo "VIP sources from $VIVADO:"
(cd "$DIR" && find . -type f | sort | sed 's|^\./|  |')
echo "written to $OUT ($SIZE bytes, limit $LIMIT)"
if [ "$SIZE" -gt "$LIMIT" ]; then
    echo "error: too big for a GitHub secret" >&2
    exit 1
fi
