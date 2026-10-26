# API summary

[← README](../README.md)

**Common**

| Call      | Effect |
|-----------|--------|
| `check()` | Verify the Python description against the SV shim now. |
| `flush()` | Drop transfers queued in the shim. Transfers already handed to the VIP are not affected. |

**`AXIS_MASTER_VIP`**

| Call / attribute | Effect |
|------------------|--------|
| `put_transaction(tdata, *, tkeep, tstrb, tlast, tid, tdest, tuser, delay=0)` | Queue one transfer. Returns immediately and consumes no sim time. `tdata` is an int or exactly `tdata_width/8` little-endian bytes. `tlast` is required when the interface has TLAST. Passing a signal the interface lacks raises `TypeError`. A value too wide for its signal raises `ValueError`. |
| `outstanding` | Transfers queued but not yet on the bus. |
| `await wait_idle()` | Wait until everything queued so far has been sent. Don't call it while the sink is stalled. |

**`AXIS_SLAVE_VIP`**

| Call / attribute | Effect |
|------------------|--------|
| `set_backpressure(mode, low, high)` | Takes effect immediately. `mode` is one of `"none"`, `"osc"`, `"random"` (with `(min, max)` ranges) or `"stall"`. |
| `stall()` / `release()` | Shorthand for `"stall"` / `"none"`. |
| `pending` | Beats received and not yet read. |
| `try_get_transaction()` | Return the oldest `Transaction`, or `None` if nothing has arrived. |
| `await get_transaction()` | Wait for the next `Transaction`. |

Signals that the interface doesn't have come back as `None`. There is no
built-in timeout; wrap waits in `cocotb.triggers.with_timeout()`.

The VIP drives noise on null bytes (TKEEP=0) and position bytes (TSTRB=0), as
AXI4-Stream allows. Compare only data bytes (TKEEP=1 and TSTRB=1). If you pass
`tkeep` without `tstrb`, TSTRB defaults to all ones, which is not allowed where
TKEEP=0. Pass both.
