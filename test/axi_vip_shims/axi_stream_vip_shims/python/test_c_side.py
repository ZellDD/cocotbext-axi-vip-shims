"""Simulator-free test of axis_dpi.cpp + axis_vip_dpi.py.

Loads lib/libaxis_dpi_test.so: axis_dpi.cpp linked with test_c_side_mock.cpp,
which plays the simulator and the SV shims (per-instance queues behind the
exported functions, and a svSetScope that must have selected the right
instance). Build it first, then run:

    cmake -B build && cmake --build build      (from the repo root)
    python3 test_c_side.py        (needs cocotb importable, no simulator)

This checks the C trampoline, scope handling, vector packing and the Python
API. It does not check the SV; see the slang elaboration check for that.
"""
import os
import random
import struct
import sys
from ctypes import POINTER, byref, c_int, c_uint32, c_void_p
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
SHIM = os.path.join(REPO, "src", "axi_vip_shims", "axi_stream_vip_shim", "src")
sys.path.insert(0, os.path.join(SHIM, "python"))

LIB = os.environ.get("AXIS_DPI_TEST_LIB",
                     os.path.join(REPO, "lib", "libaxis_dpi_test.so"))
if not os.path.isfile(LIB):
    raise SystemExit(f"{LIB} not found -- build it with "
                     "'cmake -B build && cmake --build build'")

from axis_vip_dpi import AXIS_MASTER_VIP, AXIS_SLAVE_VIP, load_lib  # noqa: E402

lib = load_lib(LIB)
vp, ip = POINTER(c_uint32), POINTER(c_int)


def proto(name, res, *args):
    f = getattr(lib, name)
    f.restype, f.argtypes = res, list(args)


proto("mock_new_scope", c_void_p, c_int)
proto("mock_set_registering", None, c_void_p)
proto("mock_set_current", None, c_void_p)
proto("mock_get_current", c_void_p)
proto("mock_mst_pop", c_int, c_void_p, vp, vp, vp, vp, ip, ip, ip, ip)
proto("mock_mst_done", None, c_void_p, c_int)
proto("mock_outstanding", c_int, c_void_p)
proto("mock_slv_push", None, c_void_p, vp, vp, vp, vp, c_int, c_int, c_int)
proto("mock_slv_count", c_int, c_void_p)
proto("mock_slv_bp", None, c_void_p, ip)

ALL_ONES = (1 << 4096) - 1


# ----- helpers ----------------------------------------------------------------
class Sig:
    """Stands in for a cocotb signal handle: .value reads the mock."""
    def __init__(self, fn):
        self._fn = fn

    @property
    def value(self):
        return self._fn()


def new_master(name, register=True, **kw):
    scope = lib.mock_new_scope(0)
    shim = SimpleNamespace(outstanding=Sig(lambda: lib.mock_outstanding(scope)))
    vip = AXIS_MASTER_VIP(name, shim, lib_path=LIB, **kw)
    if register:
        sv_register(vip, scope)
    return vip, scope


def new_slave(name, register=True, **kw):
    scope = lib.mock_new_scope(1)
    shim = SimpleNamespace(rx_count=Sig(lambda: lib.mock_slv_count(scope)))
    vip = AXIS_SLAVE_VIP(name, shim, lib_path=LIB, **kw)
    if register:
        sv_register(vip, scope)
    return vip, scope


def sv_register(vip, scope, cfg=None):
    """What the shim's initial block does (context import -> svGetScope)."""
    lib.mock_set_registering(scope)
    inst = lib.axis_dpi_sv_register(vip.name.encode(), *(cfg or vip._cfg))
    assert inst == vip._inst
    lib.mock_set_registering(None)


def to_vec(v):
    return (c_uint32 * 128)(*struct.unpack("<128I", v.to_bytes(512, "little")))


def from_vec(a):
    return int.from_bytes(struct.pack("<128I", *a), "little")


