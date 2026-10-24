import cocotb
from cocotb.triggers import ClockCycles, with_timeout

from axis_vip_dpi import AXIS_MASTER_VIP, AXIS_SLAVE_VIP

# Interface description -- must match tb_top.sv / the VIP customisation.
IFACE = dict(tdata_width=32, has_tkeep=True, has_tlast=True,
             tid_width=4, tdest_width=4, tuser_width=4)
W = IFACE["tdata_width"] // 8


async def reset(dut, cycles=10):
    dut.aresetn.value = 0
    await ClockCycles(dut.aclk, cycles)
    dut.aresetn.value = 1
    await ClockCycles(dut.aclk, 5)


async def rx(slv, us=200):
    """get_transaction() with a simulation-time timeout."""
    return await with_timeout(slv.get_transaction(), us, "us")


# Packet handling is a testbench decision, so it lives here, not in the shim.
def put_packet(mst, data: bytes, **sideband):
    beats = [data[i:i + W] for i in range(0, len(data), W)]
    for n, chunk in enumerate(beats):
        mst.put_transaction(
            tdata=int.from_bytes(chunk, "little"),
            tkeep=(1 << len(chunk)) - 1,          # partial last beat
            tlast=(n == len(beats) - 1),
            **sideband)


async def get_packet(slv, us=200):
    out = bytearray()
    while True:
        t = await rx(slv, us)
        raw = t.tdata.to_bytes(W, "little")
        out += bytes(b for i, b in enumerate(raw) if (t.tkeep >> i) & 1)
        if t.tlast:
            return bytes(out)


@cocotb.test()
async def test_beats_and_backpressure(dut):
    await reset(dut)
    mst = AXIS_MASTER_VIP("mst0", dut.mst_shim, **IFACE)
    slv = AXIS_SLAVE_VIP("slv0", dut.slv_shim, **IFACE)
    mst.check()
    slv.check()

    # 1) every signal comes back exactly as given, including tlast=False
    mst.put_transaction(tdata=0x11111111, tlast=False, tid=2, tdest=1, tuser=5)
    mst.put_transaction(tdata=0x22222222, tlast=False, tkeep=0b0110)
    mst.put_transaction(tdata=0x33333333, tlast=True, tid=3)
    t1, t2, t3 = [await rx(slv) for _ in range(3)]
    assert (t1.tdata, t1.tlast, t1.tid, t1.tdest, t1.tuser, t1.tkeep) == \
           (0x11111111, False, 2, 1, 5, 0b1111)       # tkeep defaulted
    assert (t2.tdata, t2.tlast, t2.tkeep) == (0x22222222, False, 0b0110)
    assert (t3.tdata, t3.tlast, t3.tid) == (0x33333333, True, 3)

    # 2) packets are built by the test from beats
    data = bytes(range(22))
    put_packet(mst, data, tid=3, tdest=1)
    assert await get_packet(slv) == data

    # 3) change backpressure at runtime -- plain calls
    data = bytes(range(64))
    slv.set_backpressure("osc", low=3, high=1)
    put_packet(mst, data)
    assert await get_packet(slv) == data

    slv.set_backpressure("random", low=(0, 8), high=(1, 4))
    put_packet(mst, data)
    assert await get_packet(slv) == data

    # 4) switch the pattern while a packet is in flight
    big = bytes(i & 0xFF for i in range(256))
    slv.release()
    put_packet(mst, big)
    await ClockCycles(dut.aclk, 10)
    slv.set_backpressure("osc", low=8, high=1)
    assert await get_packet(slv, us=500) == big

    # 5) full stall, then release
    slv.stall()
    await ClockCycles(dut.aclk, 5)
    put_packet(mst, data)
    await ClockCycles(dut.aclk, 50)
    assert slv.pending == 0, "data got through while TREADY was held low"
    assert mst.outstanding > 0
    slv.release()
    assert await get_packet(slv) == data
    await with_timeout(mst.wait_idle(), 100, "us")
    assert mst.outstanding == 0


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
    assert raises(TypeError, tdata=1, tlast=True, tstrb=1)     # no TSTRB here
    assert raises(ValueError, tdata=1 << 32, tlast=True)       # too wide
    assert raises(ValueError, tdata=1, tlast=True, tid=16)     # 4-bit TID
    assert raises(ValueError, tdata=1, tlast=True, tkeep=16)   # 4 bytes
    assert mst.outstanding == 0                                # nothing queued


# Keep this test last: it re-registers "mst0" with a deliberately wrong
# description, which the first call must reject.
@cocotb.test()
async def test_constructor_vs_shim_mismatch(dut):
    bad = AXIS_MASTER_VIP("mst0", dut.mst_shim, tdata_width=64, has_tlast=False)
    try:
        bad.put_transaction(tdata=1)
    except RuntimeError as e:
        assert "tdata_bytes" in str(e) and "has_tlast" in str(e), e
    else:
        raise AssertionError("mismatch was not detected")
