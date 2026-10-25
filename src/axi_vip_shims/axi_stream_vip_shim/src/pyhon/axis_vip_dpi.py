"""cocotb front end for the DPI-C version of the AXI4-Stream VIP shim.

One put_transaction() call == one AXI4-Stream transfer. Nothing is added on
your behalf: TLAST, TKEEP, packet boundaries, ... are whatever you pass. The
constructor says which signals the interface has; calls are checked against
that, and the description is verified against the SV shim's parameters on
first use.

    mst = AXIS_MASTER_VIP("mst0", dut.mst_shim, tdata_width=32, has_tkeep=True,
                          has_tlast=True, tid_width=4)
    slv = AXIS_SLAVE_VIP("slv0", dut.slv_shim, tdata_width=32, has_tkeep=True,
                         has_tlast=True, tid_width=4)

    mst.put_transaction(tdata=0x1111, tlast=False, tid=2)    # plain call
    mst.put_transaction(tdata=0x2222, tlast=True, tkeep=0b0011, tid=2)
    slv.set_backpressure("random", low=(0, 8), high=(1, 4))  # plain call
    t = await slv.get_transaction()   # Transaction(tdata, tkeep, ..., tlast)
    await mst.wait_idle()

`name` must equal the shim's NAME parameter; `shim` is the cocotb handle of
the shim instance. Waiting (get_transaction / wait_idle) sleeps on a status
signal the shim drives, so a quiet interface costs no clock-edge wakeups.
There is no timeout argument: wrap with cocotb.triggers.with_timeout().

The library must be the same file the simulator loads with -sv_lib. Point
AXIS_DPI_LIB at it (the Makefile does), or pass lib_path=.
"""
import ctypes
import os
from collections import namedtuple
from ctypes import POINTER, byref, c_char_p, c_int, c_void_p

from cocotb.triggers import ReadOnly, ReadWrite, Timer

MAX_TDATA_BITS = 4096          # = AXIS_VEC_BITS in axis_vip_dpi.sv
_BP_MODES = {"none": 0, "osc": 1, "random": 2, "stall": 3}   # = axis_dpi.cpp
_ROLE_MASTER, _ROLE_SLAVE = 0, 1
_CFG_FIELDS = ("role", "tdata_bytes", "has_tkeep", "has_tstrb", "has_tlast",
               "tid_width", "tdest_width", "tuser_width")

# Fields of signals the interface does not have are None.
Transaction = namedtuple("Transaction",
                         "tdata tkeep tstrb tlast tid tdest tuser")


# --------------------------------------------------------------- load dpi_c
_libs = {}


def load_lib(lib_path=None):
    path =  (
        lib_path or
        os.environ.get("AXIS_DPI_LIB") or
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "libaxis_dpi.so"
        )
    )

    path = os.path.realpath(path)

    if path in _libs:
        return _libs[path]

    # loads contents of axis_dpi.cpp/.so
    lib = ctypes.CDLL(path)
    # integer pointer into the
    ip = POINTER(c_int)

    # defines available functions with associated signatures
    # and registers them in the library object
    def proto(name, res, *args):
        f = getattr(lib, name)
        f.restype, f.argtypes = res, list(args)

    cfg8 = [c_int] * 8
    proto("axis_dpi_open", c_int, c_char_p)
    proto("axis_dpi_py_config", None, c_int, *cfg8)
    proto("axis_dpi_cfg_check", c_int, c_int)
    proto("axis_dpi_sv_cfg_get", None, c_int, ip)
    proto("axis_dpi_flush", c_int, c_int)
    proto("axis_dpi_tx_push", c_int, c_int, c_char_p, c_int, c_char_p, c_int,
          c_char_p, c_int, c_char_p, c_int, c_int, c_int, c_int, c_int)
    proto("axis_dpi_rx_pop", c_int, c_int, c_void_p, c_int, c_void_p, c_int,
          c_void_p, c_int, c_void_p, c_int, ip, ip, ip)
    proto("axis_dpi_bp_set", c_int, c_int, c_int, c_int, c_int, c_int, c_int)
    # called by the SV shim in a real simulation; declared so the unit test can
    # play the shim
    proto("axis_dpi_sv_register", c_int, c_char_p, *cfg8)

    _libs[path] = lib
    return lib