def sv_pop(scope):
    """The VIP driver taking the next beat out of the shim's queue."""
    arrs = [to_vec(ALL_ONES) for _ in range(4)]
    last, id_, dest, delay = (c_int() for _ in range(4))
    if not lib.mock_mst_pop(scope, *arrs, byref(last), byref(id_),
                            byref(dest), byref(delay)):
        return None
    return dict(data=from_vec(arrs[0]), keep=from_vec(arrs[1]),
                strb=from_vec(arrs[2]), user=from_vec(arrs[3]),
                last=last.value, id=id_.value, dest=dest.value,
                delay=delay.value)


def sv_rx_push(scope, data, keep=0, strb=0, user=0, last=0, id_=0, dest=0):
    """The VIP monitor delivering a received beat into the shim's queue."""
    lib.mock_slv_push(scope, to_vec(data), to_vec(keep), to_vec(strb),
                      to_vec(user), last, id_, dest)


def sv_bp(scope):
    out = (c_int * 6)()
    lib.mock_slv_bp(scope, out)
    return tuple(out)          # mode low_min low_max high_min high_max version


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc as e:
        return str(e) or True
    return False


FULL = dict(tdata_width=32, has_tkeep=True, has_tlast=True,
            tid_width=4, tdest_width=4, tuser_width=12)


# ----- tests --------------------------------------------------------------------
def test_signature_checker():
    m, sc = new_master("sig", **FULL)
    assert raises(TypeError, m.put_transaction, 1)                   # no tlast
    assert raises(TypeError, m.put_transaction, 1, tlast=True, tstrb=1)
    assert raises(ValueError, m.put_transaction, 1 << 32, tlast=True)
    assert raises(ValueError, m.put_transaction, 1, tlast=True, tid=16)
    assert raises(ValueError, m.put_transaction, 1, tlast=True, tdest=16)
    assert raises(ValueError, m.put_transaction, 1, tlast=True, tuser=1 << 12)
    assert raises(ValueError, m.put_transaction, 1, tlast=True, tkeep=16)
    assert raises(ValueError, m.put_transaction, b"abc", tlast=True)  # 3 != 4 B
    assert raises(ValueError, m.put_transaction, 1, tlast=True, delay=-1)
    assert m.outstanding == 0, "rejected calls must not queue anything"

    # an interface without TLAST/TKEEP/sideband rejects those arguments
    n, _ = new_master("sig_min", tdata_width=8)
    assert raises(TypeError, n.put_transaction, 1, tlast=True)
    assert raises(TypeError, n.put_transaction, 1, tkeep=1)
    assert raises(TypeError, n.put_transaction, 1, tid=0)
    n.put_transaction(0xAA)                                          # fine

    # constructor argument validation
    assert raises(ValueError, AXIS_MASTER_VIP, "bad1",
                  SimpleNamespace(outstanding=0), tdata_width=12)
    assert raises(ValueError, AXIS_MASTER_VIP, "bad2",
                  SimpleNamespace(outstanding=0), tdata_width=8192)
    assert raises(ValueError, AXIS_MASTER_VIP, "bad3",
                  SimpleNamespace(outstanding=0), tdata_width=8, tid_width=33)
    # wrong handle (no status signal)
    assert raises(TypeError, AXIS_MASTER_VIP, "bad4", SimpleNamespace(),
                  tdata_width=8)


def test_roundtrip_exactly_as_given():
    m, sc = new_master("m1", **FULL)
    assert sv_pop(sc) is None

    m.put_transaction(0xDEADBEEF, tlast=False, tid=3, tdest=5, tuser=0xABC, delay=2)
    m.put_transaction(0x01020304, tlast=True, tkeep=0b0110)
    m.put_transaction(bytes.fromhex("11223344"), tlast=False)   # bytes form
    assert m.outstanding == 3

    a = sv_pop(sc)
    assert (a["data"], a["last"], a["id"], a["dest"], a["user"], a["delay"]) == \
           (0xDEADBEEF, 0, 3, 5, 0xABC, 2)
    assert a["keep"] == 0b1111, "omitted tkeep defaults to all bytes"
    assert a["strb"] == 0, "TSTRB is not in this interface"
    b = sv_pop(sc)
    assert (b["data"], b["last"], b["keep"], b["id"], b["user"]) == \
           (0x01020304, 1, 0b0110, 0, 0)
    c = sv_pop(sc)
    assert c["data"] == 0x44332211 and c["last"] == 0
    assert sv_pop(sc) is None

    assert m.outstanding == 3                    # popped, VIP not done yet
    lib.mock_mst_done(sc, 2)
    assert m.outstanding == 1
    lib.mock_mst_done(sc, 1)
    assert m.outstanding == 0


