"""cocotb tests for axis_byteswap, driven through the AXI4-Stream VIP shims.

Started by test_byteswap_runner.py, not collected by pytest directly (see
pytest.ini): these run inside the simulator.
"""
import random

import cocotb
from cocotb.triggers import ClockCycles, RisingEdge, with_timeout

from axis_vip_dpi import AXIS_MASTER_VIP, AXIS_SLAVE_VIP

# Must match the VIP customisation (tb/gen_vip.tcl) and the shim parameters
# in tb/tb_byteswap.sv; a mismatch is reported field by field by check().
IFACE = dict(tdata_width=32, has_tkeep=True, has_tlast=True, tuser_width=1)
W = IFACE["tdata_width"] // 8
ALL = (1 << W) - 1


# ------------------------------------------------------------ golden model
def byteswap(tdata, tkeep):
    """What the DUT does to one beat: (tdata, tkeep) with bytes mirrored."""
    data = int.from_bytes(tdata.to_bytes(W, "little")[::-1], "little")
    keep = int(f"{tkeep:0{W}b}"[::-1], 2)
    return data, keep


def data_bytes(tdata, tkeep):
    """Only the bytes TKEEP marks: the VIP drives noise on null bytes."""
    b = tdata.to_bytes(W, "little")
    return bytes(b[i] for i in range(W) if tkeep >> i & 1)


# ----------------------------------------------------------------- helpers
async def setup(dut):
    dut.aresetn.value = 0
    await ClockCycles(dut.aclk, 20)               # VIP wants >= 16 cycles
    dut.aresetn.value = 1
    await ClockCycles(dut.aclk, 5)
    src = AXIS_MASTER_VIP("src", dut.src_shim, **IFACE)   # name == shim NAME
    sink = AXIS_SLAVE_VIP("sink", dut.sink_shim, **IFACE)
    src.check()
    sink.check()
    await RisingEdge(dut.aclk)
    return src, sink


def random_packet(rng, n_beats):
    """Beats as dicts for put_transaction(); last beat may be partial."""
    beats = []
    for i in range(n_beats):
        last = i == n_beats - 1
        keep = (1 << rng.randint(1, W)) - 1 if last else ALL
        beats.append(dict(tdata=rng.getrandbits(8 * W), tkeep=keep,
                          tlast=last, tuser=rng.getrandbits(1)))
    return beats


async def send_and_check(src, sink, beats, timeout_us=200):
    for b in beats:
        src.put_transaction(**b)
    for i, b in enumerate(beats):
        t = await with_timeout(sink.get_transaction(), timeout_us, "us")
        data, keep = byteswap(b["tdata"], b["tkeep"])
        assert t.tkeep == keep, f"beat {i}: tkeep {t.tkeep:#x} != {keep:#x}"
        assert data_bytes(t.tdata, t.tkeep) == data_bytes(data, keep), \
            f"beat {i}: tdata {t.tdata:#010x} != {data:#010x} (keep {keep:#x})"
        assert t.tlast == b["tlast"], f"beat {i}: tlast"
        assert t.tuser == b["tuser"], f"beat {i}: tuser"
        assert t.tstrb is None and t.tid is None and t.tdest is None
    await src.wait_idle()


# ------------------------------------------------------------------- tests
@cocotb.test()
async def test_single_beat(dut):
    src, sink = await setup(dut)
    await send_and_check(src, sink, [dict(tdata=0x04030201, tkeep=ALL,
                                          tlast=True, tuser=1)])


@cocotb.test()
async def test_partial_last_beat(dut):
    src, sink = await setup(dut)
    # 3 valid bytes in, mirrored: TKEEP 0b0111 comes out as 0b1110
    await send_and_check(src, sink, [
        dict(tdata=0xAABBCCDD, tkeep=ALL, tlast=False, tuser=0),
        dict(tdata=0x00332211, tkeep=0b0111, tlast=True, tuser=0),
    ])


@cocotb.test()
async def test_random_packets(dut):
    src, sink = await setup(dut)
    rng = random.Random(1)
    beats = [b for _ in range(50) for b in random_packet(rng, rng.randint(1, 16))]
    await send_and_check(src, sink, beats)


@cocotb.test()
async def test_random_packets_backpressure(dut):
    src, sink = await setup(dut)
    sink.set_backpressure("random", low=(0, 6), high=(1, 4))
    rng = random.Random(2)
    beats = [b for _ in range(50) for b in random_packet(rng, rng.randint(1, 16))]
    await send_and_check(src, sink, beats)