# ---------------------------------------------------------------- common base
class _AxisVip:
    _ROLE = None
    _STATUS = None          # status signal the shim drives

    def __init__(self, name, shim, *, tdata_width, has_tkeep=False,
                 has_tstrb=False, has_tlast=False, tid_width=0, tdest_width=0,
                 tuser_width=0, lib_path=None):
        """Describe the interface. Widths of 0 mean the signal is absent.

        name  : the shim's NAME parameter
        shim  : cocotb handle of the shim instance (e.g. dut.mst_shim)
        tdata_width : bits, multiple of 8, up to 4096
        has_tkeep / has_tstrb / has_tlast : signal present?
        tid_width / tdest_width : bits (0..32)
        tuser_width : bits (0..4096)
        Must match the VIP customisation and the shim's parameters; a mismatch
        is reported on first use.
        """
        if tdata_width % 8 or not 8 <= tdata_width <= MAX_TDATA_BITS:
            raise ValueError("tdata_width must be a multiple of 8 in 8..4096")
        for what, w, hi in (("tid_width", tid_width, 32),
                            ("tdest_width", tdest_width, 32),
                            ("tuser_width", tuser_width, MAX_TDATA_BITS)):
            if not 0 <= w <= hi:
                raise ValueError(f"{what} must be in 0..{hi}")
        try:
            self._status = getattr(shim, self._STATUS)
        except AttributeError:
            raise TypeError(
                f"{name!r}: shim handle has no '{self._STATUS}' signal -- pass "
                f"the {'axis_vip_dpi_mst' if self._ROLE == 0 else 'axis_vip_dpi_slv'}"
                " instance") from None

        self.name = name
        self.tdata_width = tdata_width
        self.nbytes = tdata_width // 8
        self.has_tkeep, self.has_tstrb = bool(has_tkeep), bool(has_tstrb)
        self.has_tlast = bool(has_tlast)
        self.tid_width, self.tdest_width = tid_width, tdest_width
        self.tuser_width = tuser_width
        self._keep_bytes = (self.nbytes + 7) // 8      # one bit per data byte
        self._user_bytes = (tuser_width + 7) // 8

        self._cfg = (self._ROLE, self.nbytes, int(self.has_tkeep),
                     int(self.has_tstrb), int(self.has_tlast),
                     tid_width, tdest_width, tuser_width)
        self._lib = load_lib(lib_path)
        self._inst = self._lib.axis_dpi_open(name.encode())
        if self._inst < 0:
            raise RuntimeError(f"axis_dpi_open({name!r}) failed (table full?)")
        self._lib.axis_dpi_py_config(self._inst, *self._cfg)
        self._verified = False

    def _verify(self):
        """Python description == SV shim parameters? Runs once, on first use
        (the SV shim may not have started yet when the constructor runs)."""
        if self._verified:
            return
        rc = self._lib.axis_dpi_cfg_check(self._inst)
        if rc == -1:
            raise RuntimeError(
                f"SV shim {self.name!r} has not registered with the DPI library. "
                "Either the simulation has not started the shim yet, the NAME "
                "parameter differs, or Python loaded a different copy of "
                "libaxis_dpi.so than the simulator did (compare AXIS_DPI_LIB "
                "with the -sv_lib path).")
        if rc > 0:
            sv = (c_int * 8)()
            self._lib.axis_dpi_sv_cfg_get(self._inst, sv)
            diffs = [f"{f}: python={self._cfg[i]} shim={sv[i]}"
                     for i, f in enumerate(_CFG_FIELDS) if rc & (1 << i)]
            raise RuntimeError(
                f"{self.name!r}: constructor does not match the SV shim -- "
                + "; ".join(diffs))
        self._verified = True

    async def _status_change(self):
        """Wait for the shim's status signal to change.

        Then leave its value-change callback before going on: on Questa a
        value-change wait re-armed from inside the callback is never woken.
        """
        await self._status.value_change
        await ReadWrite()

    def check(self):
        """Verify the constructor against the SV shim now (raises on mismatch)."""
        self._verify()

    def flush(self):
        """Drop transfers queued in the shim but not yet taken by the VIP
        (master) / not yet read by cocotb (slave)."""
        self._verify()
        rc = self._lib.axis_dpi_flush(self._inst)
        if rc != 0:
            raise RuntimeError(f"{self.name}: flush failed ({rc})")