def test_minimal_interface():
    m, sc = new_master("m_min", tdata_width=8)
    m.put_transaction(0x5A)                      # no tlast needed: no TLAST
    p = sv_pop(sc)
    assert (p["data"], p["last"], p["keep"], p["user"]) == (0x5A, 0, 0, 0)


def test_wide_signals():
    kw = dict(tdata_width=512, has_tkeep=True, has_tstrb=True, has_tlast=True,
              tid_width=8, tdest_width=4, tuser_width=70)
    m, sc = new_master("m_wide", **kw)
    rng = random.Random(7)
    for _ in range(50):
        v = dict(tdata=rng.getrandbits(512), tkeep=rng.getrandbits(64),
                 tstrb=rng.getrandbits(64), tuser=rng.getrandbits(70),
                 tid=rng.getrandbits(8), tdest=rng.getrandbits(4),
                 tlast=rng.random() < 0.5)
        m.put_transaction(**v)
        p = sv_pop(sc)
        assert (p["data"], p["keep"], p["strb"], p["user"], p["id"], p["dest"],
                bool(p["last"])) == (v["tdata"], v["tkeep"], v["tstrb"],
                                     v["tuser"], v["tid"], v["tdest"], v["tlast"])


def test_config_checker():
    # shim not registered yet
    m, sc = new_master("cfg_a", register=False, **FULL)
    assert "not registered" in raises(RuntimeError, m.put_transaction, 1, tlast=True)
    assert "not registered" in raises(RuntimeError, m.check)

    # shim has a different interface than the constructor says
    sv_register(m, sc, cfg=(0, 8, 0, 0, 0, 4, 4, 12))
    msg = raises(RuntimeError, m.put_transaction, 1, tlast=True)
    assert "tdata_bytes" in msg and "has_tkeep" in msg and "has_tlast" in msg, msg
    assert "tid_width" not in msg and "tuser_width" not in msg, msg
    assert m.outstanding == 0

    # matching description passes
    ok, _ = new_master("cfg_b", **FULL)
    ok.check()

    # wrong role: a slave object on a master shim (same name)
    wrong, _ = new_slave("cfg_b", register=False, **FULL)
    assert "role" in raises(RuntimeError, wrong.check)


def test_scope_selects_the_right_instance():
    a, sa = new_master("iso_a", tdata_width=8)
    b, sb = new_master("iso_b", tdata_width=8)
    a.put_transaction(0x11)
    a.put_transaction(0x12)
    b.put_transaction(0x21)
    assert [sv_pop(sa)["data"], sv_pop(sa)["data"], sv_pop(sa)] == [0x11, 0x12, None]
    assert [sv_pop(sb)["data"], sv_pop(sb)] == [0x21, None]

    s1, ss1 = new_slave("iso_s1", tdata_width=8)
    s2, ss2 = new_slave("iso_s2", tdata_width=8)
    sv_rx_push(ss1, 0xA1)
    sv_rx_push(ss2, 0xB1)
    assert s1.try_get_transaction().tdata == 0xA1
    assert s2.try_get_transaction().tdata == 0xB1
    assert s1.try_get_transaction() is None and s2.try_get_transaction() is None

    # a scope that was current before the call (call made from inside some DPI
    # context) must be current again afterwards
    other = lib.mock_new_scope(0)
    lib.mock_set_current(other)
    a.put_transaction(0x13)
    s1.set_backpressure("stall")
    s1.try_get_transaction()
    a.flush()
    assert lib.mock_get_current() == other
    lib.mock_set_current(None)


