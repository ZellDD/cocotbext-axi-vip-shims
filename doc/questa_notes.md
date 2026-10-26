# Questa notes

[← README](../README.md)

The runner sets three things that the shims need on Questa:

- `+define+XIL_DO_NOT_USE_ADV_RANDOMIZATION`: the VIP uses `$urandom` instead
  of `randomize()` / `randcase`. Those need a SystemVerilog verification
  licence, which the Starter edition doesn't have.
- `-dpioutoftheblue 1`: cocotb calls the shims' DPI exports from VPI callbacks,
  not from inside an SV import call chain.
- `-voptargs=+acc`: keeps the shims' status signals visible to cocotb.

The shims drive their status signals (`outstanding`, `rx_count`) with
nonblocking assignments, so cocotb never wakes up in the middle of a shim
process or of a DPI call. The Python side also leaves each status-signal
callback (through `ReadWrite`) before waiting again. On Questa, a value-change
wait re-armed from inside its own callback is never woken.