def _check_int(what, value, bits):
    if not isinstance(value, int):
        raise TypeError(f"{what}: expected int, got {type(value).__name__}")
    if not 0 <= value < (1 << bits):
        raise ValueError(f"{what}={value:#x} does not fit in {bits} bit(s)")
    return int(value)


def _enc_int(what, value, bits, nbytes):
    return _check_int(what, value, bits).to_bytes(nbytes, "little")


def _vec_bytes(what, value, nbytes):
    if isinstance(value, (bytes, bytearray)):
        if len(value) != nbytes:
            raise ValueError(f"{what}: expected {nbytes} bytes, got {len(value)}")
        return bytes(value)
    return _enc_int(what, value, nbytes * 8, nbytes)


# -------------------------------------------------------------------- master
class AXIS_MASTER_VIP(_AxisVip):
    """Drives the Xilinx AXI4-Stream master VIP, one transfer per call."""
    _ROLE = _ROLE_MASTER
    _STATUS = "outstanding"

    def put_transaction(self, tdata, *, tkeep=None, tstrb=None, tlast=None,
                        tid=None, tdest=None, tuser=None, delay=0):
        """Queue ONE transfer in the shim. Returns immediately (no await, no
        sim time).

        tdata : int, or bytes of exactly tdata_width/8 (little endian)
        tlast : required if has_tlast; TypeError if the interface has no TLAST
        tkeep / tstrb : int bitmask, bit i = byte i. Only for interfaces that
                        have the signal. Omitted -> all bytes (all ones).
        tid / tdest / tuser : only for interfaces that have the signal.
                        Omitted -> 0.
        delay : idle cycles before this transfer's TVALID.

        A signal the interface does not have must not be passed (TypeError);
        values must fit the declared width (ValueError).
        """
        given = {"tkeep": tkeep, "tstrb": tstrb, "tlast": tlast, "tid": tid,
                 "tdest": tdest, "tuser": tuser}
        present = {"tkeep": self.has_tkeep, "tstrb": self.has_tstrb,
                   "tlast": self.has_tlast, "tid": self.tid_width > 0,
                   "tdest": self.tdest_width > 0, "tuser": self.tuser_width > 0}
        extra = [n for n, v in given.items() if v is not None and not present[n]]
        if extra:
            raise TypeError(
                f"{self.name}.put_transaction() got {extra} but the interface "
                f"was declared without {'/'.join(x.upper() for x in extra)}")
        if self.has_tlast and tlast is None:
            raise TypeError(f"{self.name}.put_transaction() missing 'tlast' "
                            "(required because has_tlast=True)")
        if not isinstance(delay, int) or delay < 0:
            raise ValueError("delay must be an int >= 0")

        kb = self._keep_bytes
        full = (1 << self.nbytes) - 1
        data_b = _vec_bytes("tdata", tdata, self.nbytes)
        keep_b = (_enc_int("tkeep", full if tkeep is None else tkeep,
                           self.nbytes, kb) if self.has_tkeep else b"")
        strb_b = (_enc_int("tstrb", full if tstrb is None else tstrb,
                           self.nbytes, kb) if self.has_tstrb else b"")
        user_b = (_enc_int("tuser", tuser or 0, self.tuser_width,
                           self._user_bytes) if self.tuser_width else b"")
        id_v = (_check_int("tid", tid or 0, self.tid_width)
                if self.tid_width else 0)
        dest_v = (_check_int("tdest", tdest or 0, self.tdest_width)
                  if self.tdest_width else 0)

        self._verify()
        rc = self._lib.axis_dpi_tx_push(
            self._inst, data_b, len(data_b), keep_b, len(keep_b),
            strb_b, len(strb_b), user_b, len(user_b),
            int(bool(tlast)), id_v, dest_v, delay)
        if rc != 0:
            raise RuntimeError(f"{self.name}: put_transaction failed ({rc})")

    @property
    def outstanding(self):
        """Transfers put but not yet fully sent by the VIP."""
        return int(self._status.value)

    async def wait_idle(self):
        """Wait until the VIP has put everything queued so far on the bus.
        Do not use while the sink is stalled."""
        self._verify()
        # `outstanding` is updated in the NBA region: let a put_transaction()
        # from this same time step show up first.
        await ReadOnly()
        if self.outstanding == 0:
            # Don't hand ReadOnly back to the caller: it could not drive
            # signals or call into the shims, nor wait_idle() again.
            await Timer(1, "step")
            return
        while self.outstanding > 0:
            await self._status_change()             # ends outside ReadOnly


