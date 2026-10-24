"""Functional tests: cocotb -> master shim -> Xilinx VIP master -> bus ->
Xilinx VIP slave -> slave shim -> cocotb, one test per AXI4-Stream option.

Each test runs in its own simulation (see test_runner.py). Besides comparing
what comes out with what went in, `Bus` watches TVALID/TREADY on the wires,
so delays and backpressure are checked as they really happen on the bus.
"""
import random

import cocotb
from cocotb.triggers import ClockCycles, FallingEdge, with_timeout

from axis_vip_dpi import AXIS_MASTER_VIP, AXIS_SLAVE_VIP

# Interface description -- must match tb_top.sv / the VIP customisation.
IFACE = dict(tdata_width=32, has_tkeep=True, has_tstrb=True, has_tlast=True,
             tid_width=4, tdest_width=4, tuser_width=4)
W = IFACE["tdata_width"] // 8
ALL = (1 << W) - 1                        # TKEEP/TSTRB with every byte set


# ----------------------------------------------------------------- helpers
async def reset(dut, cycles=20):          # VIP wants >= 16 cycles
    dut.aresetn.value = 0
    await ClockCycles(dut.aclk, cycles)
    dut.aresetn.value = 1
    await ClockCycles(dut.aclk, 5)


def _bit(sig):
    v = sig.value
    return int(v) if v.is_resolvable else 0


class Bus:
    """Watches the wires mid-cycle (falling edge): a cycle with TVALID and
    TREADY high is a handshake, TVALID without TREADY is a stall cycle."""

    def __init__(self, dut):
        self.dut = dut
        self.cycle = 0
        self.handshakes = []              # cycle numbers
        self.stalls = 0
        cocotb.start_soon(self._run())

    async def _run(self):
        while True:
            await FallingEdge(self.dut.aclk)
            self.cycle += 1
            valid, ready = _bit(self.dut.tvalid), _bit(self.dut.tready)
            if valid and ready:
                self.handshakes.append(self.cycle)
            elif valid:
                self.stalls += 1

    def gaps(self, first=0):
        """Cycles between consecutive handshakes, from handshake `first` on."""
        h = self.handshakes[first:]
        return [b - a for a, b in zip(h, h[1:])]


async def setup(dut):
    await reset(dut)
    mst = AXIS_MASTER_VIP("mst0", dut.mst_shim, **IFACE)
    slv = AXIS_SLAVE_VIP("slv0", dut.slv_shim, **IFACE)
    mst.check()
    slv.check()
    return mst, slv, Bus(dut)


async def rx(slv, us=200):
    """get_transaction() with a simulation-time timeout."""
    return await with_timeout(slv.get_transaction(), us, "us")


async def rx_n(slv, n, us=200):
    return [await rx(slv, us) for _ in range(n)]


def byte_mask(bits):
    """TKEEP/TSTRB-style byte mask -> bit mask over TDATA."""
    return sum(0xFF << (8 * i) for i in range(W) if (bits >> i) & 1)


def data_bytes(t):
    """Only data bytes (TKEEP=1 and TSTRB=1) are defined: the VIP drives
    noise on null (TKEEP=0) and position (TSTRB=0) bytes."""
    return t.tdata & byte_mask(t.tkeep & t.tstrb)


# Packet handling is a testbench decision, so it lives here, not in the shim.
def put_packet(mst, data: bytes, **sideband):
    beats = [data[i:i + W] for i in range(0, len(data), W)]
    for n, chunk in enumerate(beats):
        mst.put_transaction(
            tdata=int.from_bytes(chunk, "little"),
            tkeep=(1 << len(chunk)) - 1,          # partial last beat
            tstrb=(1 << len(chunk)) - 1,          # TSTRB=1 needs TKEEP=1
            tlast=(n == len(beats) - 1),
            **sideband)
    return len(beats)


async def get_packet(slv, us=200):
    out = bytearray()
    while True:
        t = await rx(slv, us)
        raw = t.tdata.to_bytes(W, "little")
        out += bytes(b for i, b in enumerate(raw) if (t.tkeep >> i) & 1)
        if t.tlast:
            return bytes(out)