def test_master_random_stress():
    m, sc = new_master("m2", tdata_width=64, has_tlast=True, tid_width=5)
    rng = random.Random(1)
    sent = []
    for n in range(500):
        v = (rng.getrandbits(64), rng.random() < 0.3, rng.getrandbits(5))
        sent.append(v)
        m.put_transaction(v[0], tlast=v[1], tid=v[2], delay=n & 3)
    for n, v in enumerate(sent):
        p = sv_pop(sc)
        assert (p["data"], bool(p["last"]), p["id"], p["delay"]) == \
               (v[0], v[1], v[2], n & 3)
    assert sv_pop(sc) is None


def test_slave_rx():
    s, sc = new_slave("s1", **FULL)
    assert s.try_get_transaction() is None and s.pending == 0

    sv_rx_push(sc, 0x12345678, keep=0b1010, user=0x123, last=1, id_=4, dest=9)
    sv_rx_push(sc, 0x0, keep=0b1111, last=0)                  # zero data ok
    assert s.pending == 2

    t = s.try_get_transaction()
    assert t == (0x12345678, 0b1010, None, True, 4, 9, 0x123)     # tstrb absent
    t = s.try_get_transaction()
    assert t.tdata == 0 and t.tlast is False and t.tkeep == 0b1111
    assert s.try_get_transaction() is None and s.pending == 0


def test_slave_absent_signals_are_none():
    s, sc = new_slave("s2", tdata_width=16)
    sv_rx_push(sc, 0xBEEF, keep=0b11, last=1, id_=7, dest=7, user=7)   # junk
    t = s.try_get_transaction()
    assert t == (0xBEEF, None, None, None, None, None, None)


def test_slave_wide():
    kw = dict(tdata_width=1024, has_tkeep=True, has_tstrb=True, has_tlast=True,
              tid_width=8, tuser_width=130)
    s, sc = new_slave("s3", **kw)
    rng = random.Random(3)
    for _ in range(20):
        v = (rng.getrandbits(1024), rng.getrandbits(128), rng.getrandbits(128),
             rng.random() < 0.5, rng.getrandbits(8), None, rng.getrandbits(130))
        sv_rx_push(sc, v[0], v[1], v[2], v[6], int(v[3]), v[4], 0)
        assert s.try_get_transaction() == (v[0], v[1], v[2], v[3], v[4], None, v[6])


def test_backpressure():
    s, sc = new_slave("s4", tdata_width=8)
    assert sv_bp(sc) == (0, 0, 0, 1, 1, 0)           # default: none

    s.set_backpressure("osc", low=3, high=1)
    assert sv_bp(sc) == (1, 3, 3, 1, 1, 1)
    s.set_backpressure("random", low=(0, 8), high=(1, 4))
    assert sv_bp(sc) == (2, 0, 8, 1, 4, 2)
    s.stall()
    assert sv_bp(sc)[0] == 3
    s.release()
    assert sv_bp(sc)[0] == 0 and sv_bp(sc)[5] == 4

    assert raises(ValueError, s.set_backpressure, "bogus")
    assert raises(ValueError, s.set_backpressure, "random", low=(5, 2))
    assert raises(ValueError, s.set_backpressure, "osc", low=-1)
    assert sv_bp(sc)[5] == 4                         # rejected = no change


def test_flush():
    m, msc = new_master("m3", tdata_width=8)
    s, ssc = new_slave("s5", tdata_width=8)
    for i in (1, 2, 3):
        m.put_transaction(i)
    sv_pop(msc)                                       # one already taken by VIP
    assert m.outstanding == 3
    m.flush()
    assert m.outstanding == 1                         # only the in-flight one
    assert sv_pop(msc) is None
    sv_rx_push(ssc, 0xAB)
    s.flush()
    assert s.try_get_transaction() is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  ", name)
    print("all passed")
