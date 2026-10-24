"""Benchmark: the same 10k+ AXI4-Stream beats through

  vip  cocotb -> master shim -> Xilinx VIP master -> bus -> Xilinx VIP slave
       -> slave shim -> cocotb                      (tb_top's VIP loopback)
  ext  cocotbext-axi AxiStreamSource -> bus -> AxiStreamSink  (tb_top.bm_*)

once as 16-beat packets and once as single-beat packets (which mostly
measures per-call overhead). Every beat carries TKEEP/TLAST/TID/TDEST/TUSER,
and the receiver checks every byte. No backpressure, so the bus could take a
beat every cycle.

Each test writes its numbers to artifacts/logs/benchmark/<test>.json;
test_questa_runner.py collects them into artifacts/logs/benchmark.txt.
Run through the runner (BENCH=1 or the test_benchmark pytest case).
"""
import json
import os
import random
import time
from pathlib import Path

import cocotb
from cocotb.triggers import ClockCycles, Timer, with_timeout
from cocotb.utils import get_sim_time

from axis_vip_dpi import AXIS_MASTER_VIP, AXIS_SLAVE_VIP

N_BEATS = int(os.environ.get("BENCH_BEATS", 10240))
W = 4                                   # TDATA bytes, as tb_top.sv
CLK_NS = 10
IFACE = dict(tdata_width=8 * W, has_tkeep=True, has_tstrb=True, has_tlast=True,
             tid_width=4, tdest_width=4, tuser_width=4)
RESULT_DIR = Path(__file__).resolve().parent.parent / "artifacts" / "logs" / "benchmark"


async def reset(dut, cycles=20):
    dut.aresetn.value = 0
    await ClockCycles(dut.aclk, cycles)
    dut.aresetn.value = 1
    await ClockCycles(dut.aclk, 5)


def packets(beats_per_packet):
    """N_BEATS beats as (payload bytes, tid, tdest, tuser) packets; the last
    beat of each packet is partial so TKEEP is exercised."""
    rng = random.Random(42)
    out, left = [], N_BEATS
    while left:
        n = min(beats_per_packet, left)
        nbytes = (n - 1) * W + rng.randint(1, W)
        out.append((bytes(rng.getrandbits(8) for _ in range(nbytes)),
                    rng.getrandbits(4), rng.getrandbits(4), rng.getrandbits(4)))
        left -= n
    return out


class Stopwatch:
    def __init__(self):
        self.wall, self.sim = time.perf_counter(), get_sim_time("ns")

    def result(self, dut, name, flavour, beats_per_packet, n_packets):
        wall = time.perf_counter() - self.wall
        sim_ns = get_sim_time("ns") - self.sim
        cycles = sim_ns / CLK_NS
        r = dict(test=name, flavour=flavour, beats=N_BEATS, packets=n_packets,
                 beats_per_packet=beats_per_packet, wall_s=round(wall, 3),
                 sim_us=round(sim_ns / 1000, 2), cycles=int(cycles),
                 beats_per_s=round(N_BEATS / wall), beats_per_cycle=round(N_BEATS / cycles, 3))
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        (RESULT_DIR / f"{name}.json").write_text(json.dumps(r, indent=2))
        dut._log.info("BENCH %s", r)
        return r


# ----------------------------------------------------------------- baseline
@cocotb.test()
async def bench_idle(dut):
    """The same number of clock cycles with no traffic and no Python wakeups:
    what the simulator itself costs, to read the other numbers against."""
    await reset(dut)
    sw = Stopwatch()
    await Timer(N_BEATS * CLK_NS, "ns")          # not ClockCycles: no wakeups
    sw.result(dut, "bench_idle", "no traffic", 0, 0)


