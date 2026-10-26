# Debug log

[← README](../README.md)

Set `AXIS_DPI_DEBUG=1` in the environment of the simulation to log every call
in all three layers. The runners pass the environment through, for example:

```bash
AXIS_DPI_DEBUG=1 TESTCASE=test_tdata pytest test/axi_vip_shims/axi_stream_vip_shims/python/test_axis_vip_verilator_runner.py
```

One transfer then reads, in the test's log:

```text
   245.00ns DEBUG    cocotb.axis_vip_dpi.mst0   put tdata=0x04030201 tlast=1 delay=0
axis_dpi: mst0: tx_push data=0x04030201 keep=0x0f strb=0x0f user=0x00 last=1 id=0 dest=0 delay=0
245 ns axis_vip_dpi_mst mst0: push from cocotb: data=0x04030201 ... (tx_q=101, outstanding=101)
1235 ns axis_vip_dpi_mst mst0: to VIP driver: data=0x04030201 ... (tx_q=0)
1255 ns axis_vip_dpi_slv slv0: from VIP monitor: data=0x04030201 ... (rx_q=1)
1255 ns axis_vip_dpi_slv slv0: pop from cocotb: data=0x04030201 ... (rx_q=0)
axis_dpi: slv0: rx_pop data=0x04030201 keep=0x0f strb=0x0f user=0x00 last=1 id=0 dest=0
  1255.00ns DEBUG    cocotb.axis_vip_dpi.slv0   get tdata=0x04030201 tkeep=0xf ...
```

| Prefix | Layer | Logs |
|--------|-------|------|
| `cocotb.axis_vip_dpi.<name>` | Python | constructor, config check, `put`, `get` (and empty polls), `flush`, `set_backpressure`, `wait_idle` |
| `axis_dpi:` | C library | registration from SV and cocotb, config check, every DPI call with its arguments, error codes |
| `axis_vip_dpi_mst` / `_slv` | SV shim | every export call, beats handed to the VIP driver, VIP driver idle, beats from the VIP monitor, backpressure applied |

The C library reads the variable once when it loads; the shims and the Python
classes ask it (`axis_dpi_debug()`), so set it before the simulation starts.
`COCOTB_LOG_LEVEL=DEBUG` alone turns on the Python lines only. With the
variable unset, each log point costs one flag test.