# --------------------------------------------------------------------- slave
class AXIS_SLAVE_VIP(_AxisVip):
    """Receives through the Xilinx AXI4-Stream slave VIP; controls TREADY."""
    _ROLE = _ROLE_SLAVE
    _STATUS = "rx_count"

    # ------------- backpressure: plain calls, applied immediately
    def set_backpressure(self, mode="none", low=1, high=1):
        """Change TREADY behaviour while the simulation is running.

        mode : "none"   TREADY always high
               "osc"    low for `low` cycles, high for `high` cycles, repeat
               "random" low/high durations drawn from the (min, max) ranges
               "stall"  TREADY held low until the next set_backpressure()
        low, high : int, or (min, max) tuple (tuples are used by "random")
        """
        self._verify()
        if mode not in _BP_MODES:
            raise ValueError(f"mode must be one of {sorted(_BP_MODES)}")
        lo = (low, low) if isinstance(low, int) else tuple(low)
        hi = (high, high) if isinstance(high, int) else tuple(high)
        rc = self._lib.axis_dpi_bp_set(self._inst, _BP_MODES[mode],
                                       lo[0], lo[1], hi[0], hi[1])
        if rc == -2:
            raise ValueError(f"bad backpressure arguments (low={low}, high={high})")
        if rc != 0:
            raise RuntimeError(f"{self.name}: set_backpressure failed ({rc})")

    def stall(self):
        self.set_backpressure("stall")

    def release(self):
        self.set_backpressure("none")

    # ------------- receive: one Transaction per transfer, nothing merged
    @property
    def pending(self):
        """Transfers collected by the VIP and not yet read by cocotb."""
        return int(self._status.value)

    def try_get_transaction(self):
        """Return the oldest Transaction, or None if nothing has arrived."""
        self._verify()
        nb, kb, ub = self.nbytes, self._keep_bytes, self._user_bytes
        data = ctypes.create_string_buffer(nb)
        keep = ctypes.create_string_buffer(kb)
        strb = ctypes.create_string_buffer(kb)
        user = ctypes.create_string_buffer(max(ub, 1))
        last, tid, dest = c_int(), c_int(), c_int()
        rc = self._lib.axis_dpi_rx_pop(
            self._inst,
            data, nb,
            keep, kb if self.has_tkeep else 0,
            strb, kb if self.has_tstrb else 0,
            user, ub if self.tuser_width else 0,
            byref(last), byref(tid), byref(dest))
        if rc == 0:
            return None
        if rc < 0:
            raise RuntimeError(f"{self.name}: get_transaction failed ({rc})")
        le = lambda b, n: int.from_bytes(b.raw[:n], "little")   # noqa: E731
        return Transaction(
            tdata=le(data, nb),
            tkeep=le(keep, kb) if self.has_tkeep else None,
            tstrb=le(strb, kb) if self.has_tstrb else None,
            tlast=bool(last.value) if self.has_tlast else None,
            tid=tid.value if self.tid_width else None,
            tdest=dest.value if self.tdest_width else None,
            tuser=le(user, ub) if self.tuser_width else None,
        )

    async def get_transaction(self):
        """Wait for the next transfer and return it as a Transaction."""
        while True:
            t = self.try_get_transaction()
            if t is not None:
                return t
            await self._status_change()