# ------------------------------------------------------------- VIP + shims
async def _bench_vip(dut, name, beats_per_packet, bulk=False):
    await reset(dut)
    mst = AXIS_MASTER_VIP("mst0", dut.mst_shim, **IFACE)
    slv = AXIS_SLAVE_VIP("slv0", dut.slv_shim, **IFACE)
    mst.check()
    slv.check()
    pkts = packets(beats_per_packet)

    async def sender():
        for data, tid, tdest, tuser in pkts:
            beats = [data[i:i + W] for i in range(0, len(data), W)]
            for n, chunk in enumerate(beats):
                keep = (1 << len(chunk)) - 1
                mst.put_transaction(tdata=int.from_bytes(chunk, "little"),
                                    tkeep=keep, tstrb=keep,
                                    tlast=(n == len(beats) - 1),
                                    tid=tid, tdest=tdest, tuser=tuser)

    sw = Stopwatch()
    await sender()                       # put_transaction() takes no sim time
    if bulk:
        # sleep through the whole transfer, no per-beat wakeups: only the
        # SV shims and the VIP run until everything has arrived
        while slv.pending < N_BEATS:
            await Timer((N_BEATS - slv.pending + 8) * CLK_NS, "ns")
    for data, tid, tdest, tuser in pkts:
        out = bytearray()
        while True:
            t = await slv.get_transaction()
            raw = t.tdata.to_bytes(W, "little")
            out += bytes(b for i, b in enumerate(raw) if (t.tkeep >> i) & 1)
            assert (t.tid, t.tdest, t.tuser) == (tid, tdest, tuser)
            if t.tlast:
                break
        assert bytes(out) == data
    return sw.result(dut, name, "vip shim" + (" (bulk)" if bulk else ""),
                     beats_per_packet, len(pkts))


@cocotb.test()
async def bench_vip_packets(dut):
    await with_timeout(_bench_vip(dut, "bench_vip_packets", 16), 10, "ms")


@cocotb.test()
async def bench_vip_16_bulk(dut):
    """16-beat packets, but cocotb sleeps until all beats have arrived and
    then reads them: the cost of the SV shims + VIP alone."""
    await with_timeout(_bench_vip(dut, "bench_vip_16_bulk", 16, bulk=True), 10, "ms")


@cocotb.test()
async def bench_vip_64_bulk(dut):
    """64-beat packets, but cocotb sleeps until all beats have arrived and
    then reads them: the cost of the SV shims + VIP alone."""
    await with_timeout(_bench_vip(dut, "bench_vip_64_bulk", 64, bulk=True), 10, "ms")


@cocotb.test()
async def bench_vip_128_bulk(dut):
    """128-beat packets, but cocotb sleeps until all beats have arrived and
    then reads them: the cost of the SV shims + VIP alone."""
    await with_timeout(_bench_vip(dut, "bench_vip_128_bulk", 128, bulk=True), 10, "ms")


@cocotb.test()
async def bench_vip_256_bulk(dut):
    """256-beat packets, but cocotb sleeps until all beats have arrived and
    then reads them: the cost of the SV shims + VIP alone."""
    await with_timeout(_bench_vip(dut, "bench_vip_256_bulk", 256, bulk=True), 10, "ms")


@cocotb.test()
async def bench_vip_beats(dut):
    await with_timeout(_bench_vip(dut, "bench_vip_beats", 1), 10, "ms")


# ------------------------------------------------------------ cocotbext-axi
async def _bench_ext(dut, name, beats_per_packet):
    from cocotbext.axi import AxiStreamBus, AxiStreamFrame, AxiStreamSink, AxiStreamSource

    bus = AxiStreamBus.from_prefix(dut, "bm")
    src = AxiStreamSource(bus, dut.aclk, dut.aresetn, reset_active_level=False)
    sink = AxiStreamSink(bus, dut.aclk, dut.aresetn, reset_active_level=False)
    await reset(dut)
    pkts = packets(beats_per_packet)

    sw = Stopwatch()
    for data, tid, tdest, tuser in pkts:
        src.send_nowait(AxiStreamFrame(data, tid=tid, tdest=tdest, tuser=tuser))
    for data, tid, tdest, tuser in pkts:
        f = await sink.recv()
        assert bytes(f.tdata) == data
        assert (f.tid, f.tdest, f.tuser) == (tid, tdest, tuser), f
    return sw.result(dut, name, "cocotbext-axi", beats_per_packet, len(pkts))


@cocotb.test()
async def bench_ext_packets(dut):
    await with_timeout(_bench_ext(dut, "bench_ext_packets", 16), 10, "ms")


@cocotb.test()
async def bench_ext_beats(dut):
    await with_timeout(_bench_ext(dut, "bench_ext_beats", 1), 10, "ms")
