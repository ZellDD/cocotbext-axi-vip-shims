"""Benchmark: the same 10k+ AXI4-Stream beats through

  vip  cocotb -> master shim -> Xilinx VIP master -> bus -> Xilinx VIP slave
       -> slave shim -> cocotb                      (tb_top's VIP loopback)
  ext  cocotbext-axi AxiStreamSource -> bus -> AxiStreamSink  (tb_top.bm_*)

once as 16-beat packets and once as single-beat packets (which mostly
measures per-call overhead). Every beat carries TKEEP/TLAST/TID/TDEST/TUSER,
and the receiver checks every byte. No backpressure, so the bus could take a
beat every cycle.

How the receiver gets its beats is the other variable ("reads" in the table):
  as it comes  cocotb takes each beat right when it arrives. This is all
               cocotbext-axi's sink does (it samples the bus every cycle and
               hands over frames as they complete) and what get_transaction()
               does.
  every B      the shim queues the beats; cocotb sleeps until B are waiting,
               drains those B, and sleeps again (bench_vip_batch_*).

Each test writes its numbers to artifacts/logs/benchmark/<test>.json;
python/test_axis_benchmark_runner.py runs every test BENCH_REPEATS times, each in a fresh
vsim, and collects the medians into artifacts/logs/benchmark.txt.
Run through that runner.
"""
import json
import logging
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
# The runner names the result file after the test it launches, so renaming a
# test cannot leave the table out of sync.
TEST_NAME = os.environ.get("BENCH_NAME", "unnamed")
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

    def result(self, dut, flavour, beats_per_packet, n_packets, reads="-"):
        name = TEST_NAME
        wall = time.perf_counter() - self.wall
        sim_ns = get_sim_time("ns") - self.sim
        cycles = sim_ns / CLK_NS
        r = dict(test=name, flavour=flavour, reads=reads, beats=N_BEATS,
                 packets=n_packets, beats_per_packet=beats_per_packet, wall_s=round(wall, 3),
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
    sw.result(dut, "no traffic", 0, 0)


# ------------------------------------------------------------- VIP + shims
async def _bench_vip(dut, beats_per_packet, batch=None):
    """batch=None: `await get_transaction()` for every beat, woken by the
    shim's status signal: cocotb takes each beat as it arrives, like
    cocotbext-axi's sink. batch=B: cocotb sleeps on a timer (no per-beat
    wakeups) until B beats are queued in the shim, drains those B, repeats."""
    await reset(dut)
    mst = AXIS_MASTER_VIP("mst0", dut.mst_shim, **IFACE)
    slv = AXIS_SLAVE_VIP("slv0", dut.slv_shim, **IFACE)
    mst.check()
    slv.check()
    pkts = packets(beats_per_packet)

    sw = Stopwatch()
    for data, tid, tdest, tuser in pkts:          # put_transaction(): no sim time
        beats = [data[i:i + W] for i in range(0, len(data), W)]
        for n, chunk in enumerate(beats):
            keep = (1 << len(chunk)) - 1
            mst.put_transaction(tdata=int.from_bytes(chunk, "little"),
                                tkeep=keep, tstrb=keep,
                                tlast=(n == len(beats) - 1),
                                tid=tid, tdest=tdest, tuser=tuser)

    got = []
    if batch is None:
        for _ in range(N_BEATS):
            got.append(await slv.get_transaction())
    else:
        while len(got) < N_BEATS:
            want = min(batch, N_BEATS - len(got))
            while slv.pending < want:
                # the bus delivers one beat per clock: sleep for the rest
                await Timer((want - slv.pending) * CLK_NS, "ns")
            for _ in range(want):
                got.append(slv.try_get_transaction())
            # `pending` (rx_count) is updated in the NBA region: still the old
            # value in this time step, so let it settle before reading it again
            await Timer(1, "step")

    beats_iter = iter(got)
    for data, tid, tdest, tuser in pkts:
        out = bytearray()
        for t in beats_iter:
            raw = t.tdata.to_bytes(W, "little")
            out += bytes(b for i, b in enumerate(raw) if (t.tkeep >> i) & 1)
            assert (t.tid, t.tdest, t.tuser) == (tid, tdest, tuser)
            if t.tlast:
                break
        assert bytes(out) == data
    return sw.result(dut, "vip shim", beats_per_packet, len(pkts),
                     reads="as it comes" if batch is None else f"every {batch}")


@cocotb.test()
async def bench_vip_packets(dut):
    """16-beat packets, every beat taken as it arrives."""
    await with_timeout(_bench_vip(dut, 16), 10, "ms")


@cocotb.test()
async def bench_vip_beats(dut):
    """Single-beat packets, every beat taken as it arrives."""
    await with_timeout(_bench_vip(dut, 1), 10, "ms")


# 16-beat packets, read in batches: wait until B beats are queued in the
# shim, then take those B. The packet size stays the same as above, only the
# batch changes.
@cocotb.test()
async def bench_vip_packets_batch_16(dut):
    await with_timeout(_bench_vip(dut, 16, batch=16), 10, "ms")


@cocotb.test()
async def bench_vip_packets_batch_64(dut):
    await with_timeout(_bench_vip(dut, 16, batch=64), 10, "ms")


@cocotb.test()
async def bench_vip_packets_batch_128(dut):
    await with_timeout(_bench_vip(dut, 16, batch=128), 10, "ms")


@cocotb.test()
async def bench_vip_packets_batch_256(dut):
    await with_timeout(_bench_vip(dut, 16, batch=256), 10, "ms")


@cocotb.test()
async def bench_vip_packets_batch_all(dut):
    """Everything queued in the shim first, then one pass over all of it."""
    await with_timeout(_bench_vip(dut, 16, batch=N_BEATS),
                       10, "ms")


# ------------------------------------------------------------ cocotbext-axi
async def _bench_ext(dut, beats_per_packet):
    from cocotbext.axi import AxiStreamBus, AxiStreamFrame, AxiStreamSink, AxiStreamSource

    bus = AxiStreamBus.from_prefix(dut, "bm")
    src = AxiStreamSource(bus, dut.aclk, dut.aresetn, reset_active_level=False)
    sink = AxiStreamSink(bus, dut.aclk, dut.aresetn, reset_active_level=False)
    # cocotbext-axi logs every frame at INFO ("TX frame" / "RX frame"), which
    # the shims don't: that formatting and transcript writing would be timed
    # too. Keep its setup messages, drop the per-frame ones.
    src.log.setLevel(logging.WARNING)
    sink.log.setLevel(logging.WARNING)
    await reset(dut)
    pkts = packets(beats_per_packet)

    sw = Stopwatch()
    for data, tid, tdest, tuser in pkts:
        src.send_nowait(AxiStreamFrame(data, tid=tid, tdest=tdest, tuser=tuser))
    for data, tid, tdest, tuser in pkts:
        f = await sink.recv()
        assert bytes(f.tdata) == data
        assert (f.tid, f.tdest, f.tuser) == (tid, tdest, tuser), f
    return sw.result(dut, "cocotbext-axi", beats_per_packet, len(pkts),
                     reads="as it comes")


@cocotb.test()
async def bench_ext_packets(dut):
    await with_timeout(_bench_ext(dut, 16), 10, "ms")


@cocotb.test()
async def bench_ext_beats(dut):
    await with_timeout(_bench_ext(dut, 1), 10, "ms")