# ------------------------------------------------------------------ signals
@cocotb.test()
async def test_tdata(dut):
    """Every bit of TDATA, as int and as bytes."""
    mst, slv, bus = await setup(dut)
    rng = random.Random(1)
    values = ([0, ALL_BITS := (1 << 8 * W) - 1, 0xA5A5A5A5, 0x5A5A5A5A]
              + [1 << i for i in range(8 * W)]               # walking one
              + [ALL_BITS ^ (1 << i) for i in range(8 * W)]  # walking zero
              + [rng.getrandbits(8 * W) for _ in range(32)])
    for v in values:
        mst.put_transaction(tdata=v, tlast=True)
    mst.put_transaction(tdata=bytes([1, 2, 3, 4]), tlast=True)   # little endian
    got = [t.tdata for t in await rx_n(slv, len(values) + 1)]
    assert got == values + [0x04030201]
    assert len(bus.handshakes) == len(values) + 1


@cocotb.test()
async def test_tkeep(dut):
    """Every TKEEP value (TSTRB = TKEEP, so all kept bytes are data bytes),
    including sparse and all-null beats; omitted TKEEP means all bytes."""
    mst, slv, _ = await setup(dut)
    rng = random.Random(2)
    sent = []
    for keep in range(1 << W):
        v = rng.getrandbits(8 * W)
        mst.put_transaction(tdata=v, tkeep=keep, tstrb=keep, tlast=True)
        sent.append((v, keep))
    mst.put_transaction(tdata=0x12345678, tlast=True)            # TKEEP omitted
    for (v, keep), t in zip(sent, await rx_n(slv, len(sent))):
        assert (t.tkeep, t.tstrb) == (keep, keep), (keep, t)
        assert data_bytes(t) == v & byte_mask(keep), (keep, t)
    t = await rx(slv)
    assert (t.tdata, t.tkeep, t.tstrb) == (0x12345678, ALL, ALL)


@cocotb.test()
async def test_tstrb(dut):
    """Every TSTRB value with all bytes kept: TSTRB=0 marks position bytes;
    omitted TSTRB means all bytes are data bytes."""
    mst, slv, _ = await setup(dut)
    rng = random.Random(3)
    sent = []
    for strb in range(1 << W):
        v = rng.getrandbits(8 * W)
        mst.put_transaction(tdata=v, tkeep=ALL, tstrb=strb, tlast=True)
        sent.append((v, strb))
    mst.put_transaction(tdata=0x0BADF00D, tlast=True)            # TSTRB omitted
    for (v, strb), t in zip(sent, await rx_n(slv, len(sent))):
        assert (t.tkeep, t.tstrb) == (ALL, strb), (strb, t)
        assert data_bytes(t) == v & byte_mask(strb), (strb, t)
    t = await rx(slv)
    assert (t.tdata, t.tstrb) == (0x0BADF00D, ALL)


@cocotb.test()
async def test_tlast(dut):
    """TLAST exactly as given on every beat, and packets of 1..9 beats with
    a partial last beat."""
    mst, slv, _ = await setup(dut)
    rng = random.Random(4)
    pattern = [rng.random() < 0.3 for _ in range(40)] + [True]
    for i, last in enumerate(pattern):
        mst.put_transaction(tdata=i, tlast=last)
    got = await rx_n(slv, len(pattern))
    assert [t.tlast for t in got] == pattern
    assert [t.tdata for t in got] == list(range(len(pattern)))

    for nbytes in range(1, 9 * W, 3):
        data = bytes(rng.getrandbits(8) for _ in range(nbytes))
        put_packet(mst, data)
        assert await get_packet(slv) == data, nbytes


async def _sideband(dut, name, width):
    """Every value of one sideband signal; omitted means 0."""
    mst, slv, _ = await setup(dut)
    values = list(range(1 << width))
    for v in values:
        mst.put_transaction(tdata=v, tlast=True, **{name: v})
    mst.put_transaction(tdata=0, tlast=True)                     # omitted
    got = await rx_n(slv, len(values) + 1)
    assert [getattr(t, name) for t in got] == values + [0]
    assert [t.tdata for t in got[:-1]] == values                 # still in order


@cocotb.test()
async def test_tid(dut):
    await _sideband(dut, "tid", IFACE["tid_width"])


@cocotb.test()
async def test_tdest(dut):
    await _sideband(dut, "tdest", IFACE["tdest_width"])


@cocotb.test()
async def test_tuser(dut):
    await _sideband(dut, "tuser", IFACE["tuser_width"])


@cocotb.test()
async def test_all_signals_random(dut):
    """Every signal random at once, under random backpressure."""
    mst, slv, _ = await setup(dut)
    rng = random.Random(5)
    slv.set_backpressure("random", low=(0, 4), high=(1, 4))
    sent = []
    for _ in range(200):
        keep = rng.getrandbits(W)
        b = dict(tdata=rng.getrandbits(8 * W), tkeep=keep,
                 tstrb=keep & rng.getrandbits(W),               # TSTRB only where kept
                 tlast=rng.random() < 0.25,
                 tid=rng.getrandbits(IFACE["tid_width"]),
                 tdest=rng.getrandbits(IFACE["tdest_width"]),
                 tuser=rng.getrandbits(IFACE["tuser_width"]))
        mst.put_transaction(**b)
        sent.append(b)
    for b, t in zip(sent, await rx_n(slv, len(sent), us=500)):
        assert (t.tkeep, t.tstrb, t.tlast, t.tid, t.tdest, t.tuser) == \
               (b["tkeep"], b["tstrb"], b["tlast"], b["tid"], b["tdest"], b["tuser"]), (b, t)
        assert data_bytes(t) == b["tdata"] & byte_mask(b["tkeep"] & b["tstrb"]), (b, t)


# -------------------------------------------------------------------- delay
@cocotb.test()
async def test_delay(dut):
    """delay=N puts N idle cycles before the beat's TVALID."""
    mst, slv, bus = await setup(dut)
    delays = [0, 1, 2, 3, 5, 8, 13, 0, 0]
    mst.put_transaction(tdata=0xFF, tlast=True)                  # reference beat
    for i, d in enumerate(delays):
        mst.put_transaction(tdata=i, tlast=True, delay=d)
    await rx_n(slv, len(delays) + 1)
    assert bus.gaps() == [d + 1 for d in delays], bus.gaps()
    assert bus.stalls == 0                                       # no backpressure


# ------------------------------------------------------------- backpressure
N_BP = 48                                 # beats per backpressure test


@cocotb.test()
async def test_backpressure_none(dut):
    """Default: TREADY always high, beats go back to back."""
    mst, slv, bus = await setup(dut)
    for i in range(N_BP):
        mst.put_transaction(tdata=i, tlast=True)
    assert [t.tdata for t in await rx_n(slv, N_BP)] == list(range(N_BP))
    assert bus.gaps() == [1] * (N_BP - 1), bus.gaps()
    assert bus.stalls == 0


@cocotb.test()
async def test_backpressure_osc(dut):
    """"osc": TREADY low for `low` cycles, then high for `high`, repeating."""
    mst, slv, bus = await setup(dut)
    for low, high in ((3, 1), (2, 2), (1, 3)):
        start = len(bus.handshakes)
        slv.set_backpressure("osc", low=low, high=high)
        await ClockCycles(dut.aclk, 5)                           # applied
        for i in range(N_BP):
            mst.put_transaction(tdata=i, tlast=True)
        assert [t.tdata for t in await rx_n(slv, N_BP)] == list(range(N_BP))
        # `high` back-to-back handshakes, then `low` cycles without. The
        # first period can start anywhere: line up at the first long gap.
        gaps = bus.gaps(start)
        gaps = gaps[gaps.index(low + 1) + 1:]
        expect = ([1] * (high - 1) + [low + 1]) * N_BP
        assert len(gaps) > N_BP - 2 * (low + high), gaps
        assert gaps == expect[:len(gaps)], (low, high, gaps)


@cocotb.test()
async def test_backpressure_random(dut):
    """"random": TREADY low/high times drawn from the given ranges."""
    mst, slv, bus = await setup(dut)
    low, high = (2, 6), (1, 3)
    slv.set_backpressure("random", low=low, high=high)
    await ClockCycles(dut.aclk, 5)
    for i in range(N_BP):
        mst.put_transaction(tdata=i, tlast=True)
    assert [t.tdata for t in await rx_n(slv, N_BP)] == list(range(N_BP))
    gaps = bus.gaps()
    assert bus.stalls > 0, "no backpressure seen"
    assert max(gaps) <= low[1] + 2, gaps                        # bounded low time
    assert len(set(gaps)) > 1, f"not random: {gaps}"


@cocotb.test()
async def test_backpressure_stall_release(dut):
    """"stall" holds TREADY low until the next change; release() resumes."""
    mst, slv, bus = await setup(dut)
    slv.stall()
    await ClockCycles(dut.aclk, 5)
    for i in range(8):
        mst.put_transaction(tdata=i, tlast=True)
    await ClockCycles(dut.aclk, 100)
    assert bus.handshakes == [], "data got through while stalled"
    assert bus.stalls > 90                                       # TVALID waiting
    assert slv.pending == 0 and mst.outstanding == 8
    slv.release()
    assert [t.tdata for t in await rx_n(slv, 8)] == list(range(8))


@cocotb.test()
async def test_backpressure_change_in_flight(dut):
    """Changing the pattern while a packet is on the bus loses nothing."""
    mst, slv, bus = await setup(dut)
    big = bytes(i & 0xFF for i in range(256))
    put_packet(mst, big)
    await ClockCycles(dut.aclk, 10)
    slv.set_backpressure("osc", low=8, high=1)
    await ClockCycles(dut.aclk, 50)
    slv.set_backpressure("random", low=(0, 4), high=(1, 2))
    await ClockCycles(dut.aclk, 50)
    slv.release()
    assert await get_packet(slv, us=500) == big
    assert len(bus.handshakes) == len(big) // W


# ------------------------------------------------------- flush / wait_idle
@cocotb.test()
async def test_flush_master(dut):
    """flush() on the master drops only what the VIP has not taken yet:
    everything still counted in `outstanding` afterwards does arrive."""
    mst, slv, bus = await setup(dut)
    slv.stall()
    await ClockCycles(dut.aclk, 5)
    for i in range(16):
        mst.put_transaction(tdata=i, tlast=True)
    await ClockCycles(dut.aclk, 5)
    mst.flush()
    await ClockCycles(dut.aclk, 1)
    left = mst.outstanding
    assert 0 <= left <= 16
    slv.release()
    got = [t.tdata for t in await rx_n(slv, left)]
    assert got == list(range(left))
    await ClockCycles(dut.aclk, 50)
    assert slv.try_get_transaction() is None and len(bus.handshakes) == left


@cocotb.test()
async def test_flush_slave(dut):
    """flush() on the slave drops received beats not read yet."""
    mst, slv, bus = await setup(dut)
    for i in range(5):
        mst.put_transaction(tdata=i, tlast=True)
    await with_timeout(mst.wait_idle(), 100, "us")
    await ClockCycles(dut.aclk, 5)
    assert slv.pending == 5
    slv.flush()
    await ClockCycles(dut.aclk, 1)
    assert slv.pending == 0 and slv.try_get_transaction() is None
    mst.put_transaction(tdata=0x55, tlast=True)                  # still works
    assert (await rx(slv)).tdata == 0x55


@cocotb.test()
async def test_wait_idle(dut):
    """wait_idle() returns once everything queued is on the bus."""
    mst, slv, bus = await setup(dut)
    await with_timeout(mst.wait_idle(), 1, "us")                 # idle already
    slv.set_backpressure("osc", low=3, high=1)
    for i in range(16):
        mst.put_transaction(tdata=i, tlast=True)
    await with_timeout(mst.wait_idle(), 100, "us")
    assert mst.outstanding == 0
    assert len(bus.handshakes) == 16
    assert len(await rx_n(slv, 16)) == 16


# ----------------------------------------------------------- API checking
@cocotb.test()
async def test_signature_checker(dut):
    mst = AXIS_MASTER_VIP("mst0", dut.mst_shim, **IFACE)

    def raises(exc, **kw):
        try:
            mst.put_transaction(**kw)
        except exc:
            return True
        return False

    assert raises(TypeError, tdata=1)                          # tlast missing
    assert raises(ValueError, tdata=1 << 32, tlast=True)       # too wide
    assert raises(ValueError, tdata=b"abc", tlast=True)        # 3 bytes, not 4
    assert raises(ValueError, tdata=1, tlast=True, tid=16)     # 4-bit TID
    assert raises(ValueError, tdata=1, tlast=True, tdest=16)   # 4-bit TDEST
    assert raises(ValueError, tdata=1, tlast=True, tuser=16)   # 4-bit TUSER
    assert raises(ValueError, tdata=1, tlast=True, tkeep=16)   # 4 bytes
    assert raises(ValueError, tdata=1, tlast=True, tstrb=16)   # 4 bytes
    assert raises(ValueError, tdata=1, tlast=True, delay=-1)
    assert mst.outstanding == 0                                # nothing queued


# Keep this test last: it re-registers "mst0" with a deliberately wrong
# description, which the first call must reject.
@cocotb.test()
async def test_constructor_vs_shim_mismatch(dut):
    await ClockCycles(dut.aclk, 1)       # let the shim's initial block register
    bad = AXIS_MASTER_VIP("mst0", dut.mst_shim, tdata_width=64, has_tlast=False)
    try:
        bad.put_transaction(tdata=1)
    except RuntimeError as e:
        assert "tdata_bytes" in str(e) and "has_tlast" in str(e), e
    else:
        raise AssertionError("mismatch was not detected")
